from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from trading_analyst.application.ticker_catalog import TickerCatalog
from trading_analyst.application.ticker_reference_parser import TickerReferenceParser
from trading_analyst.infrastructure.database import session_factory
from trading_analyst.infrastructure.sqlalchemy_ticker_repository import (
    SqlAlchemyTickerRepository,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


def get_ticker_catalog(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TickerCatalog:
    repository = SqlAlchemyTickerRepository(session)
    return TickerCatalog(repository)


def get_reference_parser() -> TickerReferenceParser:
    return TickerReferenceParser()
