import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Path, Query

from agents.orchestration.workflow import GoogleWorkflowError
from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse, EvidenceBlock
from app.schemas.ticker import TickerProfileResponse
from app.services.analyzer import analyze_request, validate_and_normalize_ticker
from app.services.ticker_profile import build_ticker_profile
from app.settings import get_settings

app = FastAPI(title="Forseti API")


def _parse_database_url(url: str) -> Optional[dict]:
    if not url:
        return None
    parsed = urlparse(url)
    return {
        "scheme": parsed.scheme,
        "username": parsed.username,
        "password": parsed.password,
        "host": parsed.hostname,
        "port": parsed.port,
        "path": parsed.path[1:] if parsed.path.startswith("/") else parsed.path,
    }


def _tcp_check(host: Optional[str], port: Optional[int], timeout: float = 1.0) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _psycopg_check(db_url: str) -> Tuple[bool, str]:
    try:
        import psycopg2
    except Exception:
        return False, "psycopg2 not installed; falling back to TCP check"

    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
        conn.close()
        return True, "ok"
    except Exception as e:
        return False, str(e)


def _check_database_connectivity(db_url: str) -> dict:
    """Check DB connectivity via psycopg2 if available, falling back to TCP."""
    connection_info = _parse_database_url(db_url)
    if not connection_info:
        return {"status": "unknown", "detail": "DATABASE_URL could not be parsed"}

    ok, detail = _psycopg_check(db_url)
    if ok:
        return {
            "status": "ok",
            "detail": detail,
            "host": connection_info.get("host"),
            "port": connection_info.get("port"),
        }

    tcp_ok = _tcp_check(connection_info.get("host"), connection_info.get("port"))
    return {
        "status": "ok" if tcp_ok else "unreachable",
        "detail": "TCP fallback ok" if tcp_ok else detail,
        "host": connection_info.get("host"),
        "port": connection_info.get("port"),
    }


@app.get("/")
def read_root():
    return {"message": "Welcome to Forseti API!"}


@app.get("/healthz")
def healthz():
    """Health endpoint that checks application and DB connectivity."""
    db_url = os.getenv("DATABASE_URL") or get_settings().DATABASE_URL
    return {"app": "ok", "db": _check_database_connectivity(db_url)}


def get_analysis_engine():
    return get_engine()


def validated_symbol(symbol: str = Path(...)) -> str:
    try:
        return validate_and_normalize_ticker(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(
    request: AnalyzeRequest,
    include_trace: bool = Query(default=False),
    engine=Depends(get_analysis_engine),
):
    try:
        from agents.config import load_agent_config

        if load_agent_config().is_agentic():
            from agents.orchestration.workflow import AgenticAnalysisWorkflow

            response = AgenticAnalysisWorkflow(load_agent_config(), engine=engine).analyze(
                request.ticker, request=request
            )
            if not include_trace:
                response.trace = None
            return response
        return analyze_request(request, engine=engine)
    except GoogleWorkflowError as exc:
        raise HTTPException(status_code=502, detail=f"google_workflow_failed: {exc}") from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
