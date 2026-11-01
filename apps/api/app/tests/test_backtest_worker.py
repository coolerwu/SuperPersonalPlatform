from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

import app.services.backtest_worker as backtest_worker_module
from app.db import SessionLocal, init_db
from app.models import Backtest, utcnow
from app.schemas import BacktestCreate
from app.services.backtest_service import (
    cancel_backtest,
    create_backtest,
    delete_all_backtests,
    get_backtest,
    requeue_backtests,
)
from app.services.backtest_worker import BacktestWorker
from app.services.market_data import EnsureResult, MarketDataError
from app.services.timeframes import HOUR_MS

START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
END = START + 48 * HOUR_MS


def _payload(**overrides: object) -> BacktestCreate:
    values = {
        "symbol": "BTCUSDT",
        "granularity": "1h",
        "start_ms": START,
        "end_ms": END,
        "initial_capital": 10000,
        "fee_rate": 0.001,
        "slippage_rate": 0.0005,
        "strategy_code": "def strategy(df):\n    return df['close'] * 0 + 1.0\n",
    }
    values.update(overrides)
    return BacktestCreate(**values)


@pytest.fixture(autouse=True)
async def _clean_backtests() -> None:
    await init_db()
    await delete_all_backtests()
    yield
    await delete_all_backtests()


async def _fake_ensure(symbol: str, granularity: str, start_ms: int, end_ms: int, *, progress=None):
    return EnsureResult(
        symbol=symbol,
        granularity=granularity,
        data_start_ms=start_ms,
        data_end_ms=end_ms - HOUR_MS,
        end_open_ms=end_ms,
        rows=48,
        warnings=[],
    )


async def _fake_run(**kwargs):
    return 0, {
        "metrics": {
            "bars": 48,
            "start_ms": kwargs["start_ms"],
            "end_ms": kwargs["end_open_ms"] - HOUR_MS,
            "total_return": 0.1,
        },
        "equity": [[START, 10000], [END - HOUR_MS, 11000]],
        "drawdown": [[START, 0]],
        "candles": [[START, 1, 2, 0.5, 1.5, 10]],
        "trades": [],
        "trades_total": 0,
        "trades_truncated": False,
        "warnings": ["策略提示"],
    }


async def _wait_status(backtest_id: str, statuses: tuple[str, ...], timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    row = None
    while asyncio.get_running_loop().time() < deadline:
        row = await get_backtest(backtest_id)
        if row is not None and row.status in statuses:
            return row
        await asyncio.sleep(0.02)
    return row


async def test_worker_completes_backtest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backtest_worker_module.market_service, "ensure_data", _fake_ensure)
    monkeypatch.setattr(backtest_worker_module, "run_backtest_subprocess", _fake_run)
    row = await create_backtest(_payload())
    worker = BacktestWorker()
    await worker.start()
    try:
        worker.wake()
        stored = await _wait_status(row.id, ("completed",))
        assert stored is not None
        assert stored.status == "completed"
        assert stored.metrics["total_return"] == 0.1
        assert stored.equity == [[START, 10000], [END - HOUR_MS, 11000]]
        assert stored.warnings == ["策略提示"]
        assert stored.bar_count == 48
    finally:
        await worker.stop()


async def test_worker_marks_strategy_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_run(**kwargs):
        return 2, {"error": "策略执行失败：boom", "detail": "traceback"}

    monkeypatch.setattr(backtest_worker_module.market_service, "ensure_data", _fake_ensure)
    monkeypatch.setattr(backtest_worker_module, "run_backtest_subprocess", failing_run)
    row = await create_backtest(_payload())
    worker = BacktestWorker()
    await worker.start()
    try:
        worker.wake()
        stored = await _wait_status(row.id, ("failed",))
        assert stored is not None
        assert stored.status == "failed"
        assert "boom" in (stored.error or "")
        assert stored.error_detail == "traceback"
    finally:
        await worker.stop()


async def test_worker_marks_market_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_ensure(*args, **kwargs):
        raise MarketDataError("行情代理不可用，请检查 MARKET_HTTP_PROXY")

    monkeypatch.setattr(
        backtest_worker_module.market_service, "ensure_data", failing_ensure
    )
    row = await create_backtest(_payload())
    worker = BacktestWorker()
    await worker.start()
    try:
        worker.wake()
        stored = await _wait_status(row.id, ("failed",))
        assert stored is not None
        assert "代理不可用" in (stored.error or "")
    finally:
        await worker.stop()


async def test_worker_cancels_running_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def blocking_ensure(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(
        backtest_worker_module.market_service, "ensure_data", blocking_ensure
    )
    row = await create_backtest(_payload())
    worker = BacktestWorker()
    await worker.start()
    try:
        worker.wake()
        await asyncio.wait_for(started.wait(), timeout=5)
        assert await worker.cancel(row.id) is True
        stored = await _wait_status(row.id, ("cancelled",))
        assert stored is not None
        assert stored.status == "cancelled"
    finally:
        await worker.stop()


async def test_cancel_queued_backtest() -> None:
    row = await create_backtest(_payload())
    assert await cancel_backtest(row.id) is True
    stored = await get_backtest(row.id)
    assert stored is not None
    assert stored.status == "cancelled"
    assert await cancel_backtest(row.id) is False


async def test_requeue_running_backtests() -> None:
    row = await create_backtest(_payload())
    async with SessionLocal() as db:
        stored = await db.get(Backtest, row.id)
        assert stored is not None
        stored.status = "running"
        stored.started_at = utcnow()
        await db.commit()

    await requeue_backtests()

    stored = await get_backtest(row.id)
    assert stored is not None
    assert stored.status == "queued"
    assert stored.started_at is None

    async with SessionLocal() as db:
        stored = await db.get(Backtest, row.id)
        assert stored is not None
        stored.status = "running"
        stored.cancel_requested = True
        await db.commit()

    await requeue_backtests()
    stored = await get_backtest(row.id)
    assert stored is not None
    assert stored.status == "cancelled"
