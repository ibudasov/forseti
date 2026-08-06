from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trading_analyst.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)
