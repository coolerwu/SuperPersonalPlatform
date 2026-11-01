from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from app.db import _ensure_agent_columns, engine, init_db
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
        assert "enabled_skills" in columns

        row = (
            await conn.exec_driver_sql(
                "SELECT enabled_skills FROM agents WHERE id='a'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "[]"
    await engine.dispose()


async def test_init_db_creates_backtests_table() -> None:
    await init_db()
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in result.fetchall()}
    assert "backtests" in tables


async def test_init_market_db_is_idempotent() -> None:
    await init_market_db()
    await init_market_db()
    async with market_engine.begin() as conn:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in result.fetchall()}
    assert {"market_candles", "market_sync_state"} <= tables
