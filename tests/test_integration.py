"""High-level integration tests for Forseti API endpoints."""

from datetime import datetime

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session
from sqlmodel import select

from agents.config import AGENTIC_PIPELINE_MODE, LINEAR_PIPELINE_MODE, AgentWorkflowConfig, load_agent_config
from agents.orchestration.workflow import AgenticAnalysisWorkflow
from app.db.models import PriceBar, Recommendation, Security
from app.main import app
from app.main import get_analysis_engine
from app.main import get_pipeline_override_allowed
from app.schemas.analyze import AnalysisTrace, AnalyzeResponse
from app.settings import Settings
from tests.fixtures.golden.loader import load_golden_case


@pytest.fixture
def client():
    """Provide a test client for the FastAPI application."""
    return TestClient(app)


@pytest.fixture
def db_client(db_engine):
    app.dependency_overrides[get_analysis_engine] = lambda: db_engine
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestRootEndpoint:
    """Integration tests for the root endpoint."""

    def test_read_root_returns_welcome_message(self, client):
        """Test that GET / returns a welcome message with 200 status."""
        response = client.get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "Welcome to Forseti API!"}

    def test_read_root_response_content_type(self, client):
        """Test that GET / returns JSON content type."""
        response = client.get("/")
        assert response.headers["content-type"] == "application/json"

    def test_read_root_has_message_key(self, client):
        """Test that GET / response contains the 'message' key."""
        response = client.get("/")
        data = response.json()
        assert "message" in data
        assert isinstance(data["message"], str)
        assert len(data["message"]) > 0


