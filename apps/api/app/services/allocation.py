"""Long-term allocation portfolios with their own cash and position ledger.

This module deliberately does not read the trade journal. The allocation book
and the trading journal are separate ledgers: the journal records short-term
trades with a thesis, while this book records long-term capital, holdings,
target weights and rebalancing drift.

Storage is two local files next to the other runtime data:

- ``allocation.json``: portfolio definitions, targets and manual marks.
- ``allocation-ledger.jsonl``: one cash/position event per line.

Both are rewritten atomically and kept at mode 0600, matching the trade
journal's local-file approach.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import uuid
import weakref
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.market_db import MarketSessionLocal
from app.market_models import MarketCandle
from app.services.market_data import market_service
from app.services.stock_quotes import fetch_stock_quote, fetch_usd_cny_rate

logger = logging.getLogger(__name__)

MAX_PORTFOLIOS = 50
MAX_TARGETS = 200
MAX_MARKS = 200
MAX_ENTRIES = 50_000
MAX_NAME_CHARS = 60
MAX_SYMBOL_CHARS = 32
MAX_NOTE_CHARS = 20_000
MAX_ENTRY_NOTE_CHARS = 2_000
MAX_QUERY_LIMIT = 200
MAX_QUERY_OFFSET = 10_000
MAX_LIVE_SYMBOLS = 20
LIVE_CONCURRENCY = 5
LIVE_TIMEOUT_SECONDS = 5.0
MARK_STALE_SECONDS = 7 * 24 * 3600
CACHE_STALE_SECONDS = 24 * 3600
FX_STALE_SECONDS = 24 * 3600
DEFAULT_USD_CNY = 7.2
MIN_USD_CNY = 0.1
MAX_USD_CNY = 100.0
MIN_XIRR_DAYS = 30
QUANTITY_EPSILON = 1e-9

ENTRY_TYPES = ("deposit", "withdraw", "opening", "buy", "sell", "dividend", "fee")
CASH_ENTRY_TYPES = ("deposit", "withdraw", "dividend", "fee")
TRADE_ENTRY_TYPES = ("opening", "buy", "sell")

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]*$")
_CURRENCY_RE = re.compile(r"^[A-Z0-9]{2,10}$")
_A_SHARE_SYMBOL_RE = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$")
_PORTFOLIO_ID_RE = re.compile(r"^pf_[A-Za-z0-9]{6,32}$")

_LOCKS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


class AllocationError(ValueError):
    """A validation or ledger error the API should show as a 422."""


class AllocationConflict(AllocationError):
    """A request that conflicts with existing ledger data (HTTP 409)."""


class PortfolioNotFound(AllocationError):
    """No portfolio with the requested id."""


class EntryNotFound(AllocationError):
    """No ledger entry with the requested id in the portfolio."""


def _lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[loop] = lock
    return lock


def allocation_path() -> Path:
    return settings.allocation_path


def ledger_path() -> Path:
    return settings.allocation_ledger_path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _now_iso() -> str:
    return _iso(_now())


def _iso_from_ms(value: int) -> str:
    return _iso(datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc))


def _parse_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    else:
        text = str(value or "").strip()
        if not text:
            raise AllocationError(f"{field} 不能为空")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(
                    date.fromisoformat(text), time.min, tzinfo=timezone.utc
                )
            except ValueError as exc:
                raise AllocationError(
                    f"{field} 必须是 ISO 8601 时间（例如 2026-11-02T10:15:00+08:00）"
                ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_time(value: object, field: str) -> str:
    return _iso(_parse_datetime(value, field))


def _text(
    value: object,
    field: str,
    *,
    required: bool = False,
    max_chars: int,
) -> str:
    text = str(value if value is not None else "").strip()
    if required and not text:
        raise AllocationError(f"{field} 不能为空")
    if len(text) > max_chars:
        raise AllocationError(f"{field} 最多 {max_chars} 个字符")
    return text


def _number(
    value: object,
    field: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
    strictly_positive: bool = False,
    default: float | None = None,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if isinstance(value, bool):
        raise AllocationError(f"{field} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AllocationError(f"{field} 必须是数字") from exc
    if not math.isfinite(number):
        raise AllocationError(f"{field} 必须是有限数字")
    if strictly_positive:
        if number <= 0:
            raise AllocationError(f"{field} 必须大于 0")
    elif number < minimum:
        raise AllocationError(f"{field} 不能小于 {minimum}")
    if maximum is not None and number > maximum:
        raise AllocationError(f"{field} 不能大于 {maximum}")
    return number


def _required_number(value: object, field: str, **kwargs: Any) -> float:
    number = _number(value, field, **kwargs)
    if number is None:
        raise AllocationError(f"{field} 不能为空")
    return number


def normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol:
        raise AllocationError("symbol 不能为空")
    if len(symbol) > MAX_SYMBOL_CHARS:
        raise AllocationError(f"symbol 最多 {MAX_SYMBOL_CHARS} 个字符")
    if not _SYMBOL_RE.match(symbol):
        raise AllocationError(
            "symbol 只能包含字母、数字、点、下划线和连字符，例如 BTCUSDT 或 600519.SH"
        )
    return symbol


_BITGET_QUOTES = ("USDT", "USDC", "BTC", "ETH", "BGB")

# Bare tickers that are crypto, so "ETH" is quoted as ETHUSDT while a stock
# ticker like "SPY" is not sent to Bitget at all.
_CRYPTO_BASES = frozenset(
    {
        "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "TRX", "AVAX", "LINK",
        "TON", "SUI", "ARB", "OP", "PEPE", "SHIB", "LTC", "BCH", "DOT", "MATIC",
        "POL", "NEAR", "APT", "ATOM", "UNI", "AAVE", "INJ", "TIA", "SEI", "FTM",
        "ETC", "FIL", "ICP", "HBAR", "STX", "RUNE", "GRT", "SAND", "MANA", "AXS",
        "WLD", "JUP", "ONDO", "ENA", "TAO", "KAS", "BGB",
    }
)


def infer_market(symbol: str) -> str:
    if _A_SHARE_SYMBOL_RE.match(symbol):
        return "a_share"
    text = str(symbol or "").strip().upper()
    for quote in _BITGET_QUOTES:
        if text.endswith(quote) and len(text) > len(quote):
            return "crypto"
    return "crypto" if text in _CRYPTO_BASES else "stock"


def bitget_pair(symbol: str) -> str:
    """Map a bare crypto ticker (ETH) to a Bitget spot pair (ETHUSDT)."""
    text = str(symbol or "").strip().upper()
    for quote in _BITGET_QUOTES:
        if text.endswith(quote) and len(text) > len(quote):
            return text
    return f"{text}USDT"


def normalize_currency(value: object) -> str:
    text = str(value or "").strip().upper() or "USDT"
    if not _CURRENCY_RE.match(text):
        raise AllocationError("currency 必须是 2-10 位大写字母或数字，例如 USDT、CNY")
    return text


def _normalize_portfolio_id(value: object) -> str:
    text = str(value or "").strip()
    if not _PORTFOLIO_ID_RE.match(text):
        raise AllocationError("组合 id 格式不正确")
    return text


def _default_fx() -> dict[str, Any]:
    return {"usd_cny": DEFAULT_USD_CNY, "at": None, "source": "default"}


def _normalize_fx(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _default_fx()
    try:
        rate = float(value.get("usd_cny"))
    except (TypeError, ValueError):
        return _default_fx()
    if not math.isfinite(rate) or not MIN_USD_CNY <= rate <= MAX_USD_CNY:
        return _default_fx()
    at = value.get("at")
    if not isinstance(at, str) or not at.strip():
        at = None
    source = value.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "default"
    return {"usd_cny": rate, "at": at, "source": source}


def empty_config() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "portfolios": [], "fx": _default_fx()}


def _pick(payload: dict[str, Any], existing: dict[str, Any] | None, key: str, default: Any) -> Any:
    if key in payload:
        return payload[key]
    if existing is not None and key in existing:
        return existing[key]
    return default


def _normalize_targets(value: object) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AllocationError("targets 必须是 symbol 到百分比的映射")
    targets: dict[str, float] = {}
    for raw_symbol, raw_target in value.items():
        symbol = normalize_symbol(raw_symbol)
        target = _number(
            raw_target,
            f"targets.{symbol}",
            minimum=0.0,
            maximum=100.0,
        )
        if target is None:
            continue
        targets[symbol] = target
    if len(targets) > MAX_TARGETS:
        raise AllocationError(f"单个组合最多 {MAX_TARGETS} 个目标标的")
    return targets


def _normalize_marks(
    value: object,
    *,
    existing_marks: dict[str, Any],
    now_iso: str,
    honor_times: bool,
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AllocationError("marks 必须是 symbol 到手动价的映射")
    marks: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_mark in value.items():
        symbol = normalize_symbol(raw_symbol)
        raw_as_of: object = None
        if isinstance(raw_mark, dict):
            price = _number(
                raw_mark.get("price"),
                f"marks.{symbol}.price",
                strictly_positive=True,
            )
            if price is None:
                raise AllocationError(f"marks.{symbol}.price 不能为空")
            raw_as_of = raw_mark.get("as_of")
        else:
            price = _number(raw_mark, f"marks.{symbol}.price", strictly_positive=True)
        if price is None:
            continue
        previous = existing_marks.get(symbol) or {}
        same_price = False
        try:
            same_price = previous.get("price") is not None and abs(
                float(previous["price"]) - price
            ) < 1e-12
        except (TypeError, ValueError):
            same_price = False
        if honor_times and raw_as_of:
            as_of = _normalize_time(raw_as_of, f"marks.{symbol}.as_of")
        elif same_price and previous.get("as_of"):
            as_of = str(previous["as_of"])
        else:
            as_of = now_iso
        marks[symbol] = {"price": price, "as_of": as_of}
    if len(marks) > MAX_MARKS:
        raise AllocationError(f"单个组合最多 {MAX_MARKS} 个手动价")
    return marks


def _normalize_portfolio(
    payload: dict[str, Any],
    *,
    existing: dict[str, Any] | None = None,
    now: datetime | None = None,
    honor_mark_times: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AllocationError("组合必须是 JSON 对象")
    now = now or _now()
    now_iso = _iso(now)
    name = _text(
        _pick(payload, existing, "name", "长期组合"),
        "name",
        required=True,
        max_chars=MAX_NAME_CHARS,
    )
    raw_currency = _pick(payload, existing, "currency", None)
    if raw_currency is None:
        raw_currency = (existing or {}).get("currency") or "USDT"
    currency = normalize_currency(raw_currency)
    cash_target = _number(
        _pick(payload, existing, "cash_target_pct", None),
        "cash_target_pct",
        minimum=0.0,
        maximum=100.0,
        default=None,
    )
    band = _number(
        _pick(payload, existing, "rebalance_band_pct", 5.0),
        "rebalance_band_pct",
        minimum=0.0,
        maximum=50.0,
        default=5.0,
    )
    targets = _normalize_targets(_pick(payload, existing, "targets", {}))
    existing_marks = (existing or {}).get("marks") or {}
    marks = _normalize_marks(
        _pick(payload, existing, "marks", {}),
        existing_marks=existing_marks,
        now_iso=now_iso,
        honor_times=honor_mark_times,
    )
    note = _text(
        _pick(payload, existing, "note", ""),
        "note",
        max_chars=MAX_NOTE_CHARS,
    )
    if existing is not None:
        portfolio_id = str(existing["id"])
        created_at = str(existing.get("created_at") or now_iso)
        updated_at = str(existing.get("updated_at") or now_iso)
    else:
        raw_id = payload.get("id")
        portfolio_id = (
            _normalize_portfolio_id(raw_id)
            if raw_id
            else f"pf_{uuid.uuid4().hex[:12]}"
        )
        created_at = str(payload.get("created_at") or now_iso)
        updated_at = str(payload.get("updated_at") or now_iso)
    return {
        "id": portfolio_id,
        "name": name,
        "currency": currency,
        "cash_target_pct": cash_target,
        "rebalance_band_pct": band,
        "targets": targets,
        "marks": marks,
        "note": note,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _normalize_config(data: object) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AllocationError("资产配置文件必须是 JSON 对象")
    raw_version = data.get("version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise AllocationError("资产配置文件 version 必须是整数") from exc
    if version > 1:
        raise AllocationError("资产配置文件版本过新，请升级平台")
    raw_portfolios = data.get("portfolios", [])
    if raw_portfolios is None:
        raw_portfolios = []
    if not isinstance(raw_portfolios, list):
        raise AllocationError("资产配置 portfolios 必须是数组")
    if len(raw_portfolios) > MAX_PORTFOLIOS:
        raise AllocationError(f"最多 {MAX_PORTFOLIOS} 个组合")
    now = _now()
    portfolios: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_portfolios:
        if not isinstance(item, dict) or not item.get("id"):
            raise AllocationError("资产配置组合缺少 id")
        portfolio = _normalize_portfolio(
            item,
            existing=None,
            now=now,
            honor_mark_times=True,
        )
        if portfolio["id"] in seen:
            raise AllocationError(f"组合 id 重复：{portfolio['id']}")
        seen.add(portfolio["id"])
        portfolios.append(portfolio)
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at.strip():
        updated_at = _iso(now)
    return {
        "version": 1,
        "updated_at": updated_at,
        "portfolios": portfolios,
        "fx": _normalize_fx(data.get("fx")),
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _read_config_sync() -> dict[str, Any]:
    path = allocation_path()
    if not path.exists():
        return empty_config()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AllocationError(
            "资产配置文件无法解析，请检查 data/allocation.json"
        ) from exc
    return _normalize_config(data)


def _write_config_sync(config: dict[str, Any]) -> None:
    config["version"] = 1
    config["updated_at"] = _now_iso()
    _atomic_write_text(
        allocation_path(),
        json.dumps(config, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n",
    )


def _position_state() -> dict[str, Any]:
    return {
        "quantity": 0.0,
        "cost": 0.0,
        "realized_pnl": 0.0,
        "dividends": 0.0,
        "fees": 0.0,
        "entry_count": 0,
        "last_entry_at": None,
    }


def _entry_sort_key(entry: dict[str, Any]) -> tuple[float, str, str]:
    at = _parse_datetime(entry.get("at"), "at")
    return (at.timestamp(), str(entry.get("created_at") or ""), str(entry.get("id") or ""))


def _replay(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay a portfolio's ledger in chronological order.

    Raises AllocationError on an oversell. Cash going negative is allowed but
    surfaced as a warning because the user may record purchases before deposits.
    """
    ordered = sorted(entries, key=_entry_sort_key)
    cash = 0.0
    positions: dict[str, dict[str, Any]] = {}
    deposits = 0.0
    withdrawals = 0.0
    opening_cost = 0.0
    realized_pnl = 0.0
    dividends = 0.0
    fees_total = 0.0
    external_flows: list[tuple[datetime, float]] = []
    last_entry_at: str | None = None

    for entry in ordered:
        entry_type = str(entry.get("type") or "")
        at = _parse_datetime(entry.get("at"), "at")
        at_iso = _iso(at)
        last_entry_at = at_iso
        symbol = str(entry.get("symbol") or "").strip().upper() or None

        if entry_type in ("deposit", "withdraw"):
            amount = float(entry["amount"])
            if entry_type == "deposit":
                cash += amount
                deposits += amount
                external_flows.append((at, -amount))
            else:
                cash -= amount
                withdrawals += amount
                external_flows.append((at, amount))
            continue

        if entry_type in ("dividend", "fee"):
            amount = float(entry["amount"])
            if entry_type == "dividend":
                cash += amount
                dividends += amount
                if symbol:
                    position = positions.setdefault(symbol, _position_state())
                    position["dividends"] += amount
                    position["entry_count"] += 1
                    position["last_entry_at"] = at_iso
            else:
                cash -= amount
                fees_total += amount
                if symbol:
                    position = positions.setdefault(symbol, _position_state())
                    position["fees"] += amount
                    position["entry_count"] += 1
                    position["last_entry_at"] = at_iso
            continue

        if entry_type not in TRADE_ENTRY_TYPES or not symbol:
            raise AllocationError(f"流水 {entry.get('id')} 的类型或标的无效")
        quantity = float(entry["quantity"])
        price = float(entry["price"])
        fees = float(entry.get("fees") or 0.0)
        position = positions.setdefault(symbol, _position_state())
        position["entry_count"] += 1
        position["last_entry_at"] = at_iso
        fees_total += fees

        if entry_type in ("opening", "buy"):
            cost_delta = quantity * price + fees
            if entry_type == "opening":
                opening_cost += cost_delta
                external_flows.append((at, -cost_delta))
            else:
                cash -= cost_delta
            position["quantity"] += quantity
            position["cost"] += cost_delta
            continue

        # sell
        available = float(position["quantity"])
        if available + QUANTITY_EPSILON < quantity:
            raise AllocationError(
                f"{at_iso} 卖出 {symbol} {quantity:g} 超过当时持仓 {available:g}"
            )
        average_cost = (
            float(position["cost"]) / available if available > QUANTITY_EPSILON else 0.0
        )
        proceeds = quantity * price - fees
        realized = proceeds - average_cost * quantity
        cash += proceeds
        position["quantity"] = available - quantity
        position["cost"] = float(position["cost"]) - average_cost * quantity
        if position["quantity"] <= QUANTITY_EPSILON:
            position["quantity"] = 0.0
            position["cost"] = 0.0
        realized_pnl += realized
        position["realized_pnl"] += realized

    warnings: list[str] = []
    if cash < -QUANTITY_EPSILON:
        warnings.append(f"现金为负（{cash:.2f}），可能有入金未记录")

    return {
        "cash": cash,
        "positions": positions,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "opening_cost": opening_cost,
        "net_contributions": deposits - withdrawals + opening_cost,
        "realized_pnl": realized_pnl,
        "dividends": dividends,
        "fees": fees_total,
        "external_flows": external_flows,
        "last_entry_at": last_entry_at,
        "warnings": warnings,
    }


