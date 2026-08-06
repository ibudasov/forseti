import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from trading_analyst.domain.sector import Sector
from trading_analyst.domain.ticker_record import TickerRecord
from trading_analyst.domain.ticker_symbol import TickerSymbol


class Base(DeclarativeBase):
    pass


class TickerModel(Base):
    __tablename__ = "tickers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sector: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    tracked_since: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def to_record(self) -> TickerRecord:
        return TickerRecord(
            symbol=TickerSymbol(self.symbol),
            exchange=self.exchange,
            company_name=self.company_name,
            sector=Sector(self.sector),
            currency=self.currency,
            tracked_since=self.tracked_since,
        )