class TestAnalyzeEndpoint:
    def test_post_analyze_happy_path_persists_recommendation(self, db_client, db_engine):
        security = Security(
            ticker="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
            sector_tag="ai",
        )
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add_all(
                [
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-01",
                        open="100.0000",
                        high="103.0000",
                        low="99.0000",
                        close="100.0000",
                        volume=1_000_000,
                    ),
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-02",
                        open="101.0000",
                        high="104.0000",
                        low="100.0000",
                        close="102.5000",
                        volume=1_100_000,
                    ),
                ]
            )
            session.commit()

        response = db_client.post(
            "/analyze",
            json={
                "ticker": " nvda ",
                "account_size_eur": 10000,
                "risk_percentage": 0.01,
                "max_position_size_eur": 500,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "NVDA"
        assert body["decision"] in ("trade", "watchlist", "no_trade")
        assert body["engine_version"] == "v1.rules.0"
        assert body["trace_id"]
        assert "confidence" in body
        assert "reasons" in body
        assert "warnings" in body

    def test_post_analyze_returns_422_for_empty_ticker(self, db_client):
        response = db_client.post("/analyze", json={"ticker": "   "})
        assert response.status_code == 422

    def test_post_analyze_returns_422_for_url_like_ticker(self, db_client):
        response = db_client.post("/analyze", json={"ticker": "https://broker.example/NVDA"})
        assert response.status_code == 422

    def test_post_analyze_response_shape_for_watchlist(self, db_client, db_engine):
        security = Security(
            ticker="AMD",
            name="Advanced Micro Devices",
            exchange="NASDAQ",
            sector_tag="ai",
        )
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add(
                PriceBar(
                    security_id=security.id,
                    bar_date="2026-02-01",
                    open="95.0000",
                    high="96.0000",
                    low="94.0000",
                    close="95.2000",
                    volume=900_000,
                )
            )
            session.commit()

        response = db_client.post("/analyze", json={"ticker": "AMD"})
        assert response.status_code == 200
        body = response.json()
        for field in (
            "ticker",
            "decision",
            "time_stop_at",
            "entry_range",
            "stop_loss",
            "take_profit",
            "risk_reward",
            "position_size_eur",
            "confidence",
            "reasons",
            "warnings",
            "engine_version",
            "created_at",
            "trace_id",
        ):
            assert field in body
        assert body["decision"] == "watchlist"
        assert body["time_stop_at"] is None
        assert body["warnings"] == ["insufficient_price_data"]


class TestGoldenCaseEndToEnd:
    def test_post_analyze_clear_trade_matches_expected_and_persists_recommendation(self, db_client, db_engine):
        case = load_golden_case("clear_trade")
        with Session(db_engine) as session:
            case.seed(session)

        response = db_client.post(
            "/analyze",
            json={"ticker": case.ticker, "as_of_date": case.today.isoformat()},
        )

        assert response.status_code == 200
        body = response.json()
        case.assert_matches(AnalyzeResponse(**body))

        with Session(db_engine) as session:
            recommendation = session.exec(select(Recommendation)).one()

        assert recommendation.decision == case.expected["decision"]
        assert recommendation.engine_version == case.expected["engine_version"]


class TestPipelineOverride:
    @staticmethod
    def _config(mode: str) -> AgentWorkflowConfig:
        return AgentWorkflowConfig(
            pipeline_mode=mode,
            model_name="test-model",
            temperature=0.2,
            timeout_seconds=30.0,
            max_retries=1,
        )

    @staticmethod
    def _seed_security(db_engine):
        security = Security(
            ticker="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
            sector_tag="ai",
        )
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add_all(
                [
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-01",
                        open="100.0000",
                        high="103.0000",
                        low="99.0000",
                        close="100.0000",
                        volume=1_000_000,
                    ),
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-02",
                        open="101.0000",
                        high="104.0000",
                        low="100.0000",
                        close="102.5000",
                        volume=1_100_000,
                    ),
                ]
            )
            session.commit()

    @staticmethod
    def _response(*, warnings=None, trace=None) -> AnalyzeResponse:
        return AnalyzeResponse(
            ticker="NVDA",
            decision="trade",
            confidence=0.8,
            reasons=["ok"],
            warnings=warnings or [],
            engine_version="v1.rules.0",
            trace_id="trace-123",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            entry_range=(10.0, 11.0),
            stop_loss=9.0,
            take_profit=(12.0, 13.0),
            risk_reward=2.0,
            position_size_eur=100.0,
            trace=trace,
        )

    def test_post_analyze_refuses_pipeline_override_when_disabled(self, db_client, monkeypatch):
        def unexpected_agentic_call(self, request):
            pytest.fail("Agentic pipeline should not run when override is disabled.")

        app.dependency_overrides[get_pipeline_override_allowed] = lambda: False
        monkeypatch.setattr("app.services.pipeline.AgenticPipeline.analyze", unexpected_agentic_call)

        response = db_client.post("/analyze?pipeline=agentic", json={"ticker": "NVDA"})

        assert response.status_code == 403
        assert response.json() == {"detail": "pipeline_override_disabled"}

    def test_post_analyze_runs_agentic_pipeline_when_override_is_enabled(self, db_client, monkeypatch):
        trace = AnalysisTrace(run_id="run-123", ticker="NVDA", entered_agent_layer=True)

        app.dependency_overrides[get_pipeline_override_allowed] = lambda: True
        monkeypatch.setattr(
            "app.services.pipeline.AgenticPipeline.analyze",
            lambda _pipeline, request: self._response(trace=trace),
        )

        response = db_client.post("/analyze?pipeline=agentic&include_trace=true", json={"ticker": "NVDA"})

        assert response.status_code == 200
        body = response.json()
        assert body["warnings"] == ["pipeline_override:agentic"]
        assert body["trace"]["run_id"] == "run-123"
        assert body["trace"]["entered_agent_layer"] is True

    def test_post_analyze_linear_override_skips_agentic_pipeline(self, db_client, db_engine, monkeypatch):
        def unexpected_agentic_call(self, request):
            pytest.fail("Agentic pipeline should not run when pipeline=linear is requested.")

        self._seed_security(db_engine)
        app.dependency_overrides[get_pipeline_override_allowed] = lambda: True
        monkeypatch.setattr(
            "agents.config.load_agent_config",
            lambda: self._config(AGENTIC_PIPELINE_MODE),
        )
        monkeypatch.setattr("app.services.pipeline.AgenticPipeline.analyze", unexpected_agentic_call)

        response = db_client.post("/analyze?pipeline=linear", json={"ticker": "NVDA"})

        assert response.status_code == 200
        assert "pipeline_override:linear" in response.json()["warnings"]

    def test_post_analyze_rejects_unknown_pipeline_value(self, db_client):
        response = db_client.post("/analyze?pipeline=nonsense", json={"ticker": "NVDA"})

        assert response.status_code == 422

    def test_post_analyze_without_pipeline_keeps_default_response(self, db_client, monkeypatch):
        def unexpected_agentic_call(self, request):
            pytest.fail("Agentic pipeline should not run on the default linear path.")

        monkeypatch.setattr(
            "agents.config.load_agent_config",
            lambda: self._config(LINEAR_PIPELINE_MODE),
        )
        monkeypatch.setattr(
            "app.services.pipeline.LinearPipeline.analyze",
            lambda _pipeline, request: self._response(),
        )
        monkeypatch.setattr("app.services.pipeline.AgenticPipeline.analyze", unexpected_agentic_call)

        response = db_client.post("/analyze", json={"ticker": "NVDA"})

        assert response.status_code == 200
        assert response.json() == {
            "ticker": "NVDA",
            "decision": "trade",
            "time_stop_at": None,
            "entry_range": [10.0, 11.0],
            "stop_loss": 9.0,
            "take_profit": [12.0, 13.0],
            "risk_reward": 2.0,
            "position_size_eur": 100.0,
            "confidence": 0.8,
            "reasons": ["ok"],
            "warnings": [],
            "engine_version": "v1.rules.0",
            "created_at": "2026-01-01T12:00:00",
            "trace_id": "trace-123",
            "evidence": None,
            "trace": None,
            "diagnosis": None,
            "fundamental_agent_effect": None,
        }

    def test_post_analyze_without_include_trace_strips_agentic_trace(self, db_client, monkeypatch):
        trace = AnalysisTrace(run_id="run-123", ticker="NVDA", entered_agent_layer=True)

        app.dependency_overrides[get_pipeline_override_allowed] = lambda: True
        monkeypatch.setattr(
            "app.services.pipeline.AgenticPipeline.analyze",
            lambda _pipeline, request: self._response(trace=trace),
        )

        response = db_client.post("/analyze?pipeline=agentic", json={"ticker": "NVDA"})

        assert response.status_code == 200
        body = response.json()
        assert body["warnings"] == ["pipeline_override:agentic"]
        assert body["trace"] is None


