from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from app.config import settings
from app.db import SessionLocal
from app.models import Backtest
from app.services import backtest_service
from app.services.market_data import MarketDataError, market_service
from app.services.subprocess_utils import kill_process_group

logger = logging.getLogger(__name__)

RUNNER_PATH = Path(__file__).resolve().parents[1] / "backtest_runner.py"
MAX_RESULT_BYTES = 50 * 1024 * 1024

_active_worker: "BacktestWorker | None" = None


def set_active_worker(worker: "BacktestWorker | None") -> None:
    """Expose the in-process worker to Agent tools without app.state plumbing."""
    global _active_worker
    _active_worker = worker


def get_active_worker() -> "BacktestWorker | None":
    return _active_worker


def _requested_warmup(progress: object) -> int:
    if not isinstance(progress, dict):
        return 0
    config = progress.get("config")
    if not isinstance(config, dict):
        return 0
    try:
        return max(0, min(int(config.get("warmup_bars") or 0), 5_000))
    except (TypeError, ValueError):
        return 0


class BacktestWorker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._tasks: dict[str, asyncio.Task] = {}
        self._shutting_down = False

    @property
    def running(self) -> bool:
        return self._loop_task is not None and not self._loop_task.done()

    async def start(self) -> None:
        cleanup_backtest_dirs()
        self._loop_task = asyncio.create_task(self._run_loop(), name="backtest-worker")

    async def stop(self) -> None:
        self._shutting_down = True
        self._stop.set()
        self._wake.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
            self._tasks.clear()

    def wake(self) -> None:
        self._wake.set()

    async def _run_loop(self) -> None:
        await backtest_service.requeue_backtests()
        limit = max(1, settings.backtest_concurrency)
        while not self._stop.is_set():
            while len(self._tasks) < limit:
                try:
                    backtest_id = await backtest_service.claim_next_backtest()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("failed to claim next backtest")
                    await asyncio.sleep(1)
                    break
                if backtest_id is None:
                    break
                self._tasks[backtest_id] = asyncio.create_task(
                    self.execute_backtest(backtest_id),
                    name=f"backtest-{backtest_id}",
                )
            if not self._tasks:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                continue
            await asyncio.wait(
                self._tasks.values(),
                timeout=1.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for backtest_id, task in list(self._tasks.items()):
                if not task.done():
                    continue
                self._tasks.pop(backtest_id, None)
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception("backtest task failed for %s", backtest_id)

    async def execute_backtest(self, backtest_id: str) -> None:
        async with SessionLocal() as db:
            row = await db.get(Backtest, backtest_id)
            if row is None:
                return
            if row.cancel_requested:
                await backtest_service.cancel_backtest_record(backtest_id)
                return
            symbol = row.symbol
            granularity = row.granularity
            start_ms = row.start_ms
            end_ms = row.end_ms
            initial_capital = row.initial_capital
            fee_rate = row.fee_rate
            slippage_rate = row.slippage_rate
            strategy_code = row.strategy_code
            warmup_bars = _requested_warmup(row.progress)

        job_dir = settings.backtests_dir / backtest_id
        try:
            ensure = await self._ensure_data(
                backtest_id, symbol, granularity, start_ms, end_ms, warmup_bars
            )
            if ensure is None:
                return
            await backtest_service.mark_running(backtest_id)
            data_start_ms = (
                ensure.data_start_ms
                if ensure.data_start_ms is not None
                else start_ms
            )
            exit_code, payload = await run_backtest_subprocess(
                job_id=backtest_id,
                symbol=symbol,
                granularity=granularity,
                start_ms=data_start_ms,
                eval_start_ms=start_ms,
                end_open_ms=ensure.end_open_ms,
                warmup_bars=warmup_bars,
                future_check=settings.backtest_future_check,
                strategy_code=strategy_code,
                initial_capital=initial_capital,
                fee_rate=fee_rate,
                slippage_rate=slippage_rate,
            )
            if exit_code == 0 and payload and payload.get("metrics"):
                warnings = list(payload.get("warnings") or []) + ensure.warnings
                await backtest_service.complete_backtest(
                    backtest_id,
                    result=payload,
                    data_start_ms=ensure.data_start_ms,
                    data_end_ms=ensure.data_end_ms,
                    bar_count=int(payload["metrics"].get("bars") or 0),
                    warnings=warnings,
                )
            else:
                error = (payload or {}).get("error") or "回测子进程异常退出"
                detail = (payload or {}).get("detail")
                await backtest_service.fail_backtest(backtest_id, str(error), detail)
        except asyncio.CancelledError:
            if self._shutting_down:
                await backtest_service.requeue_backtest(backtest_id)
            else:
                await backtest_service.cancel_backtest_record(backtest_id)
            raise
        except TimeoutError:
            await backtest_service.fail_backtest(
                backtest_id, "行情抓取超时，请缩短区间或稍后重试"
            )
        except MarketDataError as exc:
            await backtest_service.fail_backtest(backtest_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            logger.exception("backtest %s failed", backtest_id)
            await backtest_service.fail_backtest(
                backtest_id, f"回测执行失败：{exc}"
            )
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    async def _ensure_data(
        self,
        backtest_id: str,
        symbol: str,
        granularity: str,
        start_ms: int,
        end_ms: int,
        warmup_bars: int,
    ):
        last_update = 0.0

        async def progress(fetched: int) -> None:
            nonlocal last_update
            now = time.monotonic()
            if now - last_update < 1.0:
                return
            last_update = now
            await backtest_service.update_progress(
                backtest_id,
                {
                    "stage": "fetching",
                    "fetched_bars": fetched,
                    "config": {"warmup_bars": warmup_bars},
                },
            )

        async with asyncio.timeout(settings.backtest_fetch_timeout_seconds):
            return await market_service.ensure_data(
                symbol,
                granularity,
                start_ms,
                end_ms,
                warmup_bars=warmup_bars,
                progress=progress,
            )

    async def cancel(self, backtest_id: str) -> bool:
        try:
            cancelled = await backtest_service.cancel_backtest(backtest_id)
        except LookupError:
            raise
        task = self._tasks.get(backtest_id)
        if task is not None:
            task.cancel()
        return cancelled


async def run_backtest_subprocess(
    *,
    job_id: str,
    symbol: str,
    granularity: str,
    start_ms: int,
    eval_start_ms: int,
    end_open_ms: int,
    warmup_bars: int,
    future_check: bool,
    strategy_code: str,
    initial_capital: float,
    fee_rate: float,
    slippage_rate: float,
) -> tuple[int, dict | None]:
    job_dir = settings.backtests_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    strategy_path = job_dir / "strategy.py"
    strategy_path.write_text(strategy_code, encoding="utf-8")
    output_path = job_dir / "result.json"

    environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "TMPDIR")
        if key in os.environ
    }
    environment.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
    )
    command = [
        sys.executable,
        "-I",
        str(RUNNER_PATH),
        "--db",
        str(settings.market_db_path),
        "--symbol",
        symbol,
        "--granularity",
        granularity,
        "--start-ms",
        str(start_ms),
        "--eval-start-ms",
        str(eval_start_ms),
        "--end-open-ms",
        str(end_open_ms),
        "--warmup-bars",
        str(warmup_bars),
        "--strategy",
        str(strategy_path),
        "--output",
        str(output_path),
        "--initial-capital",
        str(initial_capital),
        "--fee",
        str(fee_rate),
        "--slippage",
        str(slippage_rate),
        "--max-trades",
        str(settings.backtest_max_trades),
        "--equity-points",
        str(settings.backtest_equity_points),
        "--memory-limit-mb",
        str(settings.backtest_memory_limit_mb),
        "--timeout",
        str(settings.backtest_timeout_seconds),
    ]
    if not future_check:
        command.append("--no-future-check")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(job_dir),
        env=environment,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=settings.backtest_timeout_seconds
        )
    except asyncio.TimeoutError:
        kill_process_group(process)
        await process.wait()
        return 1, {"error": "回测执行超时，请缩短区间或简化策略"}
    except asyncio.CancelledError:
        kill_process_group(process)
        await process.wait()
        raise

    payload: dict | None = None
    if output_path.is_file():
        try:
            if output_path.stat().st_size <= MAX_RESULT_BYTES:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            else:
                return 1, {"error": "回测结果过大"}
        except (OSError, ValueError):
            payload = None
    if process.returncode != 0 and payload is None:
        message = stderr.decode("utf-8", errors="replace").strip()[-4000:]
        payload = {
            "error": f"回测子进程异常退出（code {process.returncode}）",
            "detail": message or stdout.decode("utf-8", errors="replace")[-2000:],
        }
    return process.returncode or 0, payload


def cleanup_backtest_dirs() -> None:
    """Remove scratch dirs left by interrupted jobs; queued jobs recreate them."""
    root = settings.backtests_dir
    if not root.is_dir():
        return
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
