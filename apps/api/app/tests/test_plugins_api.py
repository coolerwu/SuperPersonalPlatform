from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import plugins


def _catalog() -> plugins.PluginCatalog:
    return plugins.PluginCatalog(
        cli_available=True,
        marketplaces=(
            plugins.MarketplaceInfo(
                name="claude-plugins-official",
                repo=plugins.OFFICIAL_REPO,
                install_location="/tmp/marketplace",
            ),
        ),
        installed=(
            plugins.InstalledPluginInfo(
                id="alpha@claude-plugins-official",
                name="alpha",
                marketplace="claude-plugins-official",
                version="1.0.0",
                enabled=True,
                scope="user",
                installed_at="",
                last_updated="",
            ),
        ),
        available=(
            plugins.AvailablePluginInfo(
                name="beta",
                description="beta plugin",
                author="Anthropic",
                category="productivity",
                homepage="https://example.com",
            ),
        ),
    )


def test_plugins_api_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as anonymous:
        assert anonymous.get("/api/plugins").status_code == 401


def test_plugins_api_returns_catalog(client, monkeypatch) -> None:
    async def fake_catalog(home=None):
        return _catalog()

    monkeypatch.setattr(plugins, "catalog", fake_catalog)

    response = client.get("/api/plugins")

    assert response.status_code == 200
    body = response.json()
    assert body["cli_available"] is True
    assert body["marketplaces"][0]["repo"] == plugins.OFFICIAL_REPO
    assert body["installed"][0]["id"] == "alpha@claude-plugins-official"
    assert body["available"][0]["name"] == "beta"


def test_install_returns_catalog(client, monkeypatch) -> None:
    async def fake_install(name, home=None):
        assert name == "beta"
        return _catalog()

    monkeypatch.setattr(plugins, "install_plugin", fake_install)

    response = client.post("/api/plugins/install", json={"name": "beta"})

    assert response.status_code == 200
    assert response.json()["installed"][0]["name"] == "alpha"


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (plugins.PluginValidationError("bad"), 422),
        (plugins.PluginNotFoundError("missing"), 404),
        (plugins.PluginConflictError("conflict"), 409),
        (plugins.PluginBusyError("busy"), 409),
        (plugins.PluginUnavailableError("no cli"), 503),
        (plugins.PluginCommandError("cli failed"), 502),
    ],
)
def test_plugin_error_mapping(client, monkeypatch, error, status) -> None:
    async def fake_install(name, home=None):
        raise error

    monkeypatch.setattr(plugins, "install_plugin", fake_install)

    response = client.post("/api/plugins/install", json={"name": "beta"})

    assert response.status_code == status
    assert response.json()["detail"] == str(error)


def test_plugin_lifecycle_routes_use_path_id(client, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_enabled(plugin_id, enabled, home=None):
        calls.append(("enabled", f"{plugin_id}:{enabled}"))
        return _catalog()

    async def fake_update(plugin_id, home=None):
        calls.append(("update", plugin_id))
        return _catalog()

    async def fake_uninstall(plugin_id, home=None):
        calls.append(("uninstall", plugin_id))
        return _catalog()

    monkeypatch.setattr(plugins, "set_plugin_enabled", fake_enabled)
    monkeypatch.setattr(plugins, "update_plugin", fake_update)
    monkeypatch.setattr(plugins, "uninstall_plugin", fake_uninstall)

    plugin_id = "alpha@claude-plugins-official"
    assert (
        client.post(f"/api/plugins/{plugin_id}/disable").status_code == 200
    )
    assert client.post(f"/api/plugins/{plugin_id}/enable").status_code == 200
    assert client.post(f"/api/plugins/{plugin_id}/update").status_code == 200
    assert client.delete(f"/api/plugins/{plugin_id}").status_code == 200

    assert calls == [
        ("enabled", f"{plugin_id}:False"),
        ("enabled", f"{plugin_id}:True"),
        ("update", plugin_id),
        ("uninstall", plugin_id),
    ]


def test_marketplace_routes(client, monkeypatch) -> None:
    calls: list[str] = []

    async def fake_add(home=None):
        calls.append("add")
        return _catalog()

    async def fake_update(home=None):
        calls.append("update")
        return _catalog()

    async def fake_remove(home=None):
        calls.append("remove")
        return _catalog()

    monkeypatch.setattr(plugins, "add_official_marketplace", fake_add)
    monkeypatch.setattr(plugins, "update_official_marketplace", fake_update)
    monkeypatch.setattr(plugins, "remove_official_marketplace", fake_remove)

    assert client.post("/api/plugins/marketplace").status_code == 200
    assert client.post("/api/plugins/marketplace/update").status_code == 200
    assert client.delete("/api/plugins/marketplace").status_code == 200
    assert calls == ["add", "update", "remove"]
