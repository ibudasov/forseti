from typing import Protocol

from trading_analyst.domain.ticker_record import TickerRecord
from trading_analyst.domain.ticker_symbol import TickerSymbol


class TickerRepository(Protocol):
    async def find_by_symbol(self, symbol: TickerSymbol) -> TickerRecord | None: ...
