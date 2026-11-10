from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from app.db import (
    _ensure_agent_columns,
    _ensure_app_config_columns,
    _ensure_cron_job_columns,
    _ensure_memory_item_columns,
    _ensure_session_origin,
    engine,
    init_db,
)
from app.market_db import init_market_db, market_engine


async def test_ensure_agent_columns_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT)")
        await conn.exec_driver_sql("INSERT INTO agents (id, name) VALUES ('a', 'Legacy')")
        await _ensure_agent_columns(conn)
        await _ensure_agent_columns(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(agents)")
        columns = {row[1] for row in result.fetchall()}
        assert {"enabled_skills", "memory_turn_count"} <= columns

        row = (
            await conn.exec_driver_sql(
                "SELECT enabled_skills, memory_turn_count FROM agents WHERE id='a'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "[]"
        assert row[1] == 0
    await engine.dispose()


async def test_ensure_app_config_columns_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-config.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE app_configs (id TEXT PRIMARY KEY, session_retention_days INTEGER)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO app_configs (id, session_retention_days) VALUES ('default', 30)"
        )
        await _ensure_app_config_columns(conn)
        await _ensure_app_config_columns(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(app_configs)")
        columns = {row[1] for row in result.fetchall()}
        assert "memory_consolidate_every_turns" in columns

        row = (
            await conn.exec_driver_sql(
                "SELECT memory_consolidate_every_turns FROM app_configs WHERE id='default'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == 10
    await engine.dispose()


async def test_ensure_session_origin_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-sessions.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, agent_id TEXT, title TEXT)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO sessions (id, agent_id, title) VALUES ('s', 'a', 'Legacy')"
        )
        await _ensure_session_origin(conn)
        await _ensure_session_origin(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(sessions)")
        columns = {row[1] for row in result.fetchall()}
        assert "origin" in columns

        row = (
            await conn.exec_driver_sql(
                "SELECT origin FROM sessions WHERE id='s'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "chat"
    await engine.dispose()


async def test_ensure_memory_item_columns_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-memory.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE memory_items "
            "(id TEXT PRIMARY KEY, scope TEXT, agent_id TEXT, content TEXT)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO memory_items (id, scope, agent_id, content) "
            "VALUES ('m', 'user', NULL, '旧记忆')"
        )
        await _ensure_memory_item_columns(conn)
        await _ensure_memory_item_columns(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(memory_items)")
        columns = {row[1] for row in result.fetchall()}
        assert {
            "source_session_id",
            "source_kind",
            "snapshot_text",
            "snapshot_at",
        } <= columns

        row = (
            await conn.exec_driver_sql(
                "SELECT source_kind, snapshot_text FROM memory_items WHERE id='m'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "legacy"
        assert row[1] is None
    await engine.dispose()


async def test_ensure_cron_job_columns_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-cron.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE cron_jobs (id TEXT PRIMARY KEY, name TEXT)"
        )
        await conn.exec_driver_sql(
            "INSERT INTO cron_jobs (id, name) VALUES ('c', 'Legacy')"
        )
        await _ensure_cron_job_columns(conn)
        await _ensure_cron_job_columns(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(cron_jobs)")
        columns = {row[1] for row in result.fetchall()}
        assert {"push_channel_id", "push_conversation_id"} <= columns

        row = (
            await conn.exec_driver_sql(
                "SELECT push_channel_id, push_conversation_id FROM cron_jobs WHERE id='c'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None
    await engine.dispose()


async def test_init_db_creates_backtests_table() -> None:
    await init_db()
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in result.fetchall()}
    assert "backtests" in tables
    assert "cron_jobs" in tables
    assert "knowledge_configs" in tables


async def test_init_market_db_is_idempotent() -> None:
    await init_market_db()
    await init_market_db()
    async with market_engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in result.fetchall()}
    assert {"market_candles", "market_sync_state"} <= tables
