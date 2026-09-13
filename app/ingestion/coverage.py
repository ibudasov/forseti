from __future__ import annotations

from dataclasses import asdict, dataclass
from app.db.repository import (
    count_earnings_events,
    count_fundamental_observations,
    count_price_bars,
    get_latest_fundamental,
    get_latest_technical_feature,
    list_active_securities,
)


@dataclass(frozen=True)
class SourceCoverage:
    source: str
    expected: int
    present: int
    missing_tickers: tuple[str, ...]

    def ratio(self) -> float:
        return self.present / self.expected if self.expected else 1.0

    def is_sufficient(self, minimum: float) -> bool:
        return self.ratio() >= minimum

    def as_dict(self) -> dict:
        return {**asdict(self), "missing_tickers": list(self.missing_tickers), "ratio": self.ratio()}


def _coverage(source: str, securities: list, present_tickers: set[str]) -> SourceCoverage:
    missing = tuple(security.ticker for security in securities if security.ticker not in present_tickers)
    return SourceCoverage(source, len(securities), len(present_tickers), missing)


def build_coverage_report(engine=None) -> tuple[SourceCoverage, ...]:
    securities = list_active_securities(engine=engine)
    price_tickers = set()
    price_200_tickers = set()
    feature_tickers = set()
    fundamental_tickers = set()
    fundamental_observation_tickers = set()
    earnings_tickers = set()
    for security in securities:
        count = count_price_bars(security.ticker, engine=engine)
        if count:
            price_tickers.add(security.ticker)
        if count >= 200:
            price_200_tickers.add(security.ticker)
        if get_latest_technical_feature(security.ticker, engine=engine) is not None:
            feature_tickers.add(security.ticker)
        if get_latest_fundamental(security.ticker, engine=engine) is not None:
            fundamental_tickers.add(security.ticker)
        if count_fundamental_observations(security.ticker, engine=engine):
            fundamental_observation_tickers.add(security.ticker)
        if count_earnings_events(security.ticker, engine=engine):
            earnings_tickers.add(security.ticker)
    return (
        _coverage("prices", securities, price_tickers),
        _coverage("prices_200", securities, price_200_tickers),
        _coverage("features", securities, feature_tickers),
        _coverage("fundamentals", securities, fundamental_tickers),
        _coverage("fundamental_observations", securities, fundamental_observation_tickers),
        _coverage("earnings", securities, earnings_tickers),
    )
