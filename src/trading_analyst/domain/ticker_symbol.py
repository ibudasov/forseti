import re
from dataclasses import dataclass

from trading_analyst.domain.errors import InvalidTickerSymbolError

TICKER_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")


@dataclass(frozen=True)
class TickerSymbol:
    value: str

    def __post_init__(self) -> None:
        if not TICKER_SYMBOL_PATTERN.match(self.value):
            raise InvalidTickerSymbolError(f"'{self.value}' is not a valid ticker symbol.")
