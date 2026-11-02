from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.services.timeframes import HOUR_MS

RUNNER = Path(__file__).resolve().parents[1] / "backtest_runner.py"
START = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

STRATEGY = """
def strategy(df):
    fast = df["close"].rolling(5).mean()
    slow = df["close"].rolling(20).mean()
    return (fast > slow).astype(float)
"""


def _make_market_db(
    path: Path, bars: int = 48, *, missing: set[int] | None = None
) -> None:
    missing = missing or set()
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE market_candles (
                symbol TEXT, granularity TEXT, open_time_ms INTEGER,
                open REAL, high REAL, low REAL, close REAL,
                base_volume REAL, quote_volume REAL, source TEXT, updated_at TEXT,
                PRIMARY KEY (symbol, granularity, open_time_ms)
            )
            """
        )
        rows = []
        price = 100.0
        for index in range(bars):
            price += 1.0 if index % 3 else -0.5
            if index in missing:
                continue
            rows.append(
                (
                    "BTCUSDT",
                    "1h",
                    START + index * HOUR_MS,
                    price,
                    price + 1,
                    price - 1,
                    price,
                    1.0,
                    100.0,
                    "bitget",
                    "2025-01-01 00:00:00",
                )
            )
        connection.executemany(
            "INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        connection.commit()
    finally:
        connection.close()


def _run(
    tmp_path: Path,
    strategy_code: str,
    *,
    bars: int = 48,
    timeout: int = 60,
    missing: set[int] | None = None,
    eval_start_ms: int | None = None,
    warmup_bars: int = 0,
    future_check: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    db_path = tmp_path / "market.db"
    _make_market_db(db_path, bars, missing=missing)
    strategy_path = tmp_path / "strategy.py"
    strategy_path.write_text(strategy_code, encoding="utf-8")
    output_path = tmp_path / "result.json"
    command = [
        sys.executable,
        "-I",
        str(RUNNER),
        "--db",
        str(db_path),
        "--symbol",
        "BTCUSDT",
        "--granularity",
        "1h",
        "--start-ms",
        str(START),
        "--end-open-ms",
        str(START + bars * HOUR_MS),
        "--strategy",
        str(strategy_path),
        "--output",
        str(output_path),
        "--max-trades",
        "5000",
        "--equity-points",
        "2000",
        "--timeout",
        str(timeout),
        "--warmup-bars",
        str(warmup_bars),
    ]
    if eval_start_ms is not None:
        command.extend(["--eval-start-ms", str(eval_start_ms)])
    if not future_check:
        command.append("--no-future-check")
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout + 30,
    )
    payload = None
    if output_path.is_file():
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    return process, payload


def test_runner_returns_metrics_and_series(tmp_path: Path) -> None:
    process, payload = _run(tmp_path, STRATEGY)
    assert process.returncode == 0, process.stderr
    assert payload is not None
    assert payload["metrics"]["bars"] == 48
    assert payload["equity"]
    assert payload["drawdown"]
    assert payload["candles"]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["trades_total"] >= 1
    assert isinstance(payload["warnings"], list)


def test_runner_reports_strategy_exception(tmp_path: Path) -> None:
    process, payload = _run(
        tmp_path,
        'def strategy(df):\n    raise ValueError("boom")\n',
    )
    assert process.returncode == 2
    assert payload is not None
    assert "策略执行失败" in payload["error"]
    assert "boom" in payload["error"]


def test_runner_requires_strategy_function(tmp_path: Path) -> None:
    process, payload = _run(tmp_path, "value = 1\n")
    assert process.returncode == 2
    assert payload is not None
    assert "strategy(df)" in payload["error"]


def test_runner_reports_internal_gaps(tmp_path: Path) -> None:
    process, payload = _run(
        tmp_path,
        "def strategy(df):\n    return df['close'] * 0 + 1.0\n",
        missing={20},
    )
    assert process.returncode == 0, process.stderr
    assert payload is not None
    assert payload["metrics"]["missing_bars"] == 1
    assert payload["metrics"]["gap_count"] == 1
    assert any("缺少 1 根" in warning for warning in payload["warnings"])


def test_runner_trims_warmup_from_metrics(tmp_path: Path) -> None:
    process, payload = _run(
        tmp_path,
        "def strategy(df):\n    return df['close'] * 0 + 1.0\n",
        eval_start_ms=START + 24 * HOUR_MS,
        warmup_bars=24,
    )
    assert process.returncode == 0, process.stderr
    assert payload is not None
    assert payload["metrics"]["bars"] == 24
    assert payload["metrics"]["warmup_bars"] == 24
    assert payload["metrics"]["start_ms"] == START + 24 * HOUR_MS


def test_runner_flags_future_dependent_strategy(tmp_path: Path) -> None:
    strategy = """
def strategy(df):
    return (df["close"] > df["close"].mean()).astype(float)
"""
    process, payload = _run(tmp_path, strategy, bars=600)
    assert process.returncode == 0, process.stderr
    assert payload is not None
    assert payload["metrics"]["future_check_ok"] is False
    assert payload["metrics"]["future_check_mismatches"] > 0
    assert any("未来数据" in warning for warning in payload["warnings"])


def test_runner_accepts_causal_strategy_in_future_check(tmp_path: Path) -> None:
    process, payload = _run(tmp_path, STRATEGY, bars=600)
    assert process.returncode == 0, process.stderr
    assert payload is not None
    assert payload["metrics"]["future_check_ok"] is True
    assert not any("疑似使用未来数据" in warning for warning in payload["warnings"])
