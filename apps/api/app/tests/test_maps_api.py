import json

from fastapi.testclient import TestClient

from app.main import create_app


def _reset(client):
    client.put("/api/maps", json={"clear_api_key": True})


def test_maps_defaults_and_key_masking(client):
    _reset(client)
    assert client.get("/api/maps").json() == {
        "enabled": False,
        "provider": "amap",
        "has_api_key": False,
    }

    response = client.put(
        "/api/maps", json={"enabled": True, "api_key": "  amap-key  "}
    )
    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "provider": "amap",
        "has_api_key": True,
    }
    assert "amap-key" not in response.text
    assert client.get("/api/maps").json()["has_api_key"] is True

    data = json.loads(client.app.state.settings_store.path.read_text("utf-8"))
    assert data["maps"]["api_key"] == "amap-key"
    assert data["maps"]["provider"] == "amap"
    _reset(client)


def test_maps_enable_requires_a_key(client):
    _reset(client)
    response = client.put("/api/maps", json={"enabled": True})
    assert response.status_code == 400
    assert "Web 服务 Key" in response.json()["detail"]


def test_maps_blank_key_keeps_existing(client):
    _reset(client)
    client.put("/api/maps", json={"enabled": True, "api_key": "keep-me"})
    response = client.put("/api/maps", json={"enabled": False, "api_key": "   "})
    assert response.status_code == 200
    assert response.json()["has_api_key"] is True
    data = json.loads(client.app.state.settings_store.path.read_text("utf-8"))
    assert data["maps"]["api_key"] == "keep-me"
    _reset(client)


def test_maps_clear_key_disables_and_wins_over_new_key(client):
    _reset(client)
    client.put("/api/maps", json={"enabled": True, "api_key": "secret"})
    response = client.put(
        "/api/maps",
        json={"clear_api_key": True, "api_key": "ignored", "enabled": True},
    )
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "provider": "amap",
        "has_api_key": False,
    }
    data = json.loads(client.app.state.settings_store.path.read_text("utf-8"))
    assert data["maps"]["api_key"] == ""
    _reset(client)


def test_maps_preserve_other_settings(client):
    client.put("/api/settings", json={"session_retention_days": 11})
    client.put("/api/maps", json={"enabled": False})
    assert client.get("/api/settings").json()["session_retention_days"] == 11
    client.put("/api/settings", json={"session_retention_days": 30})
    _reset(client)


def test_maps_requires_auth():
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        assert unauthenticated.get("/api/maps").status_code == 401
        assert (
            unauthenticated.put(
                "/api/maps", json={"enabled": False}
            ).status_code
            == 401
        )
