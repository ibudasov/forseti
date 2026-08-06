import pytest

from trading_analyst.domain.errors import InvalidTickerSymbolError
from trading_analyst.domain.ticker_symbol import TickerSymbol


def test_accepts_symbol_with_dot() -> None:
    assert TickerSymbol("BRK.B").value == "BRK.B"


def test_accepts_symbol_with_hyphen() -> None:
    assert TickerSymbol("BF-B").value == "BF-B"


@pytest.mark.parametrize("value", ["NV$DA", "NV DA", "1ABC", "ABCDEFGHIJKLM"])
def test_rejects_invalid_symbol(value: str) -> None:
    with pytest.raises(InvalidTickerSymbolError):
        TickerSymbol(value)
