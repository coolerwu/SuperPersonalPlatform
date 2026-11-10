from collections.abc import AsyncIterator
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.app_database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

if settings.app_database_url.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


async def _drop_stale_channel_deliveries(conn) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(channel_deliveries)")
    columns = {row[1] for row in result.fetchall()}
    if not columns or "id" in columns:
        return
    logger.warning(
        "dropping stale channel_deliveries table without id column: %s",
        ", ".join(sorted(columns)),
    )
    await conn.exec_driver_sql("DROP TABLE channel_deliveries")


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        if settings.app_database_url.startswith("sqlite"):
            await _drop_stale_channel_deliveries(conn)
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
