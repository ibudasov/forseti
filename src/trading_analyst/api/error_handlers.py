import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from trading_analyst.domain.errors import (
    InvalidTickerReferenceError,
    InvalidTickerSymbolError,
    TickerNotFoundError,
)

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidTickerSymbolError)
    @app.exception_handler(InvalidTickerReferenceError)
    async def handle_invalid_ticker_reference(
        request: Request, exception: InvalidTickerSymbolError | InvalidTickerReferenceError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_TICKER_REFERENCE",
                    "message": str(exception),
                }
            },
        )

    @app.exception_handler(TickerNotFoundError)
    async def handle_ticker_not_found(
        request: Request, exception: TickerNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "TICKER_NOT_FOUND",
                    "message": str(exception),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(request: Request, exception: Exception) -> JSONResponse:
        logger.exception("Unhandled request error", exc_info=exception)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An internal error occurred.",
                }
            },
        )
