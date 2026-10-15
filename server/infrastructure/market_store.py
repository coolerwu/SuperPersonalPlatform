"""行情快层的本地存储。

K 线和信号事件都落在 `workspace/market/market.sqlite`：
未收盘的那根 K 线会被不断覆盖，正好是快层需要的行为；
事件用自增 seq 作为页面增量轮询的游标。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from server.domain.market import Bar, MarketEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
  exchange TEXT NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  open_time INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL NOT NULL,
  closed INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (exchange, symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  at_ms INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  detail TEXT NOT NULL,
  price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_recent ON events (seq DESC);

CREATE TABLE IF NOT EXISTS event_cooldown (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  at_ms INTEGER NOT NULL,
  PRIMARY KEY (symbol, timeframe, rule_id)
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
  symbol TEXT PRIMARY KEY,
  alias TEXT NOT NULL DEFAULT '',
  added_at TEXT NOT NULL
);
"""

LAST_READ_SEQ_KEY = "events_last_read_seq"


class MarketStore:
    def __init__(self, workspace: Path) -> None:
        self._directory = Path(workspace) / "market"
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path = self._directory / "market.sqlite"
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(self._path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.executescript(_SCHEMA)
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ---------- K 线 ----------

    def upsert_bars(self, exchange: str, symbol: str, timeframe: str, bars: list[Bar]) -> int:
        if not bars:
            return 0
        rows = [
            (
                exchange,
                symbol,
                timeframe,
                bar.open_time,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                1 if bar.closed else 0,
            )
            for bar in bars
        ]
        with self._lock:
            self._connection.executemany(
                "INSERT INTO bars (exchange, symbol, timeframe, open_time, open, high, low, close, volume, closed)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(exchange, symbol, timeframe, open_time)"
                " DO UPDATE SET open=excluded.open, high=excluded.high, low=excluded.low,"
                " close=excluded.close, volume=excluded.volume, closed=excluded.closed",
                rows,
            )
            self._connection.commit()
        return len(rows)

    def load_bars(self, exchange: str, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        with self._lock:
            cursor = self._connection.execute(
                "SELECT open_time, open, high, low, close, volume, closed FROM bars"
                " WHERE exchange = ? AND symbol = ? AND timeframe = ?"
                " ORDER BY open_time DESC LIMIT ?",
                (exchange, symbol, timeframe, max(1, limit)),
            )
            rows = cursor.fetchall()
        return [
            Bar(
                open_time=int(row["open_time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                closed=bool(row["closed"]),
            )
            for row in reversed(rows)
        ]

    def latest_bar_time(self, exchange: str, symbol: str, timeframe: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "SELECT MAX(open_time) AS latest FROM bars WHERE exchange = ? AND symbol = ? AND timeframe = ?",
                (exchange, symbol, timeframe),
            )
            row = cursor.fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else 0

    # ---------- 事件 ----------

    def insert_event(
        self,
        *,
        symbol: str,
        timeframe: str,
        rule_id: str,
        severity: str,
        title: str,
        detail: str,
        price: float,
        at_ms: int | None = None,
    ) -> MarketEvent:
        stamp = int(at_ms if at_ms is not None else time.time() * 1000)
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp / 1000))
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO events (at, at_ms, symbol, timeframe, rule_id, severity, title, detail, price)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (at, stamp, symbol, timeframe, rule_id, severity, title, detail, price),
            )
            self._connection.commit()
            seq = int(cursor.lastrowid or 0)
        return MarketEvent(
            seq=seq,
            at=at,
            symbol=symbol,
            timeframe=timeframe,
            rule_id=rule_id,
            severity=severity,
            title=title,
            detail=detail,
            price=price,
        )

    def list_events(
        self,
        *,
        after_seq: int = 0,
        symbol: str = "",
        limit: int = 100,
    ) -> list[MarketEvent]:
        query = "SELECT * FROM events WHERE seq > ?"
        params: list[object] = [max(0, after_seq)]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY seq DESC LIMIT ?"
        params.append(max(1, limit))
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def last_event_seq(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT MAX(seq) AS latest FROM events").fetchone()
        return int(row["latest"]) if row and row["latest"] is not None else 0

    def on_cooldown(self, symbol: str, timeframe: str, rule_id: str, now_ms: int, cooldown_ms: int) -> bool:
        if cooldown_ms <= 0:
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT at_ms FROM event_cooldown WHERE symbol = ? AND timeframe = ? AND rule_id = ?",
                (symbol, timeframe, rule_id),
            ).fetchone()
        if row is None:
            return False
        return now_ms - int(row["at_ms"]) < cooldown_ms

    def mark_cooldown(self, symbol: str, timeframe: str, rule_id: str, now_ms: int) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO event_cooldown (symbol, timeframe, rule_id, at_ms) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(symbol, timeframe, rule_id) DO UPDATE SET at_ms=excluded.at_ms",
                (symbol, timeframe, rule_id, now_ms),
            )
            self._connection.commit()

    # ---------- 已读游标 ----------

    def last_read_seq(self) -> int:
        value = self.get_meta(LAST_READ_SEQ_KEY)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def mark_all_read(self) -> int:
        latest = self.last_event_seq()
        self.set_meta(LAST_READ_SEQ_KEY, str(latest))
        return latest

    def get_meta(self, key: str) -> str:
        with self._lock:
            row = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
            self._connection.commit()

    # ---------- 自选标的 ----------
    #
    # 自选列表是页面可编辑的运行时状态，落在数据库而不是 config.yaml：
    # 避免在代码里对用户配置文件做保注释的 YAML 手术。
    # `market.symbols` 只作为首次启用时的种子。

    def list_watchlist(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT symbol, alias FROM watchlist ORDER BY added_at, symbol"
            ).fetchall()
        return [(str(row["symbol"]), str(row["alias"])) for row in rows]

    def watchlist_is_empty(self) -> bool:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS total FROM watchlist").fetchone()
        return int(row["total"] or 0) == 0

    def add_watch(self, symbol: str, alias: str, at: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO watchlist (symbol, alias, added_at) VALUES (?, ?, ?)"
                " ON CONFLICT(symbol) DO UPDATE SET alias=excluded.alias",
                (symbol, alias, at),
            )
            self._connection.commit()

    def remove_watch(self, symbol: str) -> bool:
        with self._lock:
            cursor = self._connection.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
            self._connection.commit()
        return bool(cursor.rowcount)

    # ---------- 保留期清理 ----------

    def prune(
        self,
        retention_days: int,
        now_ms: int | None = None,
        min_bars_per_series: int = 0,
    ) -> dict[str, int]:
        """按保留期清理，但每个 (exchange, symbol, timeframe) 至少保留 min_bars_per_series 根。

        没有这个下限时，`1d` 这类长周期会被保留期删到只剩二三十根，而指标需要 200 根
        （EMA200），且增量补数只补最近几根，不会把历史补回来 —— 指标会永久退化。
        """
        stamp = int(now_ms if now_ms is not None else time.time() * 1000)
        cutoff_ms = stamp - max(1, retention_days) * 86400 * 1000
        with self._lock:
            bar_count = 0
            if min_bars_per_series > 0:
                series = self._connection.execute(
                    "SELECT DISTINCT exchange, symbol, timeframe FROM bars"
                ).fetchall()
                for row in series:
                    newest = self._connection.execute(
                        "SELECT open_time FROM bars WHERE exchange = ? AND symbol = ? AND timeframe = ?"
                        " ORDER BY open_time DESC LIMIT 1 OFFSET ?",
                        (row["exchange"], row["symbol"], row["timeframe"], min_bars_per_series - 1),
                    ).fetchone()
                    if newest is None:
                        continue
                    floor_ms = min(cutoff_ms, int(newest["open_time"]))
                    bar_count += self._connection.execute(
                        "DELETE FROM bars WHERE exchange = ? AND symbol = ? AND timeframe = ? AND open_time < ?",
                        (row["exchange"], row["symbol"], row["timeframe"], floor_ms),
                    ).rowcount or 0
            else:
                bar_count = self._connection.execute(
                    "DELETE FROM bars WHERE open_time < ?", (cutoff_ms,)
                ).rowcount or 0
            events = self._connection.execute("DELETE FROM events WHERE at_ms < ?", (cutoff_ms,))
            cool = self._connection.execute("DELETE FROM event_cooldown WHERE at_ms < ?", (cutoff_ms,))
            self._connection.commit()
        return {
            "events": events.rowcount or 0,
            "bars": bar_count,
            "cooldowns": cool.rowcount or 0,
        }

    def vacuum(self) -> None:
        with self._lock:
            self._connection.execute("VACUUM")


def _row_to_event(row: sqlite3.Row) -> MarketEvent:
    return MarketEvent(
        seq=int(row["seq"]),
        at=str(row["at"]),
        symbol=str(row["symbol"]),
        timeframe=str(row["timeframe"]),
        rule_id=str(row["rule_id"]),
        severity=str(row["severity"]),
        title=str(row["title"]),
        detail=str(row["detail"]),
        price=float(row["price"]),
    )
