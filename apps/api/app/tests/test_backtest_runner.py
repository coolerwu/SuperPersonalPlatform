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


def _make_market_db(path: Path, bars: int = 48) -> None:
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
) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    db_path = tmp_path / "market.db"
    _make_market_db(db_path, bars)
    strategy_path = tmp_path / "strategy.py"
    strategy_path.write_text(strategy_code, encoding="utf-8")
    output_path = tmp_path / "result.json"
    process = subprocess.run(
        [
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
        ],
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
