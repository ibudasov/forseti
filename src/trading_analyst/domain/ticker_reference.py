from dataclasses import dataclass

from trading_analyst.domain.ticker_symbol import TickerSymbol


@dataclass(frozen=True)
class TickerReference:
    symbol: TickerSymbol
    exchange: str | None
