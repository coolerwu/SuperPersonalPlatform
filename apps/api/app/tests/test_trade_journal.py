from __future__ import annotations

import json

import pytest

from app.config import settings
from app.services import trade_journal


@pytest.fixture(autouse=True)
def _journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _base(**overrides):
    payload = {
        "symbol": "BTCUSDT",
        "direction": "long",
        "entry_at": "2026-11-02T02:15:00Z",
        "entry_price": 100.0,
        "thesis": "突破后回踩不破",
    }
    payload.update(overrides)
    return payload


async def test_create_open_record_infers_market_and_currency() -> None:
    record = await trade_journal.create_record(**_base())

    assert record["market"] == "crypto"
    assert record["currency"] == "USDT"
    assert record["instrument_type"] == "spot"
    assert record["status"] == "open"
    assert record["return_pct"] is None
    assert record["r_multiple"] is None
    assert record["pnl"] is None
    assert record["id"].startswith("tj_")

    lines = settings.trade_journal_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == record["id"]


async def test_closed_a_share_record_computes_return_r_and_pnl() -> None:
    record = await trade_journal.create_record(
        symbol="600519.SH",
        market="a_share",
        direction="long",
        entry_at="2026-11-02T10:15:00+08:00",
        entry_price=1450.0,
        exit_at="2026-11-03T14:30:00+08:00",
        exit_price=1498.0,
        stop_loss=1418.0,
        quantity=100,
        fees=31.2,
        thesis="日线回踩 20 日线缩量企稳",
        review="按计划持有，冲高减半",
        tags=["setup:趋势回踩", "board:主板"],
        followed_plan=True,
    )

    assert record["market"] == "a_share"
    assert record["currency"] == "CNY"
    assert record["status"] == "closed"
    assert record["return_pct"] == pytest.approx(48 / 1450)
    assert record["r_multiple"] == pytest.approx(48 / 32)
    assert record["pnl"] == pytest.approx(48 * 100 - 31.2)
    assert record["win"] is True
    assert record["warnings"] == []


async def test_short_record_and_quantity_scaling() -> None:
    record = await trade_journal.create_record(
        **_base(
            direction="short",
            entry_price=100.0,
            exit_at="2026-11-02T03:15:00Z",
            exit_price=90.0,
            stop_loss=105.0,
            quantity=2,
            fees=1.0,
        )
    )

    assert record["return_pct"] == pytest.approx(0.10)
    assert record["r_multiple"] == pytest.approx(2.0)
    assert record["pnl"] == pytest.approx(19.0)


async def test_update_and_close_rewrite_atomically() -> None:
    created = await trade_journal.create_record(**_base(quantity=1))
    closed = await trade_journal.close_record(
        created["id"],
        exit_at="2026-11-02T04:15:00Z",
        exit_price=110.0,
        fees=0.5,
        review="按计划止盈",
        tags=["setup:突破", "emotion:平静"],
        followed_plan=True,
    )

    assert closed["id"] == created["id"]
    assert closed["status"] == "closed"
    assert closed["pnl"] == pytest.approx(9.5)
    assert closed["review"] == "按计划止盈"
    assert closed["followed_plan"] is True
    lines = settings.trade_journal_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["review"] == "按计划止盈"


async def test_invalid_fields_are_rejected() -> None:
    with pytest.raises(trade_journal.TradeJournalError, match="thesis"):
        await trade_journal.create_record(**_base(thesis=""))
    with pytest.raises(trade_journal.TradeJournalError, match="entry_price"):
        await trade_journal.create_record(**_base(entry_price=None))
    with pytest.raises(trade_journal.TradeJournalError, match="direction"):
        await trade_journal.create_record(**_base(direction="flat"))
    with pytest.raises(trade_journal.TradeJournalError, match="同时提供"):
        await trade_journal.create_record(**_base(exit_at="2026-11-02T04:15:00Z"))
    with pytest.raises(trade_journal.TradeJournalError, match="不能早于"):
        await trade_journal.create_record(
            **_base(
                exit_at="2026-11-01T04:15:00Z",
                exit_price=110.0,
            )
        )


