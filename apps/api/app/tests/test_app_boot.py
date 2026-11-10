def test_app_boots_with_settings_store(client):
    assert client.get("/api/health").json() == {"ok": True}
    assert client.app.state.settings_store is not None


def test_settings_file_and_claude_dirs_seeded(client):
    from app.config import APP_HOME
    from app.services.settings_store import get_settings_store

    store = get_settings_store()
    assert store.path.exists()
    assert (APP_HOME / "claude").is_dir()
    assert (APP_HOME / "tmp").is_dir()
    assert (APP_HOME / "workspaces").is_dir()
