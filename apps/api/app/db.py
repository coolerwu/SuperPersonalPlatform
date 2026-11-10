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


_CHANNEL_INBOUND_REQUIRED_COLUMNS = frozenset(
    {
        "id",
        "channel_id",
        "external_message_id",
        "text",
        "external_id",
        "session_id",
    }
)


async def _drop_stale_channel_inbounds(conn) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(channel_inbounds)")
    columns = {row[1] for row in result.fetchall()}
    if not columns:
        return
    missing = _CHANNEL_INBOUND_REQUIRED_COLUMNS - columns
    if not missing:
        return
    # The old table lacks the quote-recovery columns, so every inbound insert
    # would fail. Rebuild it; losing dedup rows means iLink may replay one
    # already-processed batch once, which is preferable to a permanent error.
    logger.warning(
        "dropping stale channel_inbounds table missing columns: %s (have: %s)",
        ", ".join(sorted(missing)),
        ", ".join(sorted(columns)),
    )
    await conn.exec_driver_sql("DROP TABLE channel_inbounds")


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        if settings.app_database_url.startswith("sqlite"):
            await _drop_stale_channel_deliveries(conn)
            await _drop_stale_channel_inbounds(conn)
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
