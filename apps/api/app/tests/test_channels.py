from __future__ import annotations

import functools
import uuid
from datetime import datetime, timezone

from app.services.claude_runner import transcript_dir
from app.services.settings_store import ConversationSection


def _store(client):
    return client.app.state.settings_store


def _call(client, function, /, *args, **kwargs):
    return client.portal.call(functools.partial(function, *args, **kwargs))


def _create_channel(client, agent_id: str = "default", name: str = "我的微信") -> dict:
    response = client.post(
        "/api/channels", json={"agent_id": agent_id, "name": name}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _bind(client, channel_id: str, external_id: str, display_name: str = "Alice") -> str:
    session_id = str(uuid.uuid4())
    _call(
        client,
        _store(client).update_conversation,
        f"{channel_id}:{external_id}",
        ConversationSection(
            session_id=session_id,
            channel_id=channel_id,
            display_name=display_name,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    return session_id


def _patch_runtime(client, monkeypatch) -> None:
    runtime = client.app.state.weixin

    async def noop(*args, **kwargs):
        return None

    for method in ("start_channel", "stop_channel", "sweep_deliveries"):
        monkeypatch.setattr(runtime, method, noop)


def test_channel_create_and_list_hides_secret(client) -> None:
    created = _create_channel(client)
    assert created["status"] == "pairing"
    assert created["has_credentials"] is False
    assert "bot_token" not in created

    _call(
        client,
        _store(client).update_channel_fields,
        created["id"],
        account_id="acct@im.bot",
        bot_token="top-secret-token",
        base_url="https://ilink.example",
    )
    listed = client.get("/api/channels", params={"agent_id": "default"}).json()
    row = next(item for item in listed if item["id"] == created["id"])
    assert row["has_credentials"] is True
    assert "bot_token" not in row
    assert "top-secret-token" not in str(row)

    assert client.delete(f"/api/channels/{created['id']}").status_code == 200


def test_channel_activation_requires_login(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    created = _create_channel(client)
    assert (
        client.put(
            f"/api/channels/{created['id']}", json={"status": "active"}
        ).status_code
        == 400
    )

    _call(
        client,
        _store(client).update_channel_fields,
        created["id"],
        account_id="acct@im.bot",
        bot_token="token",
    )
    activated = client.put(f"/api/channels/{created['id']}", json={"status": "active"})
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    paused = client.put(f"/api/channels/{created['id']}", json={"status": "paused"})
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"

    client.delete(f"/api/channels/{created['id']}")


def test_channel_conversations_come_from_settings(client) -> None:
    created = _create_channel(client)
    session_id = _bind(client, created["id"], "wx-1")
    other = _create_channel(client, name="另一个微信")
    _bind(client, other["id"], "wx-2", display_name="Bob")

    response = client.get(f"/api/channels/{created['id']}/conversations")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["display_name"] == "Alice"
    assert row["external_id"] == "wx-1"
    assert row["session_id"] == session_id
    assert "context_token" not in row

    client.delete(f"/api/channels/{created['id']}")
    client.delete(f"/api/channels/{other['id']}")


def test_delete_channel_removes_binding_but_keeps_transcript(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    created = _create_channel(client)
    session_id = _bind(client, created["id"], "wx-1")
    transcript = transcript_dir("default") / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("{}", encoding="utf-8")

    response = client.delete(f"/api/channels/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert transcript.is_file()
    settings = _call(client, _store(client).get)
    assert all(item.id != created["id"] for item in settings.channels)
    assert f"{created['id']}:wx-1" not in settings.channel_conversations
    transcript.unlink(missing_ok=True)


def test_agent_permanent_delete_cascades_channels(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    agent = client.post("/api/agents", json={"name": "ChannelAgent"}).json()
    channel = _create_channel(client, agent_id=agent["id"])
    _bind(client, channel["id"], "wx-1")

    assert client.delete(f"/api/agents/{agent['id']}/permanent").status_code == 200
    settings = _call(client, _store(client).get)
    assert all(item.id != channel["id"] for item in settings.channels)
    assert f"{channel['id']}:wx-1" not in settings.channel_conversations
    assert client.get("/api/channels", params={"agent_id": agent["id"]}).status_code == 404


def test_wechat_login_saves_credentials_and_hides_token(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    runtime = client.app.state.weixin
    created = _create_channel(client)

    class FakeClient:
        async def fetch_qr(self):
            return {"qrcode": "qr-1", "qrcode_img_content": "https://example.com/qr"}

        async def qr_status(self, qrcode, base_url=None):
            assert qrcode == "qr-1"
            return {
                "status": "confirmed",
                "ilink_bot_id": "bot-1",
                "bot_token": "secret-token",
                "baseurl": "https://ilink.example",
                "ilink_user_id": "user-1",
            }

        async def aclose(self):
            return None

    monkeypatch.setattr(runtime, "new_client", lambda **kwargs: FakeClient())

    login = client.post(f"/api/channels/{created['id']}/wechat/login")
    assert login.status_code == 200, login.text
    assert login.json()["qrcode"] == "qr-1"
    assert "secret-token" not in login.text

    status = client.get(
        f"/api/channels/{created['id']}/wechat/login/status",
        params={"qrcode": "qr-1"},
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "confirmed"

    listed = client.get("/api/channels", params={"agent_id": "default"}).json()
    row = next(item for item in listed if item["id"] == created["id"])
    assert row["status"] == "active"
    assert row["account_id"] == "bot-1"
    assert row["has_credentials"] is True
    assert "secret-token" not in str(row)

    client.delete(f"/api/channels/{created['id']}")


def test_relogin_clears_credentials(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    created = _create_channel(client)
    _call(
        client,
        _store(client).update_channel_fields,
        created["id"],
        account_id="acct@im.bot",
        bot_token="token",
        status="active",
    )

    response = client.post(f"/api/channels/{created['id']}/wechat/relogin")
    assert response.status_code == 200
    assert response.json()["status"] == "pairing"
    assert response.json()["has_credentials"] is False

    client.delete(f"/api/channels/{created['id']}")


def test_channel_requires_agent(client) -> None:
    response = client.post(
        "/api/channels", json={"agent_id": "missing", "name": "微信"}
    )
    assert response.status_code == 404
    assert (
        client.get("/api/channels", params={"agent_id": "missing"}).status_code == 404
    )


def test_archived_agent_channels_remain_manageable(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    agent = client.post("/api/agents", json={"name": "ArchivedAgent"}).json()
    channel = _create_channel(client, agent_id=agent["id"])

    archived = client.delete(f"/api/agents/{agent['id']}")
    assert archived.status_code == 200

    listed = client.get("/api/channels", params={"agent_id": agent["id"]})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [channel["id"]]

    renamed = client.put(f"/api/channels/{channel['id']}", json={"name": "改名"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "改名"

    assert client.delete(f"/api/channels/{channel['id']}").status_code == 200


def test_paused_agent_channels_remain_manageable(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    agent = client.post("/api/agents", json={"name": "PausedAgent"}).json()
    channel = _create_channel(client, agent_id=agent["id"])
    paused = client.put(f"/api/agents/{agent['id']}", json={"status": "paused"})
    assert paused.status_code == 200

    listed = client.get("/api/channels", params={"agent_id": agent["id"]})
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [channel["id"]]
    assert client.delete(f"/api/channels/{channel['id']}").status_code == 200


def test_archiving_an_agent_releases_its_cached_clients(client, monkeypatch) -> None:
    _patch_runtime(client, monkeypatch)
    agent = client.post("/api/agents", json={"name": "ArchiveMe"}).json()

    class Registry:
        def __init__(self) -> None:
            self.closed_agents: list[str] = []

        def is_busy(self, session_id):
            return False

        def is_agent_busy(self, agent_id):
            return False

        async def close_agent_clients(self, agent_id):
            self.closed_agents.append(agent_id)
            return 1

        async def close_session(self, session_id):
            return None

    registry = Registry()
    client.app.state.registry = registry

    assert client.delete(f"/api/agents/{agent['id']}").status_code == 200
    assert registry.closed_agents == [agent["id"]]
