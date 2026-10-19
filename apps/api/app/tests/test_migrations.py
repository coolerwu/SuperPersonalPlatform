from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from app.db import _ensure_agent_columns


async def test_ensure_agent_columns_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT)")
        await conn.exec_driver_sql("INSERT INTO agents (id, name) VALUES ('a', 'Legacy')")
        await _ensure_agent_columns(conn)
        await _ensure_agent_columns(conn)

        result = await conn.exec_driver_sql("PRAGMA table_info(agents)")
        columns = {row[1] for row in result.fetchall()}
        assert {"enabled_skills", "enabled_tools"} <= columns

        row = (
            await conn.exec_driver_sql(
                "SELECT enabled_skills, enabled_tools FROM agents WHERE id='a'"
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "[]"
        assert row[1] == "[]"
    await engine.dispose()
