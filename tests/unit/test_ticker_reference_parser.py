import pytest

from trading_analyst.application.ticker_reference_parser import TickerReferenceParser
from trading_analyst.domain.errors import InvalidTickerReferenceError, InvalidTickerSymbolError


def test_parses_plain_symbol() -> None:
    reference = TickerReferenceParser().parse("nvda")
    assert reference.symbol.value == "NVDA"
    assert reference.exchange is None


def test_trims_and_uppercases_plain_symbol() -> None:
    reference = TickerReferenceParser().parse("  Nvda  ")
    assert reference.symbol.value == "NVDA"
    assert reference.exchange is None


def test_parses_qualified_symbol() -> None:
    reference = TickerReferenceParser().parse("nasdaq:nvda")
    assert reference.symbol.value == "NVDA"
    assert reference.exchange == "NASDAQ"


def test_maps_arca_alias() -> None:
    reference = TickerReferenceParser().parse("ARCA:SPY")
    assert reference.exchange == "NYSEARCA"


def test_maps_amex_alias() -> None:
    reference = TickerReferenceParser().parse("AMEX:XYZ")
    assert reference.exchange == "NYSEAMERICAN"


@pytest.mark.parametrize("value", ["BRK.B", "BF-B"])
def test_accepts_valid_special_character_symbols(value: str) -> None:
    reference = TickerReferenceParser().parse(value)
    assert reference.symbol.value == value


@pytest.mark.parametrize("value", ["", "   "])
def test_rejects_empty_reference(value: str) -> None:
    with pytest.raises(InvalidTickerReferenceError):
        TickerReferenceParser().parse(value)


@pytest.mark.parametrize("value", ["NASDAQ:", ":NVDA"])
def test_rejects_malformed_qualified_reference(value: str) -> None:
    with pytest.raises(InvalidTickerReferenceError):
        TickerReferenceParser().parse(value)


def test_rejects_unsupported_exchange() -> None:
    with pytest.raises(InvalidTickerReferenceError):
        TickerReferenceParser().parse("XETRA:SIE")


@pytest.mark.parametrize("value", ["NV$DA", "NV DA", "1ABC", "ABCDEFGHIJKLM"])
def test_rejects_invalid_symbol_grammar(value: str) -> None:
    with pytest.raises(InvalidTickerSymbolError):
        TickerReferenceParser().parse(value)
