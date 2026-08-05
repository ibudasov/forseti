from dataclasses import asdict
import os
import socket
from urllib.parse import urlparse
from typing import Optional, Tuple

from fastapi import FastAPI

app = FastAPI(title="Forseti API")


def _parse_database_url(url: Optional[str]):
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


def _default_database_url() -> str:
    user = os.getenv("POSTGRES_USER", "user")
    password = os.getenv("POSTGRES_PASSWORD", "password")
    db = os.getenv("POSTGRES_DB", "forseti")
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


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


@app.get("/")
def read_root():
    return {"message": "Welcome to Forseti API!"}


@app.get("/healthz")
def healthz():
    """Health endpoint that checks application and DB connectivity.

    It will try a real SQL check using psycopg2 if available; otherwise falls
    back to a TCP port check against the host/port extracted from DATABASE_URL.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        db_url = _default_database_url()
        os.environ["DATABASE_URL"] = db_url

    db_info = _parse_database_url(db_url)
    result: dict = {"app": "ok", "db": {}}

    if not db_info:
        result["db"]["status"] = "unknown"
        result["db"]["detail"] = "DATABASE_URL could not be parsed"
        return result

    # Prefer a real DB check if psycopg2 is installed
    ok, detail = _psycopg_check(db_url)
    if ok:
        result["db"]["status"] = "ok"
        result["db"]["detail"] = detail
        result["db"]["host"] = db_info.get("host")
        result["db"]["port"] = db_info.get("port")
        return result

    # Fallback: TCP check
    tcp_ok = _tcp_check(db_info.get("host"), db_info.get("port"))
    result["db"]["status"] = "ok" if tcp_ok else "unreachable"
    result["db"]["detail"] = detail if not detail.startswith("psycopg2 not installed") else (
        "TCP fallback ok" if tcp_ok else detail
    )
    result["db"]["host"] = db_info.get("host")
    result["db"]["port"] = db_info.get("port")
    return result
