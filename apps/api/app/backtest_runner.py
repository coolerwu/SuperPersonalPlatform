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
import re
import signal
import sys
import tempfile
import traceback
from pathlib import Path

import pandas as pd


def _add_api_root() -> None:
    api_root = Path(__file__).resolve().parents[1]
    if str(api_root) not in sys.path:
        sys.path.insert(0, str(api_root))


_add_api_root()

from app.services.backtest_engine import (  # noqa: E402
    BacktestError,
    coerce_signal_values,
    compare_signal_prefix,
    load_candles,
    prepare_frame,
    run_backtest,
)
from app.services.timeframes import count_bars, floor_open_ms  # noqa: E402


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one crypto backtest")
    parser.add_argument("--db", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--granularity", required=True)
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--eval-start-ms", type=int, default=None)
    parser.add_argument("--end-open-ms", type=int, required=True)
    parser.add_argument("--warmup-bars", type=int, default=0)
    parser.add_argument("--future-check", action=argparse.BooleanOptionalAction, default=True)
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


def _future_check(
    frame: pd.DataFrame,
    strategy_path: Path,
    full_signals: object,
    eval_start_ms: int,
    *,
    enabled: bool,
) -> tuple[dict, list[str]]:
    """Re-run the strategy on a prefix and compare the overlapping signals.

    A causal strategy must return identical signals for the same leading bars.
    Any mismatch means a later bar changed an earlier decision.
    """
    metrics = {
        "future_check_bars": 0,
        "future_check_mismatches": None,
        "future_check_ok": None,
    }
    warnings: list[str] = []
    try:
        source = strategy_path.read_text(encoding="utf-8")
    except OSError:
        source = ""
    if re.search(r"\.shift\(\s*(?:periods\s*=\s*)?-\s*\d", source):
        warnings.append("策略代码包含负向 shift，可能引用未来数据")
    if not enabled:
        return metrics, warnings
    total = len(frame)
    if total < 400:
        warnings.append("样本少于 400 根，未做未来函数检测")
        return metrics, warnings
    eval_offset = int(
        (frame.index < pd.Timestamp(eval_start_ms, unit="ms", tz="UTC")).sum()
    )
    # Bound the second run: a 20k-bar prefix is enough to expose full-sample
    # statistics while keeping the check cheap on 200k-bar backtests.
    check_len = min(
        total - 1,
        max(200, min(total // 2, 20_000), eval_offset + 200),
    )
    if check_len < 120 or check_len >= total:
        return metrics, warnings
    metrics["future_check_bars"] = check_len
    try:
        prefix_strategy = _load_strategy(strategy_path)
        prefix_signals = prefix_strategy(frame.iloc[:check_len].copy())
        full_values = coerce_signal_values(full_signals, total)
        prefix_values = coerce_signal_values(prefix_signals, check_len)
        mismatches = compare_signal_prefix(full_values, prefix_values)
    except Exception as exc:  # noqa: BLE001 - warning, not a failed backtest
        warnings.append(f"未能完成未来函数检测：{exc}")
        return metrics, warnings
    metrics["future_check_mismatches"] = mismatches
    metrics["future_check_ok"] = mismatches == 0
    if mismatches:
        warnings.append(
            f"疑似使用未来数据：用前 {check_len} 根 K 线单独运行时，"
            f"有 {mismatches} 根信号与完整区间不一致，结果不可信"
        )
    return metrics, warnings


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _limit_resources(args.memory_limit_mb)
    if args.timeout > 0:
        signal.alarm(int(args.timeout) + 15)
    output = Path(args.output)
    try:
        eval_start_ms = floor_open_ms(
            args.eval_start_ms if args.eval_start_ms is not None else args.start_ms,
            args.granularity,
        )
        frame = load_candles(
            args.db,
            args.symbol,
            args.granularity,
            args.start_ms,
            args.end_open_ms,
        )
        if len(frame) < 2:
            raise BacktestError("所选区间没有足够的完整 K 线")
        frame = prepare_frame(frame, args.granularity)
        strategy = _load_strategy(Path(args.strategy))
        signals = strategy(frame.copy())
        expected = count_bars(eval_start_ms, args.end_open_ms, args.granularity)
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
            eval_start_ms=eval_start_ms,
            warmup_bars=max(args.warmup_bars, 0),
        )
        future_metrics, future_warnings = _future_check(
            frame,
            Path(args.strategy),
            signals,
            eval_start_ms,
            enabled=args.future_check,
        )
        if result.get("metrics") is not None:
            result["metrics"].update(future_metrics)
        result["warnings"] = future_warnings + list(result.get("warnings") or [])
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
