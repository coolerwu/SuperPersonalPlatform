from __future__ import annotations

import httpx
import pytest

from app.services import stock_quotes
from app.services.stock_quotes import (
    StockQuote,
    StockQuoteError,
    fetch_eastmoney_quote,
    fetch_stock_quote,
    fetch_yahoo_quote,
    parse_eastmoney_quote,
    parse_yahoo_quote,
)


def _yahoo_payload(price: object = 735.29) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "symbol": "SPY",
                        "regularMarketPrice": price,
                        "regularMarketTime": 1793998800,
                        "currency": "USD",
                        "exchangeName": "PCY",
                        "instrumentType": "ETF",
                    }
                }
            ],
            "error": None,
        }
    }


def _eastmoney_payload(price: object = 735290) -> dict:
    return {
        "rc": 0,
        "data": {
            "f43": price,
            "f57": "SPY",
            "f58": "标普500ETF-SPDR",
            "f59": 3,
            "f86": 1793998800,
            "f107": 107,
        },
    }


def test_parse_yahoo_quote() -> None:
    quote = parse_yahoo_quote(_yahoo_payload(), "SPY")
    assert quote.symbol == "SPY"
    assert quote.price == pytest.approx(735.29)
    assert quote.currency == "USD"
    assert quote.exchange == "PCY"
    assert quote.instrument_type == "ETF"
    assert quote.at.startswith("2026-")


def test_parse_yahoo_quote_rejects_missing_price() -> None:
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote(_yahoo_payload(price=None), "SPY")
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote({"chart": {"result": [], "error": None}}, "SPY")
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote(
            {"chart": {"result": None, "error": {"description": "not found"}}},
            "SPY",
        )


def test_parse_eastmoney_quote() -> None:
    quote = parse_eastmoney_quote(_eastmoney_payload(), "SPY", 107)
    assert quote.symbol == "SPY"
    assert quote.price == pytest.approx(735.29)
    assert quote.currency == "USD"
    assert quote.exchange == "AMEX"
    assert quote.at.startswith("2026-")


async def test_fetch_yahoo_quote_uses_chart() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v8/finance/chart/SPY"
        return httpx.Response(200, json=_yahoo_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        quote = await fetch_yahoo_quote("SPY", client=client)
    finally:
        await client.aclose()
    assert quote.price == pytest.approx(735.29)
    assert requests
    assert requests[0].url.params["interval"] == "1d"
    assert requests[0].url.params["range"] == "5d"


async def test_fetch_eastmoney_quote_tries_markets() -> None:
    secids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        secid = request.url.params["secid"]
        secids.append(secid)
        if secid in ("105.SPY", "106.SPY"):
            return httpx.Response(200, json={"rc": 100, "data": None})
        return httpx.Response(200, json=_eastmoney_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        quote = await fetch_eastmoney_quote("SPY", client=client)
    finally:
        await client.aclose()
    assert secids == ["105.SPY", "106.SPY", "107.SPY"]
    assert quote.price == pytest.approx(735.29)


async def test_fetch_stock_quote_prefers_eastmoney(monkeypatch) -> None:
    async def eastmoney(symbol: str) -> StockQuote:
        return StockQuote("SPY", 735.29, "2026-11-06T20:00:00Z", "USD", "AMEX", "US_STOCK")

    async def yahoo(symbol: str) -> StockQuote:
        raise AssertionError("Yahoo should not be called when Eastmoney works")

    monkeypatch.setattr(stock_quotes, "fetch_eastmoney_quote", eastmoney)
    monkeypatch.setattr(stock_quotes, "fetch_yahoo_quote", yahoo)
    quote = await fetch_stock_quote("SPY")
    assert quote.exchange == "AMEX"


async def test_fetch_stock_quote_falls_back_to_yahoo(monkeypatch) -> None:
    async def eastmoney(symbol: str) -> StockQuote:
        raise StockQuoteError("no eastmoney data")

    async def yahoo(symbol: str) -> StockQuote:
        return StockQuote("SPY", 735.29, "2026-11-06T20:00:00Z", "USD", "PCY", "ETF")

    monkeypatch.setattr(stock_quotes, "fetch_eastmoney_quote", eastmoney)
    monkeypatch.setattr(stock_quotes, "fetch_yahoo_quote", yahoo)
    quote = await fetch_stock_quote("SPY")
    assert quote.exchange == "PCY"


async def test_fetch_stock_quote_reports_both_errors(monkeypatch) -> None:
    async def fail(symbol: str) -> StockQuote:
        raise StockQuoteError("down")

    monkeypatch.setattr(stock_quotes, "fetch_eastmoney_quote", fail)
    monkeypatch.setattr(stock_quotes, "fetch_yahoo_quote", fail)
    with pytest.raises(StockQuoteError) as exc_info:
        await fetch_stock_quote("SPY")
    assert "东方财富" in str(exc_info.value)
    assert "Yahoo" in str(exc_info.value)


async def test_fetch_yahoo_quote_reports_http_and_parse_errors() -> None:
    limited = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(429, text="too many")
        )
    )
    try:
        with pytest.raises(StockQuoteError) as exc_info:
            await fetch_yahoo_quote("SPY", client=limited)
        assert "429" in str(exc_info.value)
    finally:
        await limited.aclose()

    blocked = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, text="<html>blocked</html>")
        )
    )
    try:
        with pytest.raises(StockQuoteError):
            await fetch_yahoo_quote("SPY", client=blocked)
    finally:
        await blocked.aclose()
