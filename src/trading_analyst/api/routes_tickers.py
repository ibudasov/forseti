from typing import Annotated

from fastapi import APIRouter, Depends

from trading_analyst.api.dependencies import get_reference_parser, get_ticker_catalog
from trading_analyst.api.schemas import TickerResponse, to_ticker_response
from trading_analyst.application.ticker_catalog import TickerCatalog
from trading_analyst.application.ticker_reference_parser import TickerReferenceParser

router = APIRouter(tags=["tickers"])


@router.get("/ticker/{symbol}", response_model=TickerResponse)
async def get_ticker(
    symbol: str,
    parser: Annotated[TickerReferenceParser, Depends(get_reference_parser)],
    catalog: Annotated[TickerCatalog, Depends(get_ticker_catalog)],
) -> TickerResponse:
    reference = parser.parse(symbol)
    record = await catalog.resolve(reference)
    return to_ticker_response(record)