class TestRunTraceEndpoint:
    @staticmethod
    def _seed_security(db_engine):
        security = Security(
            ticker="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
            sector_tag="ai",
        )
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            session.add_all(
                [
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-01",
                        open="100.0000",
                        high="103.0000",
                        low="99.0000",
                        close="100.0000",
                        volume=1_000_000,
                    ),
                    PriceBar(
                        security_id=security.id,
                        bar_date="2026-01-02",
                        open="101.0000",
                        high="104.0000",
                        low="100.0000",
                        close="102.5000",
                        volume=1_100_000,
                    ),
                ]
            )
            session.commit()

    def test_get_run_returns_persisted_observed_trace(self, db_client, db_engine):
        from types import SimpleNamespace

        self._seed_security(db_engine)
        events = [
            SimpleNamespace(author="trade_analyst_supervisor", content=SimpleNamespace(parts=[]), usage_metadata=None),
            SimpleNamespace(author="fundamental_analyst", content=SimpleNamespace(parts=[]), usage_metadata=None),
        ]
        workflow = AgenticAnalysisWorkflow(
            load_agent_config(Settings(_env_file=None)),
            engine=db_engine,
            runner_factory=lambda registry, ticker: iter(events),
        )
        response = workflow.analyze("NVDA")

        run_response = db_client.get(f"/runs/{response.trace.run_id}")
        assert run_response.status_code == 200
        body = run_response.json()
        assert body["run_id"] == response.trace.run_id
        assert body["entered_agent_layer"] is True
        assert body["adk_event_count"] == 2
        assert body["observed_agents"] == ["trade_analyst_supervisor", "fundamental_analyst"]
        assert [(step["agent_name"], step["status"]) for step in body["steps"]] == [
            (step.agent_name, step.status) for step in response.trace.steps
        ]

    def test_get_run_returns_404_for_unknown_run_id(self, db_client):
        response = db_client.get("/runs/does-not-exist")
        assert response.status_code == 404
        assert response.json()["detail"] == "agent_run_not_found: does-not-exist"

    def test_post_analyze_without_include_trace_returns_null_trace(self, db_client, db_engine):
        self._seed_security(db_engine)
        response = db_client.post("/analyze", json={"ticker": "NVDA"})
        assert response.status_code == 200
        body = response.json()
        assert body["trace"] is None


