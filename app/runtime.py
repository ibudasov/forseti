from collections.abc import Callable

from app.db.session import dispose_engines
from app.settings import validate_runtime_settings


def run_job(entrypoint: Callable[[], int | None]) -> int | None:
    """Validate configuration before job work and close pools even on failure."""
    try:
        validate_runtime_settings()
        return entrypoint()
    finally:
        dispose_engines()
