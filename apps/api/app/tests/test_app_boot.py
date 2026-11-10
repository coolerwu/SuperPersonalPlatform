import asyncio
import os
import stat

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import APP_HOME, settings as app_settings
from app.main import create_app
from app.services import claude_runner, recovery, session_registry
from app.services.backtest_worker import BacktestWorker
from app.services.cron_service import CronRuntime
from app.services.knowledge_sync import KnowledgeSyncRuntime
from app.services.session_service import SessionCleanupRuntime
from app.services.settings_store import get_settings_store
from app.services.weixin_runtime import WeixinRuntime
from app.services.worker import RunWorker


@pytest.fixture
def quiet_background_workers(monkeypatch):
    """Keep startup/shutdown wiring tests off background loops and the network.

    The shared test database may still hold legacy queued Runs from other test
    modules, so the old RunWorker must not start claiming them either.
    """

    async def noop(self):
        return None

    for worker_class in (
        RunWorker,
        WeixinRuntime,
        CronRuntime,
        KnowledgeSyncRuntime,
        BacktestWorker,
        SessionCleanupRuntime,
    ):
        monkeypatch.setattr(worker_class, "start", noop)


def _patch_runtime_classes(monkeypatch, order):
    class FakeRunner:
        def __init__(self, store):
            self.store = store

        async def close_all(self):
            order.append("runner.close_all")

    class FakeRegistry:
        def __init__(self, runner, *, concurrency=5):
            self.runner = runner
            self.concurrency = concurrency

        async def submit(self, turn):
            return turn.id

        async def close(self):
            order.append("registry.close")

    monkeypatch.setattr(claude_runner, "ClaudeTurnRunner", FakeRunner)
    monkeypatch.setattr(session_registry, "SessionRegistry", FakeRegistry)
    return FakeRunner, FakeRegistry


def test_app_boots_with_settings_store(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.app.state.settings_store is not None


def test_settings_file_and_claude_dirs_seeded(client):
    store = get_settings_store()
    assert store.path == app_settings.app_data_dir / "settings.json"
    assert store.path.exists()
    settings = asyncio.run(store.get())
    assert settings.agents[0].id == "default"
    assert (APP_HOME / "claude").is_dir()
    assert (APP_HOME / "tmp").is_dir()
    assert (APP_HOME / "workspaces").is_dir()
    mode = stat.S_IMODE((APP_HOME / "claude").stat().st_mode)
    assert mode == 0o700


def test_worker_app_wires_runtime_state(quiet_background_workers):
    with TestClient(create_app(start_worker=True)) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert os.environ["CLAUDE_CONFIG_DIR"] == str(APP_HOME / "claude")
        state = client.app.state
        assert state.settings_store is not None
        assert state.runner is not None
        assert state.registry is not None
        assert state.session_catalog is not None


def test_shutdown_closes_registry_before_runner(
    quiet_background_workers, monkeypatch
):
    order = []
    _, registry_class = _patch_runtime_classes(monkeypatch, order)

    with TestClient(create_app(start_worker=True)) as client:
        assert client.get("/api/health").json() == {"ok": True}
        assert isinstance(client.app.state.registry, registry_class)

    assert order == ["registry.close", "runner.close_all"]


def test_startup_failure_still_tears_down_runtime(
    quiet_background_workers, monkeypatch
):
    order = []
    market_closed = []
    _patch_runtime_classes(monkeypatch, order)

    async def boom_recovery(registry, store):
        raise RuntimeError("recovery boom")

    async def fake_market_close():
        market_closed.append(True)

    monkeypatch.setattr(recovery, "recover_running_sessions", boom_recovery)
    monkeypatch.setattr(main_module.market_service, "close", fake_market_close)

    with pytest.raises(RuntimeError, match="recovery boom"):
        with TestClient(create_app(start_worker=True)):
            raise AssertionError("startup should have failed")

    assert order == ["registry.close", "runner.close_all"]
    assert market_closed == [True]
