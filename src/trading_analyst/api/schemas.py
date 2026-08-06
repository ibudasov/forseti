from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, field_serializer

from trading_analyst.domain.sector import Sector
from trading_analyst.domain.ticker_record import TickerRecord


class DataStatus(StrEnum):
    REFERENCE_ONLY = "reference_only"


class DataAvailability(BaseModel):
    model_config = ConfigDict(frozen=True)

    price_history: bool = False
    fundamentals: bool = False
    news: bool = False


class TickerResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    exchange: str
    company_name: str
    sector: Sector
    currency: str
    data_status: DataStatus
    data_availability: DataAvailability
    tracked_since: datetime

    @field_serializer("tracked_since")
    def serialize_tracked_since(self, tracked_since: datetime) -> str:
        if tracked_since.tzinfo is None:
            tracked_since = tracked_since.replace(tzinfo=UTC)
        return tracked_since.astimezone(UTC).isoformat().replace("+00:00", "Z")


def to_ticker_response(record: TickerRecord) -> TickerResponse:
    return TickerResponse(
        symbol=record.symbol.value,
        exchange=record.exchange,
        company_name=record.company_name,
        sector=record.sector,
        currency=record.currency,
        data_status=DataStatus.REFERENCE_ONLY,
        data_availability=DataAvailability(),
        tracked_since=record.tracked_since,
    )
