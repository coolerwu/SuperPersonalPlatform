"""Subprocess entry point for one backtest.

Run as ``python -I /abs/path/apps/api/app/backtest_runner.py ...``. The script
adds ``apps/api`` to ``sys.path`` itself so it does not depend on an editable
install or on the current working directory.
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import json
import signal
import sys
import tempfile
import traceback
from pathlib import Path


def _add_api_root() -> None:
    api_root = Path(__file__).resolve().parents[1]
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))


_add_api_root()

from app.services.backtest_engine import (  # noqa: E402
    BacktestError,
    load_candles,
    run_backtest,
)
from app.services.timeframes import count_bars, floor_open_ms  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one crypto backtest")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--granularity", required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-open-ms", type=int, required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.001)
    parser.add_argument("--slippage", type=float, default=0.0005)
    parser.add_argument("--max-trades", type=int, default=5_000)
    parser.add_argument("--equity-points", type=int, default=2_000)
    parser.add_argument("--memory-limit-mb", type=int, default=2_048)
    parser.add_argument("--timeout", type=int, default=120)
    return parser.parse_args(argv)


def _limit_resources(memory_limit_mb: int) -> None:
    try:
        import resource

        bytes_limit = max(memory_limit_mb, 256) * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
        except (ValueError, OSError):
            pass
    except ImportError:  # pragma: no cover - Windows only
        pass


def _load_strategy(path: Path):
    source = path.read_text(encoding="utf-8")
    namespace: dict[str, object] = {"__name__": "strategy", "__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)  # noqa: S102 - trusted local code
    function = namespace.get("strategy")
    if not callable(function):
        raise BacktestError("策略代码必须定义 strategy(df) 函数")
    return function


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".result-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, allow_nan=False)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _limit_resources(args.memory_limit_mb)
    if args.timeout > 0:
        signal.alarm(int(args.timeout) + 15)
    output = Path(args.output)
    try:
        frame = load_candles(
            args.db,
            args.symbol,
            args.granularity,
            args.start_ms,
            args.end_open_ms,
        )
        if len(frame) < 2:
            raise BacktestError("所选区间没有足够的完整 K 线")
        strategy = _load_strategy(Path(args.strategy))
        signals = strategy(frame.copy())
        expected = count_bars(
            floor_open_ms(args.start_ms, args.granularity),
            args.end_open_ms,
            args.granularity,
        )
        result = run_backtest(
            frame,
            signals,
            initial_capital=args.initial_capital,
            fee_rate=args.fee,
            slippage_rate=args.slippage,
            granularity=args.granularity,
            max_trades=max(args.max_trades, 1),
            max_points=max(args.equity_points, 3),
            expected_bars=expected,
        )
        result["symbol"] = args.symbol
        result["granularity"] = args.granularity
        _write_json(output, result)
        return 0
    except BacktestError as exc:
        _write_json(output, {"error": str(exc), "detail": None})
        return 2
    except Exception as exc:  # noqa: BLE001 - reported to the parent process
        _write_json(
            output,
            {
                "error": f"策略执行失败：{exc}",
                "detail": traceback.format_exc()[-8000:],
            },
        )
        return 2
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
