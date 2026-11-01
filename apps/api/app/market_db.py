from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings


class MarketBase(DeclarativeBase):
    pass


# NullPool keeps SQLite connections on the event loop that uses them; tests and
# the backtest worker both open short-lived connections.
market_engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.market_db_path}", future=True, poolclass=NullPool
)
MarketSessionLocal = async_sessionmaker(
    market_engine, expire_on_commit=False, class_=AsyncSession
)


@event.listens_for(market_engine.sync_engine, "connect")
def _market_sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


async def init_market_db() -> None:
    from app import market_models  # noqa: F401

    settings.market_db_path.parent.mkdir(parents=True, exist_ok=True)
    async with market_engine.begin() as conn:
        await conn.run_sync(MarketBase.metadata.create_all)


async def get_market_db() -> AsyncIterator[AsyncSession]:
    async with MarketSessionLocal() as session:
        yield session
