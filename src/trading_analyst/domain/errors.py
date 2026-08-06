class InvalidTickerSymbolError(ValueError):
    """Raised when a symbol violates the ticker grammar."""


class InvalidTickerReferenceError(ValueError):
    """Raised when a raw ticker reference cannot be parsed."""


class TickerNotFoundError(Exception):
    def __init__(self, symbol: str, exchange: str | None = None) -> None:
        self.symbol = symbol
        self.exchange = exchange
        qualifier = f" on exchange '{exchange}'" if exchange else ""
        super().__init__(f"No ticker '{symbol}'{qualifier} is tracked.")
