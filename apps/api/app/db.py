from collections.abc import AsyncIterator
import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
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


async def _ensure_agent_workspace_columns(conn: AsyncConnection) -> None:
    """Add workspace columns to pre-existing SQLite databases.

    `create_all` only creates missing tables, so databases created before the
    workspace feature need an explicit ALTER.
    """
    rows = (await conn.exec_driver_sql("PRAGMA table_info(agents)")).fetchall()
    existing = {row[1] for row in rows}
    if "workspace_mode" not in existing:
        await conn.exec_driver_sql(
            "ALTER TABLE agents ADD COLUMN workspace_mode VARCHAR(16) NOT NULL DEFAULT 'virtual'"
        )
    if "workspace_path" not in existing:
        await conn.exec_driver_sql(
            "ALTER TABLE agents ADD COLUMN workspace_path VARCHAR(256) NOT NULL DEFAULT ''"
        )


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if settings.app_database_url.startswith("sqlite"):
        async with engine.begin() as conn:
            await _ensure_agent_workspace_columns(conn)
        try:
            await _ensure_memory_fts()
        except Exception:
            logger.exception("memory FTS setup failed; falling back to LIKE")


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
