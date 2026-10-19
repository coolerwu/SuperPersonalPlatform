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


async def _ensure_memory_fts() -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                content,
                tags,
                content='memory_items',
                content_rowid='rowid',
                tokenize='trigram'
            )
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_items BEGIN
                INSERT INTO memory_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_items BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, content, tags)
                VALUES ('delete', old.rowid, old.content, old.tags);
            END
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory_items BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, content, tags)
                VALUES ('delete', old.rowid, old.content, old.tags);
                INSERT INTO memory_fts(rowid, content, tags)
                VALUES (new.rowid, new.content, new.tags);
            END
            """
        )
        await conn.exec_driver_sql("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")


async def _ensure_agent_column(conn, column: str) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(agents)")
    if column in {row[1] for row in result.fetchall()}:
        return
    try:
        await conn.exec_driver_sql(
            f"ALTER TABLE agents ADD COLUMN {column} JSON NOT NULL DEFAULT '[]'"
        )
    except Exception:
        # Another process may have added the column concurrently; only re-raise
        # when it is genuinely still missing.
        result = await conn.exec_driver_sql("PRAGMA table_info(agents)")
        if column not in {row[1] for row in result.fetchall()}:
            raise


async def _ensure_agent_columns(conn) -> None:  # noqa: ANN001
    for column in ("enabled_skills",):
        await _ensure_agent_column(conn, column)


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.app_database_url.startswith("sqlite"):
            await _ensure_agent_columns(conn)

    if settings.app_database_url.startswith("sqlite"):
        try:
            await _ensure_memory_fts()
        except Exception:
            logger.exception("memory FTS setup failed; falling back to LIKE")


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
