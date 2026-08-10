import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Path

from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.schemas.ticker import TickerProfileResponse
from app.schemas.evidence import TickerEvidenceResponse, EvidenceBlockSchema
from app.services.analyzer import analyze_request, validate_and_normalize_ticker
from app.services.ticker_profile import build_ticker_profile
from app.services.retrieval import retrieve
from app.services.synthesis import synthesize_evidence
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
def analyze(request: AnalyzeRequest, engine=Depends(get_analysis_engine)):
    try:
        return analyze_request(request, engine=engine)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/ticker/{symbol}", response_model=TickerProfileResponse)
def read_ticker_profile(symbol: str = Depends(validated_symbol), engine=Depends(get_analysis_engine)):
    profile = build_ticker_profile(symbol, engine=engine)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"ticker_not_found: {symbol}")
    return profile


@app.get("/ticker/{symbol}/evidence", response_model=TickerEvidenceResponse)
def get_ticker_evidence(symbol: str = Depends(validated_symbol), engine=Depends(get_analysis_engine)):
    """Retrieve evidence for a ticker from document chunks."""
    try:
        # Retrieve relevant chunks
        retrieval_results = retrieve(ticker=symbol, top_k=10, engine=engine)
        
        # Synthesize into structured evidence
        synthesis = synthesize_evidence(retrieval_results)
        
        # Convert to API schema
        evidence_block = EvidenceBlockSchema(
            bullish_drivers=[
                {
                    "text": item.text,
                    "chunk_id": item.chunk_id,
                    "source_url": item.source_url,
                    "published_at": item.published_at,
                }
                for item in synthesis.bullish_drivers
            ],
            bearish_risks=[
                {
                    "text": item.text,
                    "chunk_id": item.chunk_id,
                    "source_url": item.source_url,
                    "published_at": item.published_at,
                }
                for item in synthesis.bearish_risks
            ],
            catalysts=[
                {
                    "text": item.text,
                    "chunk_id": item.chunk_id,
                    "source_url": item.source_url,
                    "published_at": item.published_at,
                }
                for item in synthesis.catalysts
            ],
            news_alignment=synthesis.news_alignment,
            red_flags=[
                {
                    "text": item.text,
                    "chunk_id": item.chunk_id,
                    "source_url": item.source_url,
                    "published_at": item.published_at,
                }
                for item in synthesis.red_flags
            ],
            confidence_adjustment=synthesis.confidence_adjustment,
            status=synthesis.status,
        )
        
        return TickerEvidenceResponse(ticker=symbol, evidence=evidence_block)
    
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error retrieving evidence: {str(exc)}") from exc