def _is_valid_entry(item: object) -> bool:
    if not isinstance(item, dict):
        return False
    if not isinstance(item.get("id"), str) or not item["id"].strip():
        return False
    if not isinstance(item.get("portfolio_id"), str) or not item["portfolio_id"].strip():
        return False
    entry_type = item.get("type")
    if entry_type not in ENTRY_TYPES:
        return False
    try:
        _parse_datetime(item.get("at"), "at")
    except AllocationError:
        return False
    note = item.get("note")
    if note is not None and not isinstance(note, str):
        return False
    try:
        if entry_type in ("deposit", "withdraw", "dividend", "fee"):
            amount = float(item.get("amount"))
            if not math.isfinite(amount) or amount <= 0:
                return False
            if entry_type in ("dividend", "fee") and item.get("symbol") is not None:
                normalize_symbol(item["symbol"])
        else:
            normalize_symbol(item.get("symbol"))
            quantity = float(item.get("quantity"))
            price = float(item.get("price"))
            fees = float(item.get("fees") or 0.0)
            if not all(
                math.isfinite(value)
                for value in (quantity, price, fees)
            ):
                return False
            if quantity <= 0 or price <= 0 or fees < 0:
                return False
    except (TypeError, ValueError, AllocationError):
        return False
    return True


def _read_entries_sync() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning(
                        "allocation ledger: skipping invalid JSON at line %s",
                        line_number,
                    )
                    continue
                if not _is_valid_entry(item):
                    logger.warning(
                        "allocation ledger: skipping invalid entry at line %s",
                        line_number,
                    )
                    continue
                raw_symbol = item.get("symbol")
                if isinstance(raw_symbol, str) and raw_symbol.strip():
                    item["symbol"] = raw_symbol.strip().upper()
                records.append(item)
    except OSError as exc:
        raise AllocationError("无法读取资产配置流水文件") from exc
    by_id: dict[str, dict[str, Any]] = {}
    for item in records:
        by_id[str(item["id"])] = item
    return list(by_id.values())


