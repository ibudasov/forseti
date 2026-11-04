from collections.abc import Callable

from app.db.session import dispose_engines
from app.observability.logging import configure_logging
from app.observability.tracing import configure_tracing, shutdown_tracing
from app.settings import validate_runtime_settings


def run_job(entrypoint: Callable[[], int | None]) -> int | None:
    """Validate configuration before job work and close pools even on failure."""
    try:
        configure_logging()
        configure_tracing()
        validate_runtime_settings()
        return entrypoint()
    finally:
        shutdown_tracing()
        dispose_engines()
