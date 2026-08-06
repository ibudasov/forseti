from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from trading_analyst.api.dependencies import get_session
from trading_analyst.domain.sector import Sector
from trading_analyst.infrastructure.ticker_model import Base, TickerModel
from trading_analyst.main import create_app


@pytest_asyncio.fixture(scope="session")
async def test_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    tracked_since = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        session.add_all(
            [
                TickerModel(
                    id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
                    symbol="NVDA",
                    exchange="NASDAQ",
                    company_name="NVIDIA Corporation",
                    sector=Sector.AI.value,
                    currency="USD",
                    tracked_since=tracked_since,
                ),
                TickerModel(
                    id=uuid.UUID("10000000-0000-0000-0000-000000000002"),
                    symbol="LMT",
                    exchange="NYSE",
                    company_name="Lockheed Martin Corporation",
                    sector=Sector.DEFENCE.value,
                    currency="USD",
                    tracked_since=tracked_since,
                ),
            ]
        )
        await session.commit()

    try:
        yield session_factory
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def app(
    test_session_factory: async_sessionmaker[AsyncSession],
):
    application = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client
