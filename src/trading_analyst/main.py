import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from trading_analyst.api.error_handlers import register_error_handlers
from trading_analyst.api.routes_health import router as health_router
from trading_analyst.api.routes_tickers import router as tickers_router
from trading_analyst.config import get_settings
from trading_analyst.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    application = FastAPI(title="Trading Analyst", version="0.1.0")
    application.include_router(health_router)
    application.include_router(tickers_router)
    register_error_handlers(application)

    @application.middleware("http")
    async def log_requests(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "method=%s path=%s status_code=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    return application


app = create_app()
