from __future__ import annotations

import httpx
import pytest

from app.services.stock_quotes import (
    StockQuoteError,
    fetch_stock_quote,
    parse_yahoo_quote,
)


def _payload(price: object = 735.29) -> dict:
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


def test_parse_yahoo_quote() -> None:
    quote = parse_yahoo_quote(_payload(), "SPY")
    assert quote.symbol == "SPY"
    assert quote.price == pytest.approx(735.29)
    assert quote.currency == "USD"
    assert quote.exchange == "PCY"
    assert quote.instrument_type == "ETF"
    assert quote.at.startswith("2026-")


def test_parse_yahoo_quote_rejects_missing_price() -> None:
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote(_payload(price=None), "SPY")
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote({"chart": {"result": [], "error": None}}, "SPY")
    with pytest.raises(StockQuoteError):
        parse_yahoo_quote(
            {"chart": {"result": None, "error": {"description": "not found"}}},
            "SPY",
        )


async def test_fetch_stock_quote_uses_yahoo_chart() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/v8/finance/chart/SPY"
        return httpx.Response(200, json=_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        quote = await fetch_stock_quote("SPY", client=client)
    finally:
        await client.aclose()
    assert quote.price == pytest.approx(735.29)
    assert requests
    assert requests[0].url.params["interval"] == "1d"
    assert requests[0].url.params["range"] == "5d"


async def test_fetch_stock_quote_reports_http_and_parse_errors() -> None:
    limited = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(429, text="too many")
        )
    )
    try:
        with pytest.raises(StockQuoteError) as exc_info:
            await fetch_stock_quote("SPY", client=limited)
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
            await fetch_stock_quote("SPY", client=blocked)
    finally:
        await blocked.aclose()
