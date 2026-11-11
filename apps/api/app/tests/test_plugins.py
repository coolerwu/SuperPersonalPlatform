from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import claude_runner, plugins, sdk_bridge


OFFICIAL = "claude-plugins-official"


def _manifest(home: Path, names: list[str]) -> Path:
    manifest_dir = home / "marketplace"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": OFFICIAL,
        "plugins": [
            {
                "name": name,
                "description": f"{name} description",
                "author": {"name": "Anthropic"},
                "category": "productivity",
                "homepage": f"https://example.com/{name}",
            }
            for name in names
        ],
    }
    path = manifest_dir / ".claude-plugin" / "marketplace.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_dir


def _marketplaces(manifest_dir: Path) -> str:
    return json.dumps(
        [
            {
                "name": OFFICIAL,
                "repo": plugins.OFFICIAL_REPO,
                "installLocation": str(manifest_dir),
            },
            {
                "name": "other",
                "repo": "someone/other",
                "installLocation": "/tmp/other",
            },
        ]
    )


def _installed(plugin_id: str, enabled: bool = True) -> str:
    return json.dumps(
        [
            {
                "id": plugin_id,
                "version": "1.0.0",
                "scope": "user",
                "enabled": enabled,
                "installedAt": "2026-01-01T00:00:00Z",
                "lastUpdated": "2026-01-01T00:00:00Z",
            }
        ]
    )


def _patch_cli(
    monkeypatch: pytest.MonkeyPatch,
    *,
    marketplaces: str,
    installed: str,
    calls: list[list[str]],
    fail_on: str = "",
) -> None:
    monkeypatch.setattr(sdk_bridge, "cli_path", lambda: "/fake/claude")

    async def fake_run(args, *, timeout, home=None):
        calls.append(list(args))
        command = " ".join(args)
        if fail_on and command == fail_on:
            raise plugins.PluginCommandError("boom")
        if args[:2] == ["plugin", "marketplace"]:
            return marketplaces
        if args[:2] == ["plugin", "list"]:
            return installed
        return ""

    monkeypatch.setattr(plugins, "run_cli", fake_run)


@pytest.mark.asyncio
async def test_catalog_without_cli(monkeypatch):
    monkeypatch.setattr(sdk_bridge, "cli_path", lambda: None)
    catalog = await plugins.catalog()
    assert catalog.cli_available is False
    assert catalog.errors


@pytest.mark.asyncio
async def test_catalog_parses_marketplace_and_installed(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha", "beta"])
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed=_installed(f"alpha@{OFFICIAL}"),
        calls=calls,
    )

    catalog = await plugins.catalog(home=tmp_path)

    assert catalog.cli_available is True
    assert [item.name for item in catalog.marketplaces] == [OFFICIAL]
    assert [item.id for item in catalog.installed] == [f"alpha@{OFFICIAL}"]
    assert [item.name for item in catalog.available] == ["beta"]
    assert catalog.available[0].author == "Anthropic"
    assert any("非官方集市" in error for error in catalog.errors)
    assert [call[:2] for call in calls] == [
        ["plugin", "marketplace"],
        ["plugin", "list"],
    ]


@pytest.mark.asyncio
async def test_catalog_accepts_git_url_marketplace(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    marketplaces = json.dumps(
        [
            {
                "name": OFFICIAL,
                "source": "git",
                "url": plugins.OFFICIAL_SOURCE_URL,
                "installLocation": str(manifest_dir),
            }
        ]
    )
    _patch_cli(
        monkeypatch,
        marketplaces=marketplaces,
        installed="[]",
        calls=[],
    )

    catalog = await plugins.catalog(home=tmp_path)

    assert [item.repo for item in catalog.marketplaces] == [
        plugins.OFFICIAL_REPO
    ]
    assert [item.name for item in catalog.available] == ["alpha"]


@pytest.mark.asyncio
async def test_catalog_tolerates_broken_manifest(monkeypatch, tmp_path):
    manifest_dir = tmp_path / "marketplace"
    path = manifest_dir / ".claude-plugin" / "marketplace.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=[],
    )

    catalog = await plugins.catalog(home=tmp_path)

    assert catalog.available == ()
    assert any("可安装列表" in error for error in catalog.errors)


@pytest.mark.asyncio
async def test_install_requires_an_available_plugin(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=[],
    )

    with pytest.raises(plugins.PluginNotFoundError):
        await plugins.install_plugin("missing", home=tmp_path)