class TestScreeningEndpoint:
    def test_get_screening_returns_summary_and_priority_order(self, db_client, db_engine, monkeypatch):
        from app.db.models import Security
        from app.schemas.analyze import AnalyzeResponse

        with Session(db_engine) as session:
            session.add_all(
                [
                    Security(ticker="MSFT", name="Microsoft", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AMD", name="AMD", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AAPL", name="Apple", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="QQQ", name="Nasdaq", exchange="NASDAQ", sector_tag="ai", is_active=False),
                ]
            )
            session.commit()

        def fake_analyze(symbol, engine=None, today=None):
            mapping = {
                "MSFT": AnalyzeResponse(
                    ticker="MSFT",
                    decision="trade",
                    confidence=0.9,
                    reasons=["screened trade"],
                    warnings=[],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
                "AMD": AnalyzeResponse(
                    ticker="AMD",
                    decision="watchlist",
                    confidence=0.5,
                    reasons=["screened watchlist"],
                    warnings=["insufficient_price_data"],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
                "AAPL": AnalyzeResponse(
                    ticker="AAPL",
                    decision="no_trade",
                    confidence=0.2,
                    reasons=["screened no_trade"],
                    warnings=["no_price_data"],
                    engine_version="v1.rules.0",
                    trace_id="",
                ),
            }
            return mapping[symbol]

        monkeypatch.setattr("app.services.analyzer.analyze", fake_analyze)

        response = db_client.get("/screening")

        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) >= {"summary", "items"}
        assert body["summary"] == {"total": 3, "trade": 1, "watchlist": 1, "no_trade": 1, "errors": 0}
        assert [item["ticker"] for item in body["items"]] == ["MSFT", "AMD", "AAPL"]
        assert [item["decision"] for item in body["items"]] == ["trade", "watchlist", "no_trade"]


class TestScreeningDiagnostics:
    @staticmethod
    def _seed_screening_securities(db_engine):
        with Session(db_engine) as session:
            session.add_all(
                [
                    Security(ticker="MSFT", name="Microsoft", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AMD", name="AMD", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="AAPL", name="Apple", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="ERR1", name="Error One", exchange="NASDAQ", sector_tag="ai"),
                    Security(ticker="QQQ", name="Nasdaq", exchange="NASDAQ", sector_tag="ai", is_active=False),
                ]
            )
            session.commit()

    @staticmethod
    def _diagnosis(stage, rule_id, detail, checklist_score=None):
        from app.schemas.analyze import DecisionDiagnosis

        return DecisionDiagnosis(
            stage=stage,
            rule_id=rule_id,
            detail=detail,
            checklist_score=checklist_score,
            debug_reason=f"{stage}/{rule_id}: {detail}",
        )

    def test_get_screening_verbose_false_keeps_diagnosis_null_and_populates_debug_reason(
        self,
        db_client,
        db_engine,
        monkeypatch,
    ):
        from app.schemas.analyze import AnalyzeResponse

        self._seed_screening_securities(db_engine)

        def fake_analyze(symbol, engine=None, today=None):
            mapping = {
                "MSFT": AnalyzeResponse(
                    ticker="MSFT",
                    decision="trade",
                    confidence=0.9,
                    reasons=["screened trade"],
                    warnings=[],
                    engine_version="v1.rules.0",
                    trace_id="",
                    diagnosis=self._diagnosis("checklist", "checklist_passed", "score 11/11, missing: none", 11),
                ),
                "AMD": AnalyzeResponse(
                    ticker="AMD",
                    decision="watchlist",
                    confidence=0.5,
                    reasons=["screened watchlist"],
                    warnings=["insufficient_price_data"],
                    engine_version="v1.rules.0",
                    trace_id="",
                    diagnosis=self._diagnosis(
                        "data_gate",
                        "insufficient_price_data",
                        "fewer than 200 price bars available",
                    ),
                ),
                "AAPL": AnalyzeResponse(
                    ticker="AAPL",
                    decision="no_trade",
                    confidence=0.0,
                    reasons=["screened no_trade"],
                    warnings=[],
                    engine_version="v1.rules.0",
                    trace_id="",
                    diagnosis=self._diagnosis(
                        "checklist",
                        "score_below_watchlist",
                        "score 0/11, missing: none",
                        0,
                    ),
                ),
            }
            if symbol == "ERR1":
                raise RuntimeError("broken\nsecond line")
            return mapping[symbol]

        monkeypatch.setattr("app.services.analyzer.analyze", fake_analyze)

        response = db_client.get("/screening")

        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == {"total": 4, "trade": 1, "watchlist": 1, "no_trade": 1, "errors": 1}
        assert [item["ticker"] for item in body["items"]] == ["MSFT", "AMD", "AAPL", "ERR1"]
        assert [item["decision"] for item in body["items"]] == ["trade", "watchlist", "no_trade", None]
        ok_items = [item for item in body["items"] if item["status"] == "ok"]
        assert all(item["debug_reason"] for item in ok_items)
        assert all(item["diagnosis"] is None for item in ok_items)
        assert [item["checklist_score"] for item in ok_items] == [11, None, 0]
        error_item = body["items"][-1]
        assert error_item["status"] == "error"
        assert error_item["debug_reason"] == "error/RuntimeError: broken"

    def test_get_screening_verbose_true_includes_diagnosis(self, db_client, db_engine, monkeypatch):
        from app.schemas.analyze import AnalyzeResponse

        self._seed_screening_securities(db_engine)

        def fake_analyze(symbol, engine=None, today=None):
            if symbol == "ERR1":
                raise RuntimeError("broken")
            return AnalyzeResponse(
                ticker=symbol,
                decision="no_trade" if symbol == "AAPL" else "watchlist",
                confidence=0.0 if symbol == "AAPL" else 0.5,
                reasons=["screened"],
                warnings=[],
                engine_version="v1.rules.0",
                trace_id="",
                diagnosis=self._diagnosis(
                    "checklist",
                    "score_below_watchlist",
                    "score 0/11, missing: none",
                    0,
                ),
            )

        monkeypatch.setattr("app.services.analyzer.analyze", fake_analyze)

        response = db_client.get("/screening?verbose=true")

        assert response.status_code == 200
        body = response.json()
        ok_items = [item for item in body["items"] if item["status"] == "ok"]
        assert all(item["diagnosis"] is not None for item in ok_items)
        assert ok_items[0]["diagnosis"]["stage"] == "checklist"
        assert ok_items[0]["diagnosis"]["debug_reason"] == "checklist/score_below_watchlist: score 0/11, missing: none"
        assert body["items"][-1]["diagnosis"] is None


class TestUniverseDiagnosticsEndpoint:
    def test_get_universe_diagnostics_empty_universe_returns_zeroed_report(self, db_client):
        response = db_client.get("/diagnostics/universe")

        assert response.status_code == 200
        body = response.json()
        assert body["universe_size"] == 0
        assert body["items"] == []
        assert body["coverage"] == {
            "tickers_with_price_bars": 0,
            "tickers_with_200_bars": 0,
            "tickers_with_technical_features": 0,
            "tickers_with_fundamentals": 0,
            "tickers_with_earnings_events": 0,
            "tickers_with_document_chunks": 0,
            "latest_macro_daily_date": None,
        }
        assert body["blocked_by"] == {
            "data_gate": {},
            "hard_veto": {},
            "checklist": {},
            "risk_math": {},
            "passed": 0,
        }

    def test_get_universe_diagnostics_seeded_mix_is_read_only_and_reconciles(self, db_client, db_engine):
        from datetime import date, datetime, timezone
        from decimal import Decimal

        from sqlalchemy import func
        from sqlmodel import select

        from app.db.models import DocumentChunk, MacroDaily, Recommendation, SourceQualityTier, SourceType

        today = date(2026, 1, 10)
        with Session(db_engine) as session:
            bare = Security(ticker="BARE1", name="Bare One", exchange="NYSE", sector_tag="ai")
            one_bar = Security(ticker="ONEB1", name="One Bar", exchange="NYSE", sector_tag="ai")
            inactive = Security(ticker="DEAD1", name="Dead One", exchange="NYSE", sector_tag="ai", is_active=False)
            session.add_all([bare, one_bar, inactive])
            session.commit()
            session.refresh(bare)
            session.refresh(one_bar)
            session.add(
                PriceBar(
                    security_id=one_bar.id,
                    bar_date=today,
                    open=Decimal("10"),
                    high=Decimal("11"),
                    low=Decimal("9"),
                    close=Decimal("10"),
                    volume=100,
                )
            )
            session.add(
                DocumentChunk(
                    ticker="ONEB1",
                    source_type=SourceType.company_news,
                    document_id="news:oneb1",
                    source_url="https://example.invalid/oneb1",
                    publisher="Example Publisher",
                    title="ONEB1 company news",
                    source_quality_tier=SourceQualityTier.secondary_reputable,
                    source_hash="oneb1-hash",
                    published_at=datetime(2026, 1, 9, tzinfo=timezone.utc),
                    chunk_index=0,
                    text="ONEB1 coverage note",
                )
            )
            session.add(MacroDaily(obs_date=today, vix=Decimal("19.0")))
            session.commit()

        with Session(db_engine) as session:
            before_count = session.exec(select(func.count()).select_from(Recommendation)).one()

        response = db_client.get("/diagnostics/universe")

        with Session(db_engine) as session:
            after_count = session.exec(select(func.count()).select_from(Recommendation)).one()

        assert response.status_code == 200
        assert before_count == after_count == 0
        body = response.json()
        assert body["universe_size"] == 2
        assert [item["ticker"] for item in body["items"]] == ["BARE1", "ONEB1"]
        assert "DEAD1" not in [item["ticker"] for item in body["items"]]
        assert body["coverage"] == {
            "tickers_with_price_bars": 1,
            "tickers_with_200_bars": 0,
            "tickers_with_technical_features": 0,
            "tickers_with_fundamentals": 0,
            "tickers_with_earnings_events": 0,
            "tickers_with_document_chunks": 1,
            "latest_macro_daily_date": "2026-01-10",
        }
        blocked_by = body["blocked_by"]
        total = sum(sum(value.values()) if isinstance(value, dict) else value for value in blocked_by.values())
        assert total == body["universe_size"]
        assert blocked_by["data_gate"] == {"no_price_data": 1, "insufficient_price_data": 1}
        assert blocked_by["hard_veto"] == {}
        assert blocked_by["checklist"] == {}
        assert blocked_by["risk_math"] == {}
        assert blocked_by["passed"] == 0
        assert body["items"][0]["debug_reason"] == "data_gate/no_price_data: no price data available"
        assert body["items"][1]["debug_reason"] == (
            "data_gate/insufficient_price_data: fewer than 200 price bars available"
        )


class TestTickerEndpoint:
    def test_get_ticker_happy_path_full_data(self, db_client, db_engine):
        from decimal import Decimal
        from datetime import date, timedelta
        from app.db.models import EarningsEvent, Fundamental, TechnicalFeature

        security = Security(ticker="NVDA", name="NVIDIA Corporation", exchange="NASDAQ", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            today = date.today()
            bar1_date = today - timedelta(days=1)
            bar2_date = today
            session.add_all(
                [
                    PriceBar(
                        security_id=security.id, bar_date=bar2_date, open=Decimal("101.0"),
                        high=Decimal("104.0"), low=Decimal("100.0"), close=Decimal("102.5"),
                        volume=1_100_000,
                    ),
                    PriceBar(
                        security_id=security.id, bar_date=bar1_date, open=Decimal("100.0"),
                        high=Decimal("103.0"), low=Decimal("99.0"), close=Decimal("101.0"),
                        volume=1_000_000,
                    ),
                    TechnicalFeature(
                        security_id=security.id, as_of_date=bar2_date, rsi_14=Decimal("58.1234"),
                        sma_50=Decimal("99.5"), sma_200=Decimal("88.0"),
                        volume_trend=Decimal("1.05"),
                    ),
                    Fundamental(
                        security_id=security.id, as_of_date=date(2025, 12, 31),
                        revenue_growth=Decimal("0.62"), fcf=Decimal("21000000000.0"),
                        debt_to_equity=Decimal("0.41"), eps_trend=Decimal("0.18"),
                        margins=Decimal("0.55"), raw_payload={},
                    ),
                    EarningsEvent(
                        security_id=security.id,
                        report_date=today + timedelta(days=30),
                        confirmed=False,
                    ),
                ]
            )
            session.commit()

        response = db_client.get("/ticker/NVDA")
        assert response.status_code == 200
        body = response.json()
        assert body["ticker"] == "NVDA"
        assert body["name"] == "NVIDIA Corporation"
        assert body["exchange"] == "NASDAQ"
        assert body["sector_tag"] == "ai"
        assert body["is_active"] is True
        assert body["price_bars_stored"] == 2
        assert body["latest_price_bar"]["close"] == 102.5
        assert body["latest_price_bar"]["volume"] == 1_100_000
        assert body["latest_technical_features"]["rsi_14"] == pytest.approx(58.1234, rel=1e-4)
        assert body["latest_fundamentals"]["revenue_growth"] == pytest.approx(0.62, rel=1e-4)
        assert body["next_earnings_date"] == (today + timedelta(days=30)).isoformat()
        assert body["warnings"] == []
        assert body["data_freshness"]["is_price_data_stale"] is False

    def test_get_ticker_normalizes_symbol(self, db_client, db_engine):
        security = Security(ticker="NVDX", name="NVDX Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/%20nvdx%20")
        assert response.status_code == 200
        assert response.json()["ticker"] == "NVDX"

    def test_get_ticker_without_market_data(self, db_client, db_engine):
        security = Security(ticker="BARE1", name="Bare One", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/BARE1")
        assert response.status_code == 200
        body = response.json()
        assert body["latest_price_bar"] is None
        assert body["price_bars_stored"] == 0
        assert body["data_freshness"]["is_price_data_stale"] is True
        assert "no_price_data" in body["warnings"]
        assert "no_technical_features" in body["warnings"]
        assert "no_fundamentals" in body["warnings"]
        assert "no_earnings_data" in body["warnings"]

    def test_get_ticker_with_stale_price_data(self, db_client, db_engine):
        from decimal import Decimal
        from datetime import date, timedelta

        security = Security(ticker="STALE", name="Stale Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()
            session.refresh(security)
            stale_date = date.today() - timedelta(days=30)
            session.add(
                PriceBar(
                    security_id=security.id, bar_date=stale_date, open=Decimal("10"),
                    high=Decimal("11"), low=Decimal("9"), close=Decimal("10"), volume=100,
                )
            )
            session.commit()

        response = db_client.get("/ticker/STALE")
        assert response.status_code == 200
        body = response.json()
        assert "stale_price_data" in body["warnings"]
        assert body["data_freshness"]["price_data_age_days"] == 30

    def test_get_ticker_unknown_returns_404(self, db_client):
        response = db_client.get("/ticker/MSFT")
        assert response.status_code == 404
        assert response.json()["detail"] == "ticker_not_found: MSFT"

    def test_get_ticker_invalid_symbol_returns_422(self, db_client):
        for symbol in [
            "%20",
            "TOOLONGTICKER",
            "NV%24DA",
        ]:
            response = db_client.get(f"/ticker/{symbol}")
            assert response.status_code == 422, f"Expected 422 for {symbol}"

    def test_get_ticker_inactive_security_warns(self, db_client, db_engine):
        security = Security(ticker="DEAD1", name="Dead Corp", exchange="NYSE", sector_tag="ai", is_active=False)
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/DEAD1")
        assert response.status_code == 200
        body = response.json()
        assert "security_inactive" in body["warnings"]
        assert body["is_active"] is False

    def test_get_ticker_response_contains_no_analysis_fields(self, db_client, db_engine):
        security = Security(ticker="CHK1X", name="Check Corp", exchange="NYSE", sector_tag="ai")
        with Session(db_engine) as session:
            session.add(security)
            session.commit()

        response = db_client.get("/ticker/CHK1X")
        assert response.status_code == 200
        body = response.json()
        for absent_field in ("engine_version", "trace_id", "decision"):
            assert absent_field not in body
