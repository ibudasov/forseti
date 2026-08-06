import os
import socket
from typing import Optional, Tuple
from urllib.parse import urlparse

from fastapi import Depends, FastAPI

from app.db.session import get_engine
from app.schemas.analyze import AnalyzeRequest, AnalyzeResponse
from app.services.analyzer import analyze_request
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


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, engine=Depends(get_analysis_engine)):
    return analyze_request(request, engine=engine)
