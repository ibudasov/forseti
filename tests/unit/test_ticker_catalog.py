from datetime import UTC, datetime

import pytest

from trading_analyst.application.ticker_catalog import TickerCatalog
from trading_analyst.domain.errors import TickerNotFoundError
from trading_analyst.domain.sector import Sector
from trading_analyst.domain.ticker_record import TickerRecord
from trading_analyst.domain.ticker_reference import TickerReference
from trading_analyst.domain.ticker_symbol import TickerSymbol


class FakeTickerRepository:
    def __init__(self, records: list[TickerRecord]) -> None:
        self._records = {record.symbol.value: record for record in records}

    async def find_by_symbol(self, symbol: TickerSymbol) -> TickerRecord | None:
        return self._records.get(symbol.value)


def build_record() -> TickerRecord:
    return TickerRecord(
        symbol=TickerSymbol("NVDA"),
        exchange="NASDAQ",
        company_name="NVIDIA Corporation",
        sector=Sector.AI,
        currency="USD",
        tracked_since=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_returns_record_without_exchange() -> None:
    catalog = TickerCatalog(FakeTickerRepository([build_record()]))
    reference = TickerReference(symbol=TickerSymbol("NVDA"), exchange=None)
    assert await catalog.resolve(reference) == build_record()


@pytest.mark.asyncio
async def test_returns_record_with_matching_exchange() -> None:
    catalog = TickerCatalog(FakeTickerRepository([build_record()]))
    reference = TickerReference(symbol=TickerSymbol("NVDA"), exchange="NASDAQ")
    assert await catalog.resolve(reference) == build_record()


@pytest.mark.asyncio
async def test_raises_for_unknown_symbol() -> None:
    catalog = TickerCatalog(FakeTickerRepository([build_record()]))
    reference = TickerReference(symbol=TickerSymbol("ZZZZ"), exchange=None)
    with pytest.raises(TickerNotFoundError):
        await catalog.resolve(reference)


@pytest.mark.asyncio
async def test_raises_for_mismatched_exchange() -> None:
    catalog = TickerCatalog(FakeTickerRepository([build_record()]))
    reference = TickerReference(symbol=TickerSymbol("NVDA"), exchange="NYSE")
    with pytest.raises(TickerNotFoundError):
        await catalog.resolve(reference)
