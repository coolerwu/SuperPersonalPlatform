import json

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.asr import AsrError


def _reset(client) -> None:
    client.put("/api/asr", json={"clear_api_key": True})


def _fake_wav(seconds: float) -> bytes:
    return b"RIFF" + b"\0" * 40 + b"\0" * int(16000 * 2 * seconds)


def test_asr_defaults_and_key_masking(client) -> None:
    _reset(client)
    assert client.get("/api/asr").json() == {
        "enabled": False,
        "has_api_key": False,
    }

    response = client.put(
        "/api/asr", json={"enabled": True, "api_key": "  deepseek-key  "}
    )
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "has_api_key": True}
    assert "deepseek-key" not in response.text
    assert "deepseek-key" not in client.get("/api/asr").text

    data = json.loads(client.app.state.settings_store.path.read_text("utf-8"))
    assert data["asr"]["api_key"] == "deepseek-key"
    _reset(client)


def test_asr_enable_requires_a_key(client) -> None:
    _reset(client)
    response = client.put("/api/asr", json={"enabled": True})
    assert response.status_code == 400
    assert "DeepSeek API Key" in response.json()["detail"]


def test_asr_clear_key_disables(client) -> None:
    _reset(client)
    client.put("/api/asr", json={"enabled": True, "api_key": "secret"})
    response = client.put("/api/asr", json={"clear_api_key": True})
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "has_api_key": False}
    _reset(client)


def test_asr_transcribe_requires_enabled(client) -> None:
    _reset(client)
    response = client.post(
        "/api/asr/transcribe",
        files={"file": ("recording.webm", b"raw", "audio/webm")},
    )
    assert response.status_code == 409


def test_asr_transcribe_success(client, monkeypatch) -> None:
    _reset(client)
    client.put("/api/asr", json={"enabled": True, "api_key": "asr-key"})

    async def fake_transcode(source: bytes) -> bytes:
        return _fake_wav(1.0)

    monkeypatch.setattr("app.routers.asr.transcode_to_wav", fake_transcode)

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(
            200,
            json={"request_id": "r", "text": "你好", "duration_ms": 1000},
        )

    client.app.state.asr_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        response = client.post(
            "/api/asr/transcribe",
            files={"file": ("recording.webm", b"raw-audio", "audio/webm")},
        )
    finally:
        client.portal.call(client.app.state.asr_client.aclose)
        del client.app.state.asr_client

    assert response.status_code == 200
    assert response.json() == {"text": "你好", "duration_ms": 1000}
    assert seen["auth"] == "Bearer asr-key"
    _reset(client)


def test_asr_transcribe_rejects_long_audio(client, monkeypatch) -> None:
    _reset(client)
    client.put("/api/asr", json={"enabled": True, "api_key": "asr-key"})
    called = False

    async def fake_transcode(source: bytes) -> bytes:
        return _fake_wav(121.0)

    monkeypatch.setattr("app.routers.asr.transcode_to_wav", fake_transcode)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"text": "", "duration_ms": 0})

    client.app.state.asr_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    try:
        response = client.post(
            "/api/asr/transcribe",
            files={"file": ("recording.webm", b"raw-audio", "audio/webm")},
        )
    finally:
        client.portal.call(client.app.state.asr_client.aclose)
        del client.app.state.asr_client

    assert response.status_code == 400
    assert "2 分钟" in response.json()["detail"]
    assert called is False
    _reset(client)


def test_asr_transcribe_rejects_large_upload(client, monkeypatch) -> None:
    _reset(client)
    client.put("/api/asr", json={"enabled": True, "api_key": "asr-key"})
    monkeypatch.setattr("app.routers.asr.MAX_UPLOAD_BYTES", 4)

    response = client.post(
        "/api/asr/transcribe",
        files={"file": ("recording.webm", b"12345", "audio/webm")},
    )
    assert response.status_code == 413
    _reset(client)


def test_asr_transcribe_maps_service_errors(client, monkeypatch) -> None:
    _reset(client)
    client.put("/api/asr", json={"enabled": True, "api_key": "asr-key"})

    async def fake_transcode(source: bytes) -> bytes:
        raise AsrError(503, "服务器未安装 ffmpeg，无法处理语音")

    monkeypatch.setattr("app.routers.asr.transcode_to_wav", fake_transcode)

    response = client.post(
        "/api/asr/transcribe",
        files={"file": ("recording.webm", b"raw", "audio/webm")},
    )
    assert response.status_code == 503
    assert "ffmpeg" in response.json()["detail"]
    _reset(client)


def test_asr_requires_auth() -> None:
    with TestClient(create_app(start_worker=False)) as unauthenticated:
        assert unauthenticated.get("/api/asr").status_code == 401
        assert (
            unauthenticated.put("/api/asr", json={"enabled": False}).status_code
            == 401
        )
        assert (
            unauthenticated.post(
                "/api/asr/transcribe",
                files={"file": ("recording.webm", b"raw", "audio/webm")},
            ).status_code
            == 401
        )
