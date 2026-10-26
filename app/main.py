import logging
import os
from contextlib import asynccontextmanager
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.engine import make_url

from agents.orchestration.workflow import GoogleWorkflowError
from app.db.session import dispose_engines, get_engine
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, EvidenceBlock
from app.schemas.screening import ScreeningResponse
from app.schemas.diagnostics import UniverseDiagnosticsResponse
from app.schemas.ticker import TickerProfileResponse
from app.services.analyzer import validate_and_normalize_ticker
from app.services.pipeline import (
    PipelineOverrideNotAllowedError,
    log_pipeline_override,
    override_warning,
    select_pipeline,
)
from app.services.screening import run_screening
from app.services.universe_diagnostics import build_universe_diagnostics
from app.services.ticker_profile import build_ticker_profile
from app.settings import get_settings, validate_runtime_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        validate_runtime_settings(get_settings())
        yield
    finally:
        dispose_engines()


app = FastAPI(title="Forseti API", lifespan=lifespan)


def _parse_database_url(url: str) -> Optional[dict]:
    if not url:
        return None
    parsed = make_url(url)
    return {
        "scheme": parsed.drivername,
        "username": parsed.username,
        "password": parsed.password,
        "host": parsed.query.get("host") or parsed.host,
        "port": parsed.port,
        "path": parsed.database,
    }


def _check_database_connectivity(db_url: str) -> dict:
    connection_info = {}
    try:
        connection_info = _parse_database_url(db_url) or {}
        with get_engine(db_url).connect() as connection:
            connection.execute(text("SELECT 1"))
        status, detail = "ok", "ok"
    except Exception as exc:
        logger.exception("Database health check failed")
        status, detail = "unreachable", str(exc)
    return {"status": status, "detail": detail,
            "host": connection_info.get("host"), "port": connection_info.get("port")}


@app.get("/")
def read_root():
    return {"message": "Welcome to Forseti API!"}


@app.get("/healthz")
def healthz():
    """Health endpoint that checks application and DB connectivity."""
    db_url = os.getenv("DATABASE_URL") or get_settings().DATABASE_URL
    database = _check_database_connectivity(db_url)
    if get_settings().APP_ENV == "cloud":
        return {"app": "ok", "db": database["status"]}
    return {"app": "ok", "db": database}


@app.get("/livez")
def livez():
    """Liveness and startup probe independent of the database."""
    return {"app": "ok"}


def get_analysis_engine():
    return get_engine()


def get_pipeline_override_allowed() -> bool:
    return get_settings().ALLOW_PIPELINE_OVERRIDE


def validated_symbol(symbol: str = Path(...)) -> str:
    try:
        return validate_and_normalize_ticker(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    request: AnalyzeRequest,
    include_trace: bool = Query(default=False),
    pipeline: Optional[Literal["linear", "agentic"]] = Query(default=None),
    engine=Depends(get_analysis_engine),
    override_allowed: bool = Depends(get_pipeline_override_allowed),
):
    try:
        from agents.config import load_agent_config

        selected_pipeline = select_pipeline(
            pipeline,
            load_agent_config(),
            override_allowed=override_allowed,
            engine=engine,
        )
        response = selected_pipeline.analyze(request)
        warning = override_warning(pipeline)
        if warning is not None:
            response.warnings.append(warning)
            log_pipeline_override(request.ticker, pipeline)
        if not include_trace:
            response.trace = None
        return response
    except PipelineOverrideNotAllowedError as exc:
        raise HTTPException(status_code=403, detail="pipeline_override_disabled") from exc
    except GoogleWorkflowError as exc:
        raise HTTPException(status_code=502, detail=f"google_workflow_failed: {exc}") from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/screening", response_model=ScreeningResponse)
def read_screening(verbose: bool = Query(default=False), engine=Depends(get_analysis_engine)):
    return run_screening(engine=engine, verbose=verbose)


@app.get("/diagnostics/universe", response_model=UniverseDiagnosticsResponse)
def read_universe_diagnostics(engine=Depends(get_analysis_engine)):
    return build_universe_diagnostics(engine=engine)


@app.get("/runs/{run_id}")
def read_run(run_id: str, engine=Depends(get_analysis_engine)):
    from agents.orchestration.workflow import load_trace

    trace = load_trace(run_id, engine=engine)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"agent_run_not_found: {run_id}")
    return trace


@app.get("/ticker/{symbol}/evidence", response_model=EvidenceBlock)
def ticker_evidence(symbol: str = Depends(validated_symbol), engine=Depends(get_analysis_engine)):
    """Return evidence-backed analysis for *symbol* from the RAG layer."""
    from app.rag.evidence import build_evidence
    from app.rag.synthesis import SynthesisOutput

    output: SynthesisOutput = build_evidence(ticker=symbol, engine=engine)
    return EvidenceBlock(
        bullish_drivers=[{"claim": item.claim, "chunk_ids": item.chunk_ids} for item in output.bullish_drivers],
        bearish_risks=[{"claim": item.claim, "chunk_ids": item.chunk_ids} for item in output.bearish_risks],
        catalysts=[{"claim": item.claim, "chunk_ids": item.chunk_ids} for item in output.catalysts],
        news_alignment=output.news_alignment,
        red_flags=[{"claim": item.claim, "chunk_ids": item.chunk_ids} for item in output.red_flags],
        chunk_count=output.chunk_count,
        status=output.status,
    )


@app.get("/ticker/{symbol}", response_model=TickerProfileResponse)
def read_ticker_profile(symbol: str = Depends(validated_symbol), engine=Depends(get_analysis_engine)):
    profile = build_ticker_profile(symbol, engine=engine)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"ticker_not_found: {symbol}")
    return profile
