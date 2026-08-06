from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trading_analyst.domain.ticker_record import TickerRecord
from trading_analyst.domain.ticker_symbol import TickerSymbol
from trading_analyst.infrastructure.ticker_model import TickerModel


class SqlAlchemyTickerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_symbol(self, symbol: TickerSymbol) -> TickerRecord | None:
        statement = select(TickerModel).where(TickerModel.symbol == symbol.value)
        result = await self._session.execute(statement)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return model.to_record()
