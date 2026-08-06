from dataclasses import dataclass
from datetime import datetime

from trading_analyst.domain.sector import Sector
from trading_analyst.domain.ticker_symbol import TickerSymbol


@dataclass(frozen=True)
class TickerRecord:
    symbol: TickerSymbol
    exchange: str
    company_name: str
    sector: Sector
    currency: str
    tracked_since: datetime
