"""Latest US stock quote from Eastmoney (primary) and Yahoo Finance (fallback).

Eastmoney's push2 quote endpoint works directly from mainland China without an
API key; Yahoo's chart endpoint is used as a fallback when Eastmoney has no
quote for a symbol. Neither is a real-time trading feed.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

EASTMONEY_QUOTE_BASE = "https://push2.eastmoney.com"
EASTMONEY_MARKETS = (105, 106, 107)
EASTMONEY_EXCHANGES = {105: "NASDAQ", 106: "NYSE", 107: "AMEX"}
# Eastmoney's offshore USD/CNH quote; used as an approximation of USD/CNY.
EASTMONEY_FX_SECID = "133.USDCNH"
FUND_NAV_BASE = "https://fundmobapi.eastmoney.com"
YAHOO_CHART_BASE = "https://query1.finance.yahoo.com"
REQUEST_TIMEOUT_SECONDS = 8.0
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class StockQuoteError(Exception):
    """A user-facing failure while fetching a US stock quote."""


@dataclass(frozen=True)
class StockQuote:
    symbol: str
    price: float
    at: str
    currency: str
    exchange: str
    instrument_type: str


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _timestamp_iso(value: object) -> str:
    if value is None:
        return _iso(datetime.now(timezone.utc))
    try:
        return _iso(datetime.fromtimestamp(int(value), tz=timezone.utc))
    except (TypeError, ValueError, OSError):
        return _iso(datetime.now(timezone.utc))


def _client(*, trust_env: bool) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        trust_env=trust_env,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )


def parse_yahoo_quote(payload: object, symbol: str) -> StockQuote:
    if not isinstance(payload, dict):
        raise StockQuoteError("Yahoo 美股行情返回格式不正确")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise StockQuoteError("Yahoo 美股行情返回缺少 chart")
    error = chart.get("error")
    if error:
        if isinstance(error, dict):
            description = error.get("description") or error.get("code") or "未知错误"
        else:
            description = str(error)
        raise StockQuoteError(f"Yahoo 返回错误：{description}")
    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise StockQuoteError(f"Yahoo 没有返回 {symbol} 的行情")
    result = results[0] if isinstance(results[0], dict) else {}
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise StockQuoteError("Yahoo 美股行情返回缺少 meta")
    raw_price = meta.get("regularMarketPrice")
    if raw_price is None:
        raise StockQuoteError(f"Yahoo 没有返回 {symbol} 的最新价")
    try:
        price = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise StockQuoteError(f"Yahoo 返回的 {symbol} 价格无效") from exc
    if price <= 0:
        raise StockQuoteError(f"Yahoo 返回的 {symbol} 价格无效")
    return StockQuote(
        symbol=str(meta.get("symbol") or symbol).upper(),
        price=price,
        at=_timestamp_iso(meta.get("regularMarketTime")),
        currency=str(meta.get("currency") or "USD").upper(),
        exchange=str(meta.get("exchangeName") or ""),
        instrument_type=str(meta.get("instrumentType") or ""),
    )


def parse_eastmoney_quote(payload: object, symbol: str, market: int) -> StockQuote:
    if not isinstance(payload, dict):
        raise StockQuoteError("东方财富美股行情返回格式不正确")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise StockQuoteError(f"东方财富没有找到 {symbol} 的美股行情")
    raw_price = data.get("f43")
    try:
        raw = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise StockQuoteError(f"东方财富返回的 {symbol} 价格无效") from exc
    try:
        digits = int(data.get("f59") or 3)
    except (TypeError, ValueError):
        digits = 3
    if not 0 <= digits <= 6:
        digits = 3
    price = raw / (10**digits)
    if price <= 0:
        raise StockQuoteError(f"东方财富返回的 {symbol} 价格无效")
    return StockQuote(
        symbol=str(data.get("f57") or symbol).upper(),
        price=price,
        at=_timestamp_iso(data.get("f86")),
        currency="USD",
        exchange=EASTMONEY_EXCHANGES.get(market, "US"),
        instrument_type="US_STOCK",
    )


async def fetch_eastmoney_quote(
    symbol: str, *, client: httpx.AsyncClient | None = None
) -> StockQuote:
    text = str(symbol or "").strip().upper()
    if not text:
        raise StockQuoteError("美股代码不能为空")
    owns_client = client is None
    http = client or _client(trust_env=False)
    try:
        for market in EASTMONEY_MARKETS:
            try:
                response = await http.get(
                    f"{EASTMONEY_QUOTE_BASE}/api/qt/stock/get",
                    params={
                        "secid": f"{market}.{text}",
                        "fields": "f43,f57,f58,f59,f86,f107",
                    },
                )
            except httpx.TimeoutException as exc:
                raise StockQuoteError("东方财富美股行情请求超时") from exc
            except httpx.HTTPError as exc:
                raise StockQuoteError(
                    f"东方财富美股行情请求失败：{type(exc).__name__}"
                ) from exc
            if response.status_code == 429:
                raise StockQuoteError("东方财富限流（HTTP 429）")
            if response.status_code >= 400:
                raise StockQuoteError(
                    f"东方财富返回 HTTP {response.status_code}"
                )
            try:
                payload = response.json()
            except (ValueError, TypeError) as exc:
                raise StockQuoteError("东方财富返回了无法解析的数据") from exc
            if not isinstance(payload.get("data"), dict):
                continue
            return parse_eastmoney_quote(payload, text, market)
    finally:
        if owns_client:
            await http.aclose()
    raise StockQuoteError(f"东方财富没有找到 {text} 的美股行情")


async def fetch_yahoo_quote(
    symbol: str, *, client: httpx.AsyncClient | None = None
) -> StockQuote:
    text = str(symbol or "").strip().upper()
    if not text:
        raise StockQuoteError("美股代码不能为空")
    owns_client = client is None
    http = client or _client(trust_env=True)
    try:
        try:
            response = await http.get(
                f"{YAHOO_CHART_BASE}/v8/finance/chart/{quote(text, safe='')}",
                params={"interval": "1d", "range": "5d"},
            )
        except httpx.ProxyError as exc:
            raise StockQuoteError(
                "Yahoo 美股行情代理不可用，请检查 HTTP_PROXY/HTTPS_PROXY"
            ) from exc
        except httpx.TimeoutException as exc:
            raise StockQuoteError("Yahoo 美股行情请求超时") from exc
        except httpx.ConnectError as exc:
            raise StockQuoteError(
                "无法连接 Yahoo Finance，请检查服务器网络或代理"
            ) from exc
        except httpx.HTTPError as exc:
            raise StockQuoteError(
                f"Yahoo 美股行情请求失败：{type(exc).__name__}"
            ) from exc

        if response.status_code == 429:
            raise StockQuoteError("Yahoo 限流（HTTP 429）")
        if response.status_code >= 400:
            raise StockQuoteError(f"Yahoo 返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise StockQuoteError(
                "Yahoo 返回了无法解析的数据；如果服务器在中国大陆，请检查 HTTP_PROXY/HTTPS_PROXY"
            ) from exc
        return parse_yahoo_quote(payload, text)
    finally:
        if owns_client:
            await http.aclose()


async def fetch_stock_quote(symbol: str) -> StockQuote:
    """Try Eastmoney first (direct), then Yahoo via the configured proxy."""
    errors: list[str] = []
    try:
        return await fetch_eastmoney_quote(symbol)
    except StockQuoteError as exc:
        errors.append(f"东方财富：{exc}")
    try:
        return await fetch_yahoo_quote(symbol)
    except StockQuoteError as exc:
        errors.append(f"Yahoo：{exc}")
    raise StockQuoteError("美股行情获取失败：" + "；".join(errors))


@dataclass(frozen=True)
class FxQuote:
    """USD/CNY exchange rate; ``rate`` is CNY per 1 USD."""

    pair: str
    rate: float
    at: str
    source: str


def parse_eastmoney_fx(payload: object) -> FxQuote:
    if not isinstance(payload, dict):
        raise StockQuoteError("东方财富汇率返回格式不正确")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise StockQuoteError("东方财富没有返回 USD/CNY 汇率")
    try:
        raw = float(data.get("f43"))
        digits = int(data.get("f59") or 4)
    except (TypeError, ValueError) as exc:
        raise StockQuoteError("东方财富返回的汇率无效") from exc
    if not 0 <= digits <= 6:
        digits = 4
    rate = raw / (10**digits)
    if not math.isfinite(rate) or not 0.1 <= rate <= 100:
        raise StockQuoteError("东方财富返回的汇率无效")
    return FxQuote(
        pair="USD/CNY",
        rate=rate,
        at=_timestamp_iso(data.get("f86")),
        source="eastmoney",
    )


async def fetch_eastmoney_fx(
    *, client: httpx.AsyncClient | None = None
) -> FxQuote:
    owns_client = client is None
    http = client or _client(trust_env=False)
    try:
        try:
            response = await http.get(
                f"{EASTMONEY_QUOTE_BASE}/api/qt/stock/get",
                params={
                    "secid": EASTMONEY_FX_SECID,
                    "fields": "f43,f57,f58,f59,f86",
                },
            )
        except httpx.TimeoutException as exc:
            raise StockQuoteError("东方财富汇率请求超时") from exc
        except httpx.HTTPError as exc:
            raise StockQuoteError(f"东方财富汇率请求失败：{type(exc).__name__}") from exc
        if response.status_code == 429:
            raise StockQuoteError("东方财富限流（HTTP 429）")
        if response.status_code >= 400:
            raise StockQuoteError(f"东方财富返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise StockQuoteError("东方财富返回了无法解析的数据") from exc
        return parse_eastmoney_fx(payload)
    finally:
        if owns_client:
            await http.aclose()


async def fetch_usd_cny_rate() -> FxQuote:
    """Fetch USD/CNY from Eastmoney (direct) with a Yahoo fallback."""
    errors: list[str] = []
    try:
        return await fetch_eastmoney_fx()
    except StockQuoteError as exc:
        errors.append(f"东方财富：{exc}")
    try:
        quote = await fetch_yahoo_quote("CNY=X")
        if not math.isfinite(quote.price) or not 0.1 <= quote.price <= 100:
            raise StockQuoteError("Yahoo 返回的汇率无效")
        return FxQuote(
            pair="USD/CNY",
            rate=quote.price,
            at=quote.at,
            source="yahoo",
        )
    except StockQuoteError as exc:
        errors.append(f"Yahoo：{exc}")
    raise StockQuoteError("USD/CNY 汇率获取失败：" + "；".join(errors))


async def fetch_fund_nav(
    code: str, *, client: httpx.AsyncClient | None = None
) -> StockQuote:
    """Latest published NAV for an open-end fund from Eastmoney."""
    text = str(code or "").strip()
    if not (len(text) == 6 and text.isdigit()):
        raise StockQuoteError("基金代码必须是 6 位数字")
    owns_client = client is None
    http = client or _client(trust_env=False)
    try:
        try:
            response = await http.get(
                f"{FUND_NAV_BASE}/FundMNewApi/FundMNFInfo",
                params={
                    "Fcodes": text,
                    "plat": "Android",
                    "appType": "ttjj",
                    "product": "EFund",
                    "Version": "1",
                    "deviceid": "1",
                },
            )
        except httpx.TimeoutException as exc:
            raise StockQuoteError("东方财富基金净值请求超时") from exc
        except httpx.HTTPError as exc:
            raise StockQuoteError(
                f"东方财富基金净值请求失败：{type(exc).__name__}"
            ) from exc
        if response.status_code == 429:
            raise StockQuoteError("东方财富限流（HTTP 429）")
        if response.status_code >= 400:
            raise StockQuoteError(f"东方财富返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except (ValueError, TypeError) as exc:
            raise StockQuoteError("东方财富返回了无法解析的数据") from exc
        rows = payload.get("Datas") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise StockQuoteError(f"东方财富没有找到基金 {text} 的净值")
        data = rows[0]
        try:
            nav = float(data.get("NAV"))
        except (TypeError, ValueError) as exc:
            raise StockQuoteError(f"基金 {text} 的净值无效") from exc
        if not math.isfinite(nav) or nav <= 0:
            raise StockQuoteError(f"基金 {text} 的净值无效")
        day = str(data.get("PDATE") or "").strip()
        at = (
            f"{day}T00:00:00Z"
            if day
            else _iso(datetime.now(timezone.utc))
        )
        return StockQuote(
            symbol=text,
            price=nav,
            at=at,
            currency="CNY",
            exchange="FUND",
            instrument_type="FUND",
        )
    finally:
        if owns_client:
            await http.aclose()