async def test_query_filters_pagination_and_detail() -> None:
    eth = await trade_journal.create_record(
        **_base(symbol="ETHUSDT", entry_at="2026-11-01T00:00:00Z", tags=["setup:回踩"])
    )
    btc = await trade_journal.create_record(
        **_base(
            symbol="BTCUSDT",
            entry_at="2026-11-02T00:00:00Z",
            exit_at="2026-11-02T05:00:00Z",
            exit_price=105.0,
            tags=["setup:突破"],
        )
    )
    a_share = await trade_journal.create_record(
        **_base(
            symbol="600519.SH",
            market="a_share",
            entry_at="2026-10-01T02:00:00+08:00",
            exit_at="2026-10-02T06:00:00+08:00",
            exit_price=110.0,
            thesis="日线回踩 20 日线缩量企稳",
            tags=["setup:回踩", "board:主板"],
        )
    )

    closed = await trade_journal.query_records(status="closed")
    assert closed["total"] == 2
    assert {item["id"] for item in closed["items"]} == {btc["id"], a_share["id"]}

    by_tag = await trade_journal.query_records(tag="setup:回踩")
    assert {item["id"] for item in by_tag["items"]} == {eth["id"], a_share["id"]}

    searched = await trade_journal.query_records(search="突破后回踩")
    assert searched["total"] == 2

    windowed = await trade_journal.query_records(
        start="2026-10-31", end="2026-11-02", limit=1
    )
    assert windowed["total"] == 2  # date-only end includes 11-02
    assert len(windowed["items"]) == 1

    detail = await trade_journal.query_records(record_id=btc["id"])
    assert detail["items"][0]["symbol"] == "BTCUSDT"
    assert detail["items"][0]["status"] == "closed"

    with pytest.raises(trade_journal.TradeJournalNotFound):
        await trade_journal.query_records(record_id="missing")


async def test_stats_group_by_market_and_tag_and_do_not_mix_currencies() -> None:
    await trade_journal.create_record(
        **_base(
            symbol="ETHUSDT",
            entry_price=100.0,
            exit_at="2026-11-02T04:00:00Z",
            exit_price=110.0,
            quantity=2,
            fees=1.0,
            tags=["setup:回踩"],
        )
    )
    await trade_journal.create_record(
        **_base(
            symbol="600519.SH",
            market="a_share",
            entry_price=100.0,
            exit_at="2026-11-03T04:00:00+08:00",
            exit_price=95.0,
            quantity=100,
            fees=5.0,
            tags=["setup:回踩", "board:主板"],
        )
    )

    result = await trade_journal.stats()
    assert result["total"] == 2
    assert result["count"] == 2
    assert result["win_rate"] == pytest.approx(0.5)
    assert result["avg_return"] == pytest.approx((0.10 - 0.05) / 2)
    assert result["total_pnl"] is None
    assert result["pnl_currencies"] == ["CNY", "USDT"]
    assert {item["market"] for item in result["by_market"]} == {"a_share", "crypto"}
    assert {item["tag"] for item in result["by_tag"]} == {"setup:回踩", "board:主板"}

    crypto_only = await trade_journal.stats(market="crypto")
    assert crypto_only["total_pnl"] == pytest.approx(19.0)
    assert crypto_only["currency"] == "USDT"


async def test_corrupt_lines_are_skipped() -> None:
    valid = await trade_journal.create_record(**_base())
    path = settings.trade_journal_path
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not-json}\n")
        handle.write('{"id": "broken"}\n')
        handle.write(
            '{"id":"bad-price","symbol":"BTCUSDT","direction":"long",'
            '"entry_at":"2026-11-02T00:00:00Z","entry_price":"abc"}\n'
        )

    result = await trade_journal.query_records()
    assert [item["id"] for item in result["items"]] == [valid["id"]]


async def test_a_share_warnings_and_stop_side() -> None:
    record = await trade_journal.create_record(
        **_base(
            symbol="600519",
            market="a_share",
            entry_at="2026-11-02T10:15:00+08:00",
            exit_at="2026-11-02T14:30:00+08:00",
            exit_price=110.0,
            stop_loss=105.0,
            quantity=100.5,
        )
    )

    warnings = " ".join(record["warnings"])
    assert "交易所后缀" in warnings
    assert "T+1" in warnings
    assert "整数股" in warnings
    assert "多单止损" in warnings
