"""Latest US stock quote from Yahoo Finance's public chart endpoint.

The endpoint is an unofficial but keyless source. It is used only by the
long-term allocation book; it is not a trading or real-time feed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

YAHOO_CHART_BASE = "https://query1.finance.yahoo.com"
REQUEST_TIMEOUT_SECONDS = 10.0
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


def parse_yahoo_quote(payload: object, symbol: str) -> StockQuote:
    if not isinstance(payload, dict):
        raise StockQuoteError("美股行情返回格式不正确")
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise StockQuoteError("美股行情返回缺少 chart")
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
        raise StockQuoteError("美股行情返回缺少 meta")
    raw_price = meta.get("regularMarketPrice")
    if raw_price is None:
        raise StockQuoteError(f"Yahoo 没有返回 {symbol} 的最新价")
    try:
        price = float(raw_price)
    except (TypeError, ValueError) as exc:
        raise StockQuoteError(f"Yahoo 返回的 {symbol} 价格无效") from exc
    if price <= 0:
        raise StockQuoteError(f"Yahoo 返回的 {symbol} 价格无效")
    raw_time = meta.get("regularMarketTime")
    if raw_time is None:
        at = _iso(datetime.now(timezone.utc))
    else:
        try:
            at = _iso(datetime.fromtimestamp(int(raw_time), tz=timezone.utc))
        except (TypeError, ValueError, OSError):
            at = _iso(datetime.now(timezone.utc))
    return StockQuote(
        symbol=str(meta.get("symbol") or symbol).upper(),
        price=price,
        at=at,
        currency=str(meta.get("currency") or "USD").upper(),
        exchange=str(meta.get("exchangeName") or ""),
        instrument_type=str(meta.get("instrumentType") or ""),
    )


async def fetch_stock_quote(
    symbol: str, *, client: httpx.AsyncClient | None = None
) -> StockQuote:
    """Fetch the latest quote for a US stock/ETF ticker from Yahoo Finance."""
    text = str(symbol or "").strip().upper()
    if not text:
        raise StockQuoteError("美股代码不能为空")
    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT_SECONDS,
        trust_env=True,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        try:
            response = await http.get(
                f"{YAHOO_CHART_BASE}/v8/finance/chart/{quote(text, safe='')}",
                params={"interval": "1d", "range": "5d"},
            )
        except httpx.ProxyError as exc:
            raise StockQuoteError(
                "美股行情代理不可用，请检查 HTTP_PROXY/HTTPS_PROXY"
            ) from exc
        except httpx.TimeoutException as exc:
            raise StockQuoteError("美股行情请求超时，请稍后重试") from exc
        except httpx.ConnectError as exc:
            raise StockQuoteError(
                "无法连接 Yahoo Finance，请检查服务器网络或代理"
            ) from exc
        except httpx.HTTPError as exc:
            raise StockQuoteError(f"美股行情请求失败：{type(exc).__name__}") from exc

        if response.status_code == 429:
            raise StockQuoteError("Yahoo 限流（HTTP 429），请稍后重试")
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
