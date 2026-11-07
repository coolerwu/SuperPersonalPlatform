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


async def _ensure_message_fts() -> None:
    async with engine.begin() as conn:
        existing = await conn.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        )
        created = existing.first() is None
        await conn.exec_driver_sql(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content='messages',
                content_rowid='rowid',
                tokenize='trigram'
            )
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            END
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
            """
        )
        if created:
            await conn.exec_driver_sql(
                "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')"
            )
            return
        fts_count = int(
            (await conn.exec_driver_sql("SELECT count(*) FROM messages_fts")).scalar() or 0
        )
        message_count = int(
            (await conn.exec_driver_sql("SELECT count(*) FROM messages")).scalar() or 0
        )
        if fts_count == 0 and message_count > 0:
            await conn.exec_driver_sql(
                "INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')"
            )


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


async def _ensure_agent_integer_column(conn, column: str) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(agents)")
    if column in {row[1] for row in result.fetchall()}:
        return
    try:
        await conn.exec_driver_sql(
            f"ALTER TABLE agents ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
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
    await _ensure_agent_integer_column(conn, "memory_turn_count")


async def _ensure_app_config_columns(conn) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(app_configs)")
    if "memory_consolidate_every_turns" in {row[1] for row in result.fetchall()}:
        return
    try:
        await conn.exec_driver_sql(
            "ALTER TABLE app_configs ADD COLUMN memory_consolidate_every_turns "
            "INTEGER NOT NULL DEFAULT 10"
        )
    except Exception:
        result = await conn.exec_driver_sql("PRAGMA table_info(app_configs)")
        if "memory_consolidate_every_turns" not in {row[1] for row in result.fetchall()}:
            raise


async def _ensure_session_origin(conn) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(sessions)")
    if "origin" in {row[1] for row in result.fetchall()}:
        return
    try:
        await conn.exec_driver_sql(
            "ALTER TABLE sessions ADD COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'chat'"
        )
    except Exception:
        # Another process may have added the column concurrently; only re-raise
        # when it is genuinely still missing.
        result = await conn.exec_driver_sql("PRAGMA table_info(sessions)")
        if "origin" not in {row[1] for row in result.fetchall()}:
            raise


async def _ensure_cron_job_column(conn, column: str) -> None:  # noqa: ANN001
    result = await conn.exec_driver_sql("PRAGMA table_info(cron_jobs)")
    if column in {row[1] for row in result.fetchall()}:
        return
    try:
        await conn.exec_driver_sql(f"ALTER TABLE cron_jobs ADD COLUMN {column} VARCHAR(32)")
    except Exception:
        result = await conn.exec_driver_sql("PRAGMA table_info(cron_jobs)")
        if column not in {row[1] for row in result.fetchall()}:
            raise


async def _ensure_cron_job_columns(conn) -> None:  # noqa: ANN001
    for column in ("push_channel_id", "push_conversation_id"):
        await _ensure_cron_job_column(conn, column)


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.app_database_url.startswith("sqlite"):
            await _ensure_agent_columns(conn)
            await _ensure_app_config_columns(conn)
            await _ensure_session_origin(conn)
            await _ensure_cron_job_columns(conn)

    if settings.app_database_url.startswith("sqlite"):
        try:
            await _ensure_memory_fts()
        except Exception:
            logger.exception("memory FTS setup failed; falling back to LIKE")
        try:
            await _ensure_message_fts()
        except Exception:
            logger.exception("message FTS setup failed; falling back to LIKE")


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