def _write_entries_sync(entries: list[dict[str, Any]]) -> None:
    lines = [
        json.dumps(
            entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        for entry in entries
    ]
    _atomic_write_text(ledger_path(), "\n".join(lines) + ("\n" if lines else ""))


def _append_entry_sync(entry: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        json.dumps(
            entry, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    )
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError as exc:
        raise AllocationError("无法写入资产配置流水文件") from exc


def _normalize_entry(
    payload: dict[str, Any],
    *,
    portfolio: dict[str, Any],
    existing: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise AllocationError("流水必须是 JSON 对象")
    now = now or _now()
    now_iso = _iso(now)
    entry_type = str(
        _pick(payload, existing, "type", "")
    ).strip().lower()
    if entry_type not in ENTRY_TYPES:
        raise AllocationError(
            "type 必须是 deposit、withdraw、opening、buy、sell、dividend 或 fee"
        )
    at = _parse_datetime(
        _pick(payload, existing, "at", None),
        "at",
    )
    if at > now + timedelta(minutes=5):
        raise AllocationError("流水时间不能晚于当前时间")
    note = _text(
        _pick(payload, existing, "note", ""),
        "note",
        max_chars=MAX_ENTRY_NOTE_CHARS,
    )
    entry: dict[str, Any] = {
        "id": str(existing["id"]) if existing is not None else f"al_{uuid.uuid4().hex[:12]}",
        "portfolio_id": portfolio["id"],
        "type": entry_type,
        "at": _iso(at),
        "note": note,
        "source": "manual",
        "created_at": (
            str(existing.get("created_at") or now_iso)
            if existing is not None
            else now_iso
        ),
        "updated_at": now_iso,
    }
    if entry_type in ("deposit", "withdraw"):
        amount = _required_number(
            _pick(payload, existing, "amount", None),
            "amount",
            strictly_positive=True,
        )
        entry["amount"] = amount
        return entry
    if entry_type in ("dividend", "fee"):
        amount = _required_number(
            _pick(payload, existing, "amount", None),
            "amount",
            strictly_positive=True,
        )
        entry["amount"] = amount
        raw_symbol = _pick(payload, existing, "symbol", None)
        if raw_symbol is not None and str(raw_symbol).strip():
            entry["symbol"] = normalize_symbol(raw_symbol)
        return entry
    entry["symbol"] = normalize_symbol(_pick(payload, existing, "symbol", None))
    entry["quantity"] = _required_number(
        _pick(payload, existing, "quantity", None),
        "quantity",
        strictly_positive=True,
    )
    entry["price"] = _required_number(
        _pick(payload, existing, "price", None),
        "price",
        strictly_positive=True,
    )
    entry["fees"] = _required_number(
        _pick(payload, existing, "fees", 0.0),
        "fees",
        minimum=0.0,
        default=0.0,
    )
    return entry


def _entry_view(entry: dict[str, Any]) -> dict[str, Any]:
    result = dict(entry)
    if entry.get("type") in TRADE_ENTRY_TYPES:
        result["amount"] = float(entry["quantity"]) * float(entry["price"])
    return result


async def load_config() -> dict[str, Any]:
    async with _lock():
        return _read_config_sync()


async def _load_all() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    async with _lock():
        return _read_config_sync(), _read_entries_sync()


async def create_portfolio(payload: dict[str, Any]) -> dict[str, Any]:
    async with _lock():
        config = _read_config_sync()
        if len(config["portfolios"]) >= MAX_PORTFOLIOS:
            raise AllocationError(f"最多 {MAX_PORTFOLIOS} 个组合")
        now = _now()
        portfolio = _normalize_portfolio(
            payload,
            existing=None,
            now=now,
            honor_mark_times=False,
        )
        portfolio["created_at"] = _iso(now)
        portfolio["updated_at"] = _iso(now)
        config["portfolios"].append(portfolio)
        _write_config_sync(config)
        return dict(portfolio)


async def update_portfolio(
    portfolio_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    async with _lock():
        config = _read_config_sync()
        existing = next(
            (item for item in config["portfolios"] if item["id"] == portfolio_id),
            None,
        )
        if existing is None:
            raise PortfolioNotFound(f"找不到资产组合 {portfolio_id}")
        requested_currency = (
            normalize_currency(payload.get("currency"))
            if payload.get("currency") is not None
            else existing["currency"]
        )
        entries = _read_entries_sync()
        has_entries = any(
            entry.get("portfolio_id") == portfolio_id for entry in entries
        )
        if has_entries and requested_currency != existing["currency"]:
            raise AllocationError("组合已有流水，不能修改基础币种")
        now = _now()
        updated = _normalize_portfolio(
            payload,
            existing=existing,
            now=now,
            honor_mark_times=False,
        )
        updated["updated_at"] = _iso(now)
        config["portfolios"] = [
            updated if item["id"] == portfolio_id else item
            for item in config["portfolios"]
        ]
        _write_config_sync(config)
        return dict(updated)


async def delete_portfolio(portfolio_id: str) -> None:
    async with _lock():
        config = _read_config_sync()
        existing = next(
            (item for item in config["portfolios"] if item["id"] == portfolio_id),
            None,
        )
        if existing is None:
            raise PortfolioNotFound(f"找不到资产组合 {portfolio_id}")
        entries = _read_entries_sync()
        if any(entry.get("portfolio_id") == portfolio_id for entry in entries):
            raise AllocationConflict("组合还有流水，不能删除")
        config["portfolios"] = [
            item for item in config["portfolios"] if item["id"] != portfolio_id
        ]
        _write_config_sync(config)


def _find_portfolio(config: dict[str, Any], portfolio_id: str) -> dict[str, Any]:
    portfolio = next(
        (item for item in config["portfolios"] if item["id"] == portfolio_id),
        None,
    )
    if portfolio is None:
        raise PortfolioNotFound(f"找不到资产组合 {portfolio_id}")
    return portfolio


def _find_entry(
    entries: list[dict[str, Any]], portfolio_id: str, entry_id: str
) -> dict[str, Any]:
    entry = next(
        (
            item
            for item in entries
            if item.get("portfolio_id") == portfolio_id and item.get("id") == entry_id
        ),
        None,
    )
    if entry is None:
        raise EntryNotFound(f"找不到流水 {entry_id}")
    return entry


async def list_entries(
    portfolio_id: str, *, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    if not 1 <= limit <= MAX_QUERY_LIMIT:
        raise AllocationError(f"limit 必须在 1-{MAX_QUERY_LIMIT} 之间")
    if not 0 <= offset <= MAX_QUERY_OFFSET:
        raise AllocationError(f"offset 必须在 0-{MAX_QUERY_OFFSET} 之间")
    config, entries = await _load_all()
    _find_portfolio(config, portfolio_id)
    filtered = [item for item in entries if item.get("portfolio_id") == portfolio_id]
    ordered = sorted(filtered, key=_entry_sort_key, reverse=True)
    page = ordered[offset : offset + limit]
    return {
        "total": len(ordered),
        "items": [_entry_view(item) for item in page],
        "offset": offset,
        "limit": limit,
    }


async def create_entry(
    portfolio_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    async with _lock():
        config = _read_config_sync()
        portfolio = _find_portfolio(config, portfolio_id)
        entries = _read_entries_sync()
        if len(entries) >= MAX_ENTRIES:
            raise AllocationError(f"流水超过上限 {MAX_ENTRIES}")
        entry = _normalize_entry(payload, portfolio=portfolio)
        candidate = entries + [entry]
        _replay([item for item in candidate if item.get("portfolio_id") == portfolio_id])
        _append_entry_sync(entry)
        return _entry_view(entry)


async def update_entry(
    portfolio_id: str, entry_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    async with _lock():
        config = _read_config_sync()
        portfolio = _find_portfolio(config, portfolio_id)
        entries = _read_entries_sync()
        existing = _find_entry(entries, portfolio_id, entry_id)
        updated = _normalize_entry(
            payload,
            portfolio=portfolio,
            existing=existing,
        )
        candidate = [
            updated if item.get("id") == entry_id else item for item in entries
        ]
        _replay([item for item in candidate if item.get("portfolio_id") == portfolio_id])
        _write_entries_sync(candidate)
        return _entry_view(updated)


async def delete_entry(portfolio_id: str, entry_id: str) -> None:
    async with _lock():
        config = _read_config_sync()
        _find_portfolio(config, portfolio_id)
        entries = _read_entries_sync()
        _find_entry(entries, portfolio_id, entry_id)
        candidate = [item for item in entries if item.get("id") != entry_id]
        _replay([item for item in candidate if item.get("portfolio_id") == portfolio_id])
        _write_entries_sync(candidate)


def collect_symbols(
    config: dict[str, Any], entries: list[dict[str, Any]]
) -> list[str]:
    symbols: set[str] = set()
    for entry in entries:
        symbol = entry.get("symbol")
        if isinstance(symbol, str) and symbol:
            symbols.add(symbol)
    for portfolio in config.get("portfolios", []):
        symbols.update((portfolio.get("targets") or {}).keys())
        symbols.update((portfolio.get("marks") or {}).keys())
    return sorted(symbols)


async def load_cached_prices(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Latest crypto close per symbol from market.db, regardless of granularity."""
    result: dict[str, dict[str, Any]] = {}
    if not symbols:
        return result
    try:
        async with MarketSessionLocal() as db:
            for symbol in symbols:
                if infer_market(symbol) != "crypto":
                    continue
                row = (
                    await db.execute(
                        select(
                            MarketCandle.open_time_ms,
                            MarketCandle.close,
                        )
                        .where(MarketCandle.symbol == bitget_pair(symbol))
                        .order_by(MarketCandle.open_time_ms.desc())
                        .limit(1)
                    )
                ).first()
                if row is None:
                    continue
                try:
                    price = float(row[1])
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(price) or price <= 0:
                    continue
                result[symbol] = {
                    "price": price,
                    "at": _iso_from_ms(int(row[0])),
                    "source": "cache",
                }
    except Exception:  # noqa: BLE001 - market cache is optional for allocation
        logger.warning("allocation: could not read cached prices", exc_info=True)
    return result


def _unique_live_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    live_items: list[dict[str, str]] = []
    for item in items:
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        market = infer_market(symbol)
        if market not in ("crypto", "stock"):
            continue
        seen.add(symbol)
        live_items.append({"symbol": symbol, "market": market})
    return live_items


async def fetch_live_prices(
    items: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Fetch newest Bitget crypto candles and Yahoo US stock quotes.

    One failed symbol must not fail the page; errors are returned alongside the
    prices that did succeed.
    """
    live_items = _unique_live_items(items)
    if not live_items:
        return {}, []
    prices: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    if len(live_items) > MAX_LIVE_SYMBOLS:
        for item in live_items[MAX_LIVE_SYMBOLS:]:
            errors.append(
                {
                    "symbol": item["symbol"],
                    "error": f"单次最多刷新 {MAX_LIVE_SYMBOLS} 个标的",
                }
            )
        live_items = live_items[:MAX_LIVE_SYMBOLS]
    semaphore = asyncio.Semaphore(LIVE_CONCURRENCY)

    async def one(item: dict[str, str]) -> None:
        symbol = item["symbol"]
        market = item["market"]
        async with semaphore:
            try:
                if market == "crypto":
                    candle = await asyncio.wait_for(
                        market_service.fetch_latest_candle(
                            bitget_pair(symbol), "1min"
                        ),
                        timeout=LIVE_TIMEOUT_SECONDS,
                    )
                    if not candle:
                        errors.append(
                            {"symbol": symbol, "error": "Bitget 返回空数据"}
                        )
                        return
                    price = float(candle["close"])
                    if not math.isfinite(price) or price <= 0:
                        errors.append(
                            {"symbol": symbol, "error": "Bitget 返回无效价格"}
                        )
                        return
                    prices[symbol] = {
                        "price": price,
                        "at": _iso_from_ms(int(candle["open_time_ms"])),
                        "source": "live",
                    }
                    return
                quote = await fetch_stock_quote(symbol)
                prices[symbol] = {
                    "price": quote.price,
                    "at": quote.at,
                    "source": "stock",
                }
            except Exception as exc:  # noqa: BLE001 - per-symbol failure
                errors.append({"symbol": symbol, "error": str(exc)})

    await asyncio.gather(*(one(item) for item in live_items))
    return prices, errors


def compute_xirr(flows: list[tuple[datetime, float]]) -> float | None:
    """Annualized money-weighted return, returned as a percentage.

    Uses bisection over a wide positive/negative range; returns None when the
    cash flows cannot produce a solution.
    """
    cleaned = [
        (when, float(amount))
        for when, amount in flows
        if math.isfinite(float(amount)) and abs(float(amount)) > 1e-12
    ]
    if len(cleaned) < 2:
        return None
    if not (any(amount > 0 for _, amount in cleaned) and any(amount < 0 for _, amount in cleaned)):
        return None
    start = min(when for when, _ in cleaned)
    end = max(when for when, _ in cleaned)
    days = (end - start).total_seconds() / 86400
    if days < 1:
        return None

    def npv(rate: float) -> float:
        total = 0.0
        for when, amount in cleaned:
            exponent = (when - start).total_seconds() / 86400 / 365
            try:
                total += amount / ((1.0 + rate) ** exponent)
            except (OverflowError, ZeroDivisionError):
                return math.inf
        return total

    low, high = -0.9999, 10.0
    low_value = npv(low)
    high_value = npv(high)
    if not (math.isfinite(low_value) and math.isfinite(high_value)):
        return None
    if low_value == 0:
        return low * 100
    if high_value == 0:
        return high * 100
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        middle = (low + high) / 2
        middle_value = npv(middle)
        if abs(middle_value) < 1e-7:
            return middle * 100
        if low_value * middle_value <= 0:
            high = middle
            high_value = middle_value
        else:
            low = middle
            low_value = middle_value
    return ((low + high) / 2) * 100


def _is_older_than(value: object, now: datetime, seconds: int) -> bool:
    if not value:
        return True
    try:
        parsed = _parse_datetime(value, "price time")
    except AllocationError:
        return True
    return (now - parsed).total_seconds() > seconds


def _resolve_price(
    symbol: str,
    *,
    quantity: float,
    cost: float,
    live_prices: dict[str, dict[str, Any]] | None,
    cached_prices: dict[str, dict[str, Any]] | None,
    marks: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    live = (live_prices or {}).get(symbol)
    if live and float(live.get("price") or 0) > 0:
        return {
            "price": float(live["price"]),
            "source": str(live.get("source") or "live"),
            "at": live.get("at"),
            "stale": False,
        }
    mark = marks.get(symbol)
    if mark and float(mark.get("price") or 0) > 0:
        return {
            "price": float(mark["price"]),
            "source": "mark",
            "at": mark.get("as_of"),
            "stale": _is_older_than(mark.get("as_of"), now, MARK_STALE_SECONDS),
        }
    cached = (cached_prices or {}).get(symbol)
    if cached and float(cached.get("price") or 0) > 0:
        return {
            "price": float(cached["price"]),
            "source": "cache",
            "at": cached.get("at"),
            "stale": _is_older_than(cached.get("at"), now, CACHE_STALE_SECONDS),
        }
    if quantity > QUANTITY_EPSILON and cost > 0:
        return {
            "price": cost / quantity,
            "source": "cost",
            "at": None,
            "stale": False,
        }
    return {"price": None, "source": "none", "at": None, "stale": False}


def _snapshot_base(portfolio: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": portfolio["id"],
        "name": portfolio["name"],
        "currency": portfolio["currency"],
        "note": portfolio.get("note") or "",
        "cash": 0.0,
        "total_value": 0.0,
        "holdings_value": 0.0,
        "invested_cost": 0.0,
        "net_contributions": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl": 0.0,
        "dividends": 0.0,
        "fees": 0.0,
        "total_return": 0.0,
        "total_return_pct": None,
        "xirr_pct": None,
        "cash_target_pct": portfolio.get("cash_target_pct"),
        "rebalance_band_pct": portfolio.get("rebalance_band_pct", 5.0),
        "targets": dict(portfolio.get("targets") or {}),
        "marks": dict(portfolio.get("marks") or {}),
        "cash_weight_pct": None,
        "cash_drift_pp": None,
        "cash_rebalance_needed": None,
        "cash_rebalance_amount": None,
        "target_sum_pct": None,
        "unallocated_pct": None,
        "entry_count": 0,
        "last_entry_at": None,
        "positions": [],
        "warnings": [],
        "error": None,
    }


def build_portfolio_snapshot(
    portfolio: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    live_prices: dict[str, dict[str, Any]] | None = None,
    cached_prices: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    base = _snapshot_base(portfolio)
    base["entry_count"] = len(entries)
    if entries:
        try:
            base["last_entry_at"] = _iso(
                max(_parse_datetime(item.get("at"), "at") for item in entries)
            )
        except AllocationError:
            base["last_entry_at"] = None
    try:
        state = _replay(entries)
    except AllocationError as exc:
        base["error"] = str(exc)
        base["warnings"] = [str(exc)]
        return base

    targets = portfolio.get("targets") or {}
    marks = portfolio.get("marks") or {}
    band = float(portfolio.get("rebalance_band_pct") or 0.0)
    symbols = set(state["positions"].keys()) | set(targets.keys())
    positions: list[dict[str, Any]] = []
    warnings: list[str] = list(state["warnings"])

    for symbol in sorted(symbols):
        position = state["positions"].get(symbol) or _position_state()
        target = targets.get(symbol)
        quantity = float(position["quantity"])
        if quantity <= QUANTITY_EPSILON and target is None:
            continue
        cost = float(position["cost"])
        resolved = _resolve_price(
            symbol,
            quantity=quantity,
            cost=cost,
            live_prices=live_prices,
            cached_prices=cached_prices,
            marks=marks,
            now=now,
        )
        price = resolved["price"]
        if quantity <= QUANTITY_EPSILON:
            market_value: float | None = 0.0
        elif price is None:
            market_value = None
        else:
            market_value = quantity * price
        average_cost = cost / quantity if quantity > QUANTITY_EPSILON else None
        if market_value is not None and quantity > QUANTITY_EPSILON:
            unrealized = market_value - cost
            unrealized_pct = (unrealized / cost * 100) if cost > 0 else None
        elif quantity <= QUANTITY_EPSILON:
            unrealized = 0.0
            unrealized_pct = None
        else:
            unrealized = None
            unrealized_pct = None
        position_warnings: list[str] = []
        market = infer_market(symbol)
        price_stale = bool(resolved["stale"]) and quantity > QUANTITY_EPSILON
        if quantity > QUANTITY_EPSILON and price is None:
            position_warnings.append("缺少价格，无法估值")
        if resolved["source"] == "cost" and quantity > QUANTITY_EPSILON:
            position_warnings.append("按成本价估值")
        if price_stale:
            position_warnings.append("价格已过期")
        if market == "stock" and str(portfolio.get("currency") or "").upper() not in (
            "USD",
            "USDT",
        ):
            position_warnings.append("美股行情以 USD 计价，未做汇率换算")
        positions.append(
            {
                "symbol": symbol,
                "market": market,
                "quantity": quantity,
                "avg_cost": average_cost,
                "cost": cost,
                "price": price,
                "price_source": resolved["source"],
                "price_at": resolved["at"],
                "price_stale": price_stale,
                "market_value": market_value,
                "weight_pct": None,
                "target_pct": target,
                "drift_pp": None,
                "rebalance_needed": None,
                "rebalance_amount": None,
                "unrealized_pnl": unrealized,
                "unrealized_pct": unrealized_pct,
                "realized_pnl": float(position["realized_pnl"]),
                "dividends": float(position["dividends"]),
                "fees": float(position["fees"]),
                "entry_count": int(position["entry_count"]),
                "last_entry_at": position["last_entry_at"],
                "warnings": position_warnings,
            }
        )

    held_values = [
        item["market_value"]
        for item in positions
        if item["quantity"] > QUANTITY_EPSILON
    ]
    valuation_complete = all(value is not None for value in held_values)
    holdings_value = sum(value for value in held_values if value is not None)
    cash = float(state["cash"])
    total_value = cash + holdings_value

    if not valuation_complete:
        warnings.append("部分持仓缺少价格，市值和占比不完整")
    if total_value <= QUANTITY_EPSILON:
        warnings.append("总资产为 0 或为负，无法计算占比和再平衡")

    if total_value > QUANTITY_EPSILON:
        for item in positions:
            value = item["market_value"]
            if value is None:
                continue
            weight = value / total_value * 100
            item["weight_pct"] = weight
            target = item["target_pct"]
            if target is None:
                continue
            drift = weight - target
            item["drift_pp"] = drift
            item["rebalance_needed"] = abs(drift) > band
            item["rebalance_amount"] = target / 100 * total_value - value

    cash_target = portfolio.get("cash_target_pct")
    cash_weight = None
    cash_drift = None
    cash_rebalance = None
    cash_rebalance_needed = None
    if total_value > QUANTITY_EPSILON:
        cash_weight = cash / total_value * 100
        if cash_target is not None:
            cash_drift = cash_weight - float(cash_target)
            cash_rebalance_needed = abs(cash_drift) > band
            cash_rebalance = float(cash_target) / 100 * total_value - cash

    target_sum: float | None = None
    unallocated: float | None = None
    if targets or cash_target is not None:
        target_sum = sum(float(value) for value in targets.values()) + float(
            cash_target or 0.0
        )
        unallocated = max(0.0, 100.0 - target_sum)
        if abs(target_sum - 100.0) > 0.5:
            if target_sum > 100.0:
                warnings.append(f"目标合计 {target_sum:.2f}%，超过 100%")
            else:
                warnings.append(
                    f"目标合计 {target_sum:.2f}%，还有 {unallocated:.2f}% 未分配"
                )

    total_return = total_value - float(state["net_contributions"])
    total_return_pct = None
    if float(state["net_contributions"]) > QUANTITY_EPSILON:
        total_return_pct = total_return / float(state["net_contributions"]) * 100

    xirr_pct = None
    flows = list(state["external_flows"])
    if valuation_complete and total_value > QUANTITY_EPSILON and flows:
        xirr_pct = compute_xirr(flows + [(now, total_value)])
        if xirr_pct is None:
            warnings.append("无法计算 XIRR（现金流不足或没有解）")
        else:
            earliest = min(when for when, _ in flows)
            if (now - earliest).days < MIN_XIRR_DAYS:
                warnings.append("样本期不足 30 天，XIRR 仅供参考")

    unrealized_total = sum(
        float(item["unrealized_pnl"])
        for item in positions
        if item["unrealized_pnl"] is not None
    )
    invested_cost = sum(
        float(item["cost"])
        for item in positions
        if item["quantity"] > QUANTITY_EPSILON
    )
    positions.sort(
        key=lambda item: (
            item["market_value"] is None,
            -(item["market_value"] or 0.0),
            item["symbol"],
        )
    )
    warnings = list(dict.fromkeys(warnings))
    return {
        **base,
        "cash": cash,
        "total_value": total_value,
        "holdings_value": holdings_value,
        "invested_cost": invested_cost,
        "net_contributions": float(state["net_contributions"]),
        "unrealized_pnl": unrealized_total,
        "realized_pnl": float(state["realized_pnl"]),
        "dividends": float(state["dividends"]),
        "fees": float(state["fees"]),
        "total_return": total_return,
        "total_return_pct": total_return_pct,
        "xirr_pct": xirr_pct,
        "cash_weight_pct": cash_weight,
        "cash_drift_pp": cash_drift,
        "cash_rebalance_needed": cash_rebalance_needed,
        "cash_rebalance_amount": cash_rebalance,
        "target_sum_pct": target_sum,
        "unallocated_pct": unallocated,
        "entry_count": len(entries),
        "last_entry_at": state["last_entry_at"] or base["last_entry_at"],
        "positions": positions,
        "warnings": warnings,
    }


def _rate_to_usd(currency: object, usd_cny: float) -> float | None:
    """USD and USDT are treated as 1:1; CNY is divided by the USD/CNY rate."""
    code = str(currency or "").strip().upper()
    if code in ("USD", "USDT"):
        return 1.0
    if code == "CNY":
        return 1.0 / usd_cny if usd_cny > 0 else None
    return None


_OVERVIEW_TOTAL_FIELDS = (
    "total_value",
    "cash",
    "holdings_value",
    "invested_cost",
    "net_contributions",
    "unrealized_pnl",
    "realized_pnl",
    "dividends",
    "fees",
)


def build_overview(
    config: dict[str, Any],
    entries: list[dict[str, Any]],
    portfolios: list[dict[str, Any]],
    fx: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    """Convert every portfolio into USD and sum them for the overview tab."""
    usd_cny = float(fx.get("usd_cny") or DEFAULT_USD_CNY)
    totals = {field: 0.0 for field in _OVERVIEW_TOTAL_FIELDS}
    rows: list[dict[str, Any]] = []
    flows: list[tuple[datetime, float]] = []
    warnings: list[str] = []
    valuation_complete = True
    snapshots_by_id = {item["id"]: item for item in portfolios}

    for portfolio in config.get("portfolios", []):
        snapshot_item = snapshots_by_id.get(portfolio["id"])
        if snapshot_item is None:
            continue
        currency = str(portfolio.get("currency") or "").strip().upper()
        rate = _rate_to_usd(currency, usd_cny)
        if rate is None:
            warnings.append(
                f"组合「{portfolio.get('name')}」的币种 {currency} 暂不支持折算，未计入总览"
            )
            continue
        for field in _OVERVIEW_TOTAL_FIELDS:
            value = snapshot_item.get(field)
            if isinstance(value, (int, float)):
                totals[field] += float(value) * rate

        portfolio_entries = [
            entry for entry in entries if entry.get("portfolio_id") == portfolio["id"]
        ]
        try:
            state = _replay(portfolio_entries)
            flows.extend(
                (when, amount * rate) for when, amount in state["external_flows"]
            )
        except AllocationError:
            valuation_complete = False
        if any(
            item["quantity"] > QUANTITY_EPSILON and item["price"] is None
            for item in snapshot_item.get("positions", [])
        ):
            valuation_complete = False

        total_value = float(snapshot_item.get("total_value") or 0.0)
        cash = float(snapshot_item.get("cash") or 0.0)
        rows.append(
            {
                "id": portfolio["id"],
                "name": portfolio.get("name"),
                "currency": currency,
                "rate_to_usd": rate,
                "total_value": total_value,
                "total_value_usd": total_value * rate,
                "cash": cash,
                "cash_usd": cash * rate,
                "weight_pct": None,
            }
        )

    total_value = totals["total_value"]
    net_contributions = totals["net_contributions"]
    total_return = total_value - net_contributions
    total_return_pct = (
        total_return / net_contributions * 100
        if net_contributions > QUANTITY_EPSILON
        else None
    )
    if total_value > QUANTITY_EPSILON:
        for row in rows:
            row["weight_pct"] = row["total_value_usd"] / total_value * 100

    xirr_pct = None
    if valuation_complete and total_value > QUANTITY_EPSILON and flows:
        xirr_pct = compute_xirr(flows + [(now, total_value)])
        if xirr_pct is None:
            warnings.append("无法计算总览 XIRR（现金流不足或没有解）")
        else:
            warnings.append("总览 XIRR 按当前汇率折算历史现金流，仅供参考")

    fx_at = fx.get("at")
    fx_stale = fx.get("source") == "default" or _is_older_than(
        fx_at, now, FX_STALE_SECONDS
    )
    if fx.get("source") == "default" or fx_at is None:
        warnings.append(f"尚未获取实时汇率，按 1 USD ≈ {usd_cny:g} CNY 折算")
    elif fx_stale:
        warnings.append("汇率超过 24 小时未更新，总览为估算值")

    return {
        "display_currency": "USD",
        "usd_cny": usd_cny,
        "fx_at": fx_at,
        "fx_source": fx.get("source") or "default",
        "fx_stale": fx_stale,
        **totals,
        "total_return": total_return,
        "total_return_pct": total_return_pct,
        "xirr_pct": xirr_pct,
        "xirr_approximate": xirr_pct is not None,
        "portfolios": rows,
        "warnings": list(dict.fromkeys(warnings)),
    }


async def get_snapshot(
    *,
    live_prices: dict[str, dict[str, Any]] | None = None,
    price_errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    config, entries = await _load_all()
    symbols = collect_symbols(config, entries)
    cached_prices = await load_cached_prices(symbols)
    portfolios = []
    for portfolio in config.get("portfolios", []):
        portfolio_entries = [
            entry
            for entry in entries
            if entry.get("portfolio_id") == portfolio["id"]
        ]
        portfolios.append(
            build_portfolio_snapshot(
                portfolio,
                portfolio_entries,
                live_prices=live_prices,
                cached_prices=cached_prices,
            )
        )
    now = _now()
    fx = config.get("fx") or _default_fx()
    return {
        "generated_at": _iso(now),
        "fx": fx,
        "overview": build_overview(config, entries, portfolios, fx, now=now),
        "portfolios": portfolios,
        "warnings": [],
        "price_errors": price_errors or [],
    }


def _live_items(
    config: dict[str, Any], entries: list[dict[str, Any]]
) -> list[dict[str, str]]:
    items: dict[str, dict[str, str]] = {}
    for portfolio in config.get("portfolios", []):
        portfolio_entries = [
            entry
            for entry in entries
            if entry.get("portfolio_id") == portfolio["id"]
        ]
        try:
            state = _replay(portfolio_entries)
        except AllocationError:
            continue
        for symbol, position in state["positions"].items():
            if position["quantity"] <= QUANTITY_EPSILON:
                continue
            market = infer_market(symbol)
            if market not in ("crypto", "stock"):
                continue
            items[symbol] = {"symbol": symbol, "market": market}
    return list(items.values())


async def _save_fx(fx: dict[str, Any]) -> None:
    async with _lock():
        config = _read_config_sync()
        config["fx"] = _normalize_fx(fx)
        _write_config_sync(config)


async def refresh_snapshot() -> dict[str, Any]:
    config, entries = await _load_all()
    fx_error: str | None = None
    try:
        quote = await fetch_usd_cny_rate()
        await _save_fx(
            {"usd_cny": quote.rate, "at": quote.at, "source": quote.source}
        )
    except Exception as exc:  # noqa: BLE001 - quotes still refresh without FX
        fx_error = str(exc)
        logger.warning("allocation: USD/CNY rate refresh failed: %s", exc)
    live_prices, errors = await fetch_live_prices(_live_items(config, entries))
    snapshot = await get_snapshot(live_prices=live_prices, price_errors=errors)
    if fx_error:
        snapshot["fx_error"] = fx_error
    return snapshot
