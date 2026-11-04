from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_data_dir", tmp_path)


def _payload(**overrides):
    payload = {
        "symbol": "600519.SH",
        "market": "a_share",
        "direction": "long",
        "entry_at": "2026-11-02T10:15:00+08:00",
        "entry_price": 1450.0,
        "stop_loss": 1418.0,
        "quantity": 100,
        "thesis": "日线回踩 20 日线缩量企稳",
        "tags": ["setup:趋势回踩", "board:主板"],
    }
    payload.update(overrides)
    return payload


def test_create_list_get_update_and_close(client) -> None:
    created = client.post("/api/trade-journal", json=_payload())
    assert created.status_code == 201, created.text
    record = created.json()
    record_id = record["id"]
    assert record["market"] == "a_share"
    assert record["currency"] == "CNY"
    assert record["status"] == "open"

    listed = client.get("/api/trade-journal", params={"status": "open"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == record_id

    detail = client.get(f"/api/trade-journal/{record_id}")
    assert detail.status_code == 200
    assert detail.json()["thesis"].startswith("日线回踩")

    updated = client.put(
        f"/api/trade-journal/{record_id}",
        json={"review": "继续观察", "followed_plan": True},
    )
    assert updated.status_code == 200
    assert updated.json()["review"] == "继续观察"
    assert updated.json()["followed_plan"] is True

    closed = client.post(
        f"/api/trade-journal/{record_id}/close",
        json={
            "exit_at": "2026-11-03T14:30:00+08:00",
            "exit_price": 1498.0,
            "fees": 31.2,
            "review": "按计划持有，冲高减半",
        },
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["status"] == "closed"
    assert body["return_pct"] == pytest.approx(48 / 1450)
    assert body["r_multiple"] == pytest.approx(48 / 32)
    assert body["pnl"] == pytest.approx(48 * 100 - 31.2)

    stats = client.get("/api/trade-journal/stats", params={"market": "a_share"})
    assert stats.status_code == 200
    assert stats.json()["count"] == 1
    assert stats.json()["total_pnl"] == pytest.approx(4768.8)
    assert stats.json()["currency"] == "CNY"


def test_filters_and_validation(client) -> None:
    client.post("/api/trade-journal", json=_payload(symbol="BTCUSDT", market="crypto"))
    invalid = client.post(
        "/api/trade-journal",
        json=_payload(direction="flat"),
    )
    assert invalid.status_code == 422

    missing_thesis = client.post(
        "/api/trade-journal",
        json={k: v for k, v in _payload().items() if k != "thesis"},
    )
    assert missing_thesis.status_code == 422

    filtered = client.get("/api/trade-journal", params={"market": "crypto"})
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["symbol"] == "BTCUSDT"


def test_missing_record_returns_404(client) -> None:
    assert client.get("/api/trade-journal/missing").status_code == 404
    assert (
        client.put("/api/trade-journal/missing", json={"review": "x"}).status_code
        == 404
    )
    assert (
        client.post(
            "/api/trade-journal/missing/close",
            json={"exit_at": "2026-11-03T00:00:00Z", "exit_price": 1},
        ).status_code
        == 404
    )
