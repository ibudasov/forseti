from trading_analyst.domain.errors import InvalidTickerReferenceError
from trading_analyst.domain.ticker_reference import TickerReference
from trading_analyst.domain.ticker_symbol import TickerSymbol

EXCHANGE_ALIASES = {
    "ARCA": "NYSEARCA",
    "AMEX": "NYSEAMERICAN",
}

SUPPORTED_EXCHANGES = frozenset({"NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN", "CBOE", "BATS"})


class TickerReferenceParser:
    def parse(self, raw_reference: str) -> TickerReference:
        normalized = raw_reference.strip().upper()
        if not normalized:
            raise InvalidTickerReferenceError("Ticker reference is empty.")
        if ":" not in normalized:
            return TickerReference(symbol=TickerSymbol(normalized), exchange=None)
        return self._parse_qualified_reference(normalized)

    def _parse_qualified_reference(self, reference: str) -> TickerReference:
        exchange, separator, symbol = reference.partition(":")
        if not separator or not exchange or not symbol:
            raise InvalidTickerReferenceError(
                f"Malformed exchange-qualified reference: '{reference}'."
            )
        canonical_exchange = EXCHANGE_ALIASES.get(exchange, exchange)
        if canonical_exchange not in SUPPORTED_EXCHANGES:
            raise InvalidTickerReferenceError(f"Unsupported exchange: '{exchange}'.")
        return TickerReference(symbol=TickerSymbol(symbol), exchange=canonical_exchange)