@pytest.mark.asyncio
async def test_install_uses_user_scope_and_marks_revision(
    monkeypatch, tmp_path
):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=calls,
    )

    await plugins.install_plugin("alpha", home=tmp_path)

    assert [
        "plugin",
        "install",
        f"alpha@{OFFICIAL}",
        "--scope",
        "user",
    ] in calls
    assert (tmp_path / "claude" / ".plugins-revision").is_file()


@pytest.mark.asyncio
async def test_plugin_lifecycle_commands(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    plugin_id = f"alpha@{OFFICIAL}"
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed=_installed(plugin_id),
        calls=calls,
    )

    await plugins.set_plugin_enabled(plugin_id, False, home=tmp_path)
    await plugins.update_plugin(plugin_id, home=tmp_path)
    await plugins.uninstall_plugin(plugin_id, home=tmp_path)

    assert ["plugin", "disable", plugin_id, "--scope", "user"] in calls
    assert ["plugin", "update", plugin_id, "--scope", "user"] in calls
    assert ["plugin", "uninstall", plugin_id, "--scope", "user"] in calls


@pytest.mark.asyncio
async def test_unknown_plugin_is_not_found(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=[],
    )

    with pytest.raises(plugins.PluginNotFoundError):
        await plugins.set_plugin_enabled(f"alpha@{OFFICIAL}", False, home=tmp_path)


@pytest.mark.asyncio
async def test_add_marketplace_uses_https_source(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    _patch_cli(monkeypatch, marketplaces="[]", installed="[]", calls=calls)

    await plugins.add_official_marketplace(home=tmp_path)

    assert [
        "plugin",
        "marketplace",
        "add",
        plugins.OFFICIAL_SOURCE_URL,
        "--scope",
        "user",
    ] in calls


@pytest.mark.asyncio
async def test_marketplace_add_is_conflict_when_present(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, [])
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=[],
    )

    with pytest.raises(plugins.PluginConflictError):
        await plugins.add_official_marketplace(home=tmp_path)


@pytest.mark.asyncio
async def test_remove_marketplace_blocked_by_installed_plugins(
    monkeypatch, tmp_path
):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed=_installed(f"alpha@{OFFICIAL}"),
        calls=[],
    )

    with pytest.raises(plugins.PluginConflictError):
        await plugins.remove_official_marketplace(home=tmp_path)


@pytest.mark.asyncio
async def test_install_surfaces_cli_failure(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, ["alpha"])
    calls: list[list[str]] = []
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=calls,
        fail_on=f"plugin install alpha@{OFFICIAL} --scope user",
    )

    with pytest.raises(plugins.PluginCommandError):
        await plugins.install_plugin("alpha", home=tmp_path)


@pytest.mark.asyncio
async def test_mutation_lock_times_out_as_busy(monkeypatch, tmp_path):
    manifest_dir = _manifest(tmp_path, [])
    _patch_cli(
        monkeypatch,
        marketplaces=_marketplaces(manifest_dir),
        installed="[]",
        calls=[],
    )
    monkeypatch.setattr(plugins, "LOCK_WAIT_SECONDS", 0.05)
    lock = plugins._lock()
    await lock.acquire()
    try:
        with pytest.raises(plugins.PluginBusyError):
            await plugins.catalog(home=tmp_path)
    finally:
        lock.release()


def test_plugin_id_validation():
    with pytest.raises(plugins.PluginValidationError):
        plugins._parse_plugin_id("../../etc/passwd")
    with pytest.raises(plugins.PluginValidationError):
        plugins._validate_plugin_name("--scope")


def test_plugin_stamp_tracks_plugin_state(tmp_path):
    assert claude_runner._plugin_stamp(tmp_path) == "||"
    config = tmp_path / "claude"
    (config / "plugins").mkdir(parents=True)
    (config / "settings.json").write_text("{}", encoding="utf-8")
    first = claude_runner._plugin_stamp(tmp_path)
    assert first != "||"
    (config / ".plugins-revision").write_text("1", encoding="utf-8")
    assert claude_runner._plugin_stamp(tmp_path) != first


def test_config_fingerprint_includes_plugin_stamp():
    agent = SimpleNamespace(
        instructions="",
        provider=SimpleNamespace(
            base_url="https://example.com", model="m", api_key="k"
        ),
    )
    assert claude_runner._config_fingerprint(
        agent, "memory", "plugins-a"
    ) != claude_runner._config_fingerprint(
        agent, "memory", "plugins-b"
    )
