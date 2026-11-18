# 网页语音输入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在网页聊天输入框旁加入麦克风按钮：录音后由后端 ffmpeg 转码并代理 DeepSeek ASR，把转写文字追加到当前草稿。

**Architecture:** 新增 `asr` 配置节与 `/api/asr` 路由；后端 `services/asr.py` 负责 ffmpeg 转码与 DeepSeek 调用；前端在 `ChatPanel` 内做录音状态机与草稿追加，配置页新增「语音识别」分区。

**Tech Stack:** FastAPI + httpx（已有）；ffmpeg/ffprobe（生产已装，本地需安装）；React 18 + TypeScript + Vite；无新增 Python/npm 依赖。

**Spec:** `docs/superpowers/specs/2026-11-18-voice-input-asr-design.md`

---

## 开工前检查（共享 checkout）

本仓库可能同时有其它会话在改同一份工作区。开始前：

- [ ] **Step 0.1: 确认工作区状态**

Run: `cd /Users/wulang/Desktop/AI/SuperPersonalPlatform && git status --short`

Expected: 记录所有未提交文件。本任务只会修改下表列出的文件；**属于其它会话的未提交改动（例如 `ArtifactGrid.tsx`、与语音无关的 `styles.css`/`CLAUDE.md`/`intent.md` 段落）不要动、不要提交**。

- [ ] **Step 0.2: 本计划内每个提交只用定向暂存**

后续所有 `git commit` 步骤都使用列出的具体路径，禁止 `git add .`。如果 `npm run build` 会把你没写过的前端改动打进 `apps/web/dist`，先停下来与用户确认，等那些改动落盘后再构建。

## 文件结构

| 文件 | 责任 |
| --- | --- |
| `apps/api/app/services/settings_store.py`（改） | `AsrSection`、`PlatformSettings.asr`、`update_asr` |
| `apps/api/app/services/asr.py`（新） | ffmpeg 转码、WAV 时长、DeepSeek ASR 调用与错误映射 |
| `apps/api/app/routers/asr.py`（新） | 配置读写与转写接口 |
| `apps/api/app/schemas.py`（改） | `AsrConfigUpdate` |
| `apps/api/app/main.py`（改） | 注册 asr 路由 |
| `apps/api/app/tests/test_settings_store.py`（改） | `update_asr` 行为 |
| `apps/api/app/tests/test_asr_service.py`（新） | 服务层单元测试 |
| `apps/api/app/tests/test_asr_api.py`（新） | 接口测试 |
| `apps/web/src/types.ts`（改） | `AsrConfig`、`TranscribeResult`、`SettingsTab` 增加 `asr` |
| `apps/web/src/api.ts`（改） | 三个 API 方法 |
| `apps/web/src/components/AsrSettings.tsx`（新） | 配置页卡片 |
| `apps/web/src/components/SettingsPage.tsx`（改） | 「语音识别」卡片 |
| `apps/web/src/components/Sidebar.tsx`（改） | 侧栏配置分区入口 |
| `apps/web/src/components/ChatPanel.tsx`（改） | 录音按钮、状态机、转写、追加草稿 |
| `apps/web/src/styles.css`（改） | 录音按钮与状态文字样式 |
| `docs/intent.md`、`CLAUDE.md`、`README.md`（改） | 需求与使用文档 |

---

## Task 1: 后端配置节（TDD）

**Files:**
- Modify: `apps/api/app/services/settings_store.py`
- Test: `apps/api/app/tests/test_settings_store.py`

- [ ] **Step 1: 追加失败测试**

在 `apps/api/app/tests/test_settings_store.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_update_asr_returns_state_and_enforces_key(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)

    with pytest.raises(ValueError):
        await store.update_asr(enabled=True)
    assert not path.exists()

    section = await store.update_asr(api_key="  key-a  ")
    assert section.api_key == "key-a"
    assert section.enabled is False

    section = await store.update_asr(enabled=True)
    assert section.enabled is True
    assert section.api_key == "key-a"

    section = await store.update_asr(api_key="key-b")
    assert section.enabled is True
    assert section.api_key == "key-b"

    section = await store.update_asr(clear_api_key=True)
    assert section.enabled is False
    assert section.api_key == ""
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py::test_update_asr_returns_state_and_enforces_key -v`

Expected: FAIL，`AttributeError: 'SettingsStore' object has no attribute 'update_asr'`。

- [ ] **Step 3: 实现 `AsrSection` 与 `update_asr`**

在 `AsrSection` 之前的位置（`MapsSection` 之后）插入：

```python
class AsrSection(BaseModel):
    enabled: bool = False
    api_key: str = ""
```

在 `PlatformSettings` 的 `maps` 字段之后插入：

```python
    asr: AsrSection = Field(default_factory=AsrSection)
```

在 `update_maps` 方法之后插入：

```python
    async def update_asr(
        self,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> AsrSection:
        """Apply a speech-recognition update under the store lock."""
        result = AsrSection()

        def mutate(data: dict[str, Any]) -> None:
            nonlocal result
            raw = _dict_section(data, "asr")
            current = _section(AsrSection, raw)
            if clear_api_key:
                fields: dict[str, Any] = {"api_key": "", "enabled": False}
            else:
                fields = {}
                if api_key is not None and api_key.strip():
                    fields["api_key"] = api_key.strip()
                if enabled is not None:
                    fields["enabled"] = bool(enabled)
                next_key = str(fields.get("api_key", current.api_key) or "").strip()
                next_enabled = bool(fields.get("enabled", current.enabled))
                if next_enabled and not next_key:
                    raise ValueError("请先填写 DeepSeek API Key")
            raw.update(_validated_update(AsrSection, fields))
            result = _section(AsrSection, raw)

        await self._json.update(mutate)
        return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_settings_store.py::test_update_asr_returns_state_and_enforces_key -v`

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add apps/api/app/services/settings_store.py apps/api/app/tests/test_settings_store.py
git commit -m "Add ASR settings section

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: ASR 服务层（TDD）

**Files:**
- Create: `apps/api/app/services/asr.py`
- Test: `apps/api/app/tests/test_asr_service.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_asr_service.py`：

```python
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import asr
from app.services.asr import (
    ASR_URL,
    AsrError,
    transcode_to_wav,
    transcribe_wav,
    wav_duration_seconds,
)


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"wav-bytes",
        stderr: bytes = b"",
        error: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._error = error
        self.killed = False

    async def communicate(self, source: bytes) -> tuple[bytes, bytes]:
        if self._error is not None:
            raise self._error
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def test_wav_duration_seconds() -> None:
    one_second = b"RIFF" + b"\0" * 40 + b"\0" * 32000
    assert wav_duration_seconds(one_second) == pytest.approx(1.0)
    assert wav_duration_seconds(b"") == 0.0


@pytest.mark.asyncio
async def test_transcode_success_uses_expected_ffmpeg_args(monkeypatch) -> None:
    calls: dict[str, object] = {}
    process = _FakeProcess(stdout=b"RIFF-wav")

    async def fake_exec(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)

    result = await transcode_to_wav(b"raw-audio")

    assert result == b"RIFF-wav"
    argv = calls["args"]
    assert argv[0] == "ffmpeg"
    assert "-ac" in argv and "1" in argv
    assert "-ar" in argv and "16000" in argv
    assert calls["kwargs"]["stdin"] == asyncio.subprocess.PIPE


@pytest.mark.asyncio
async def test_transcode_missing_ffmpeg(monkeypatch) -> None:
    async def fake_exec(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(AsrError) as info:
        await transcode_to_wav(b"raw-audio")

    assert info.value.status_code == 503
    assert "ffmpeg" in info.value.detail


@pytest.mark.asyncio
async def test_transcode_rejects_bad_audio(monkeypatch) -> None:
    process = _FakeProcess(returncode=1)

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(AsrError) as info:
        await transcode_to_wav(b"raw-audio")

    assert info.value.status_code == 400
    assert "无法解析" in info.value.detail


@pytest.mark.asyncio
async def test_transcode_timeout_kills_process(monkeypatch) -> None:
    process = _FakeProcess(error=asyncio.TimeoutError())

    async def fake_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asr.asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(AsrError) as info:
        await transcode_to_wav(b"raw-audio", timeout=0.01)

    assert info.value.status_code == 504
    assert process.killed is True


@pytest.mark.asyncio
async def test_transcribe_success() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={"request_id": "r-1", "text": "你好", "duration_ms": 1200},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await transcribe_wav(b"wav-bytes", "key-1", client=client)

    assert result == {"text": "你好", "duration_ms": 1200}
    assert seen["url"] == ASR_URL
    assert seen["auth"] == "Bearer key-1"
    assert 'filename="speech.wav"' in seen["body"].decode("latin-1")


@pytest.mark.asyncio
async def test_transcribe_allows_empty_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"request_id": "r", "text": "", "duration_ms": 0})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await transcribe_wav(b"wav-bytes", "key", client=client)

    assert result == {"text": "", "duration_ms": 0}


@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (400, "语音识别无法处理这段音频"),
        (408, "音频上传超时，请重试"),
        (413, "录音文件过大"),
        (429, "语音识别服务繁忙，请稍后再试"),
        (503, "语音识别服务暂不可用"),
        (504, "语音识别超时，请重试"),
    ],
)
@pytest.mark.asyncio
async def test_transcribe_maps_status(status: int, detail: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == status
    assert info.value.detail == detail


@pytest.mark.asyncio
async def test_transcribe_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == 502


@pytest.mark.asyncio
async def test_transcribe_rejects_bad_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": 1})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == 502
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_asr_service.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'app.services.asr'`。

- [ ] **Step 3: 实现服务**

创建 `apps/api/app/services/asr.py`：

```python
from __future__ import annotations

import asyncio

import httpx

ASR_URL = "https://api.deepseek.com/v1/tools/asr"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_WAV_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120.5
FFMPEG_TIMEOUT_SECONDS = 30.0
WAV_HEADER_BYTES = 44
BYTES_PER_SECOND = 16000 * 2

_STATUS_MESSAGES = {
    400: "语音识别无法处理这段音频",
    408: "音频上传超时，请重试",
    413: "录音文件过大",
    429: "语音识别服务繁忙，请稍后再试",
    503: "语音识别服务暂不可用",
    504: "语音识别超时，请重试",
}


class AsrError(Exception):
    """An ASR failure that maps directly to an HTTP response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def transcode_to_wav(
    source: bytes, *, timeout: float = FFMPEG_TIMEOUT_SECONDS
) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-sample_fmt",
            "s16",
            "-f",
            "wav",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise AsrError(503, "服务器未安装 ffmpeg，无法处理语音") from exc
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(source), timeout=timeout
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise AsrError(504, "音频转码超时") from exc
    if process.returncode != 0:
        raise AsrError(400, "无法解析这段录音")
    return stdout


def wav_duration_seconds(wav: bytes) -> float:
    return max(0.0, (len(wav) - WAV_HEADER_BYTES) / BYTES_PER_SECOND)


async def transcribe_wav(
    wav: bytes, api_key: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, object]:
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        response = await client.post(
            ASR_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("speech.wav", wav, "audio/wav")},
        )
    except httpx.TimeoutException as exc:
        raise AsrError(504, "语音识别超时，请重试") from exc
    except httpx.HTTPError as exc:
        raise AsrError(502, "语音识别服务返回异常") from exc
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code != 200:
        known = response.status_code in _STATUS_MESSAGES
        raise AsrError(
            response.status_code if known else 502,
            _STATUS_MESSAGES.get(response.status_code, "语音识别服务返回异常"),
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise AsrError(502, "语音识别服务返回异常") from exc
    text = payload.get("text")
    duration_ms = payload.get("duration_ms")
    if not isinstance(text, str) or not isinstance(duration_ms, int):
        raise AsrError(502, "语音识别服务返回异常")
    return {"text": text, "duration_ms": duration_ms}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_asr_service.py -v`

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add apps/api/app/services/asr.py apps/api/app/tests/test_asr_service.py
git commit -m "Add DeepSeek ASR service with ffmpeg transcoding

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: ASR 路由与注册（TDD）

**Files:**
- Create: `apps/api/app/routers/asr.py`
- Modify: `apps/api/app/schemas.py`
- Modify: `apps/api/app/main.py`
- Test: `apps/api/app/tests/test_asr_api.py`

- [ ] **Step 1: 写失败测试**

创建 `apps/api/app/tests/test_asr_api.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_asr_api.py -v`

Expected: FAIL，`ModuleNotFoundError: No module named 'app.routers.asr'` 或 404。

- [ ] **Step 3: 实现 schema 与路由**

在 `apps/api/app/schemas.py` 的 `MapsConfigUpdate` 之后插入：

```python
class AsrConfigUpdate(BaseModel):
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=200)
    clear_api_key: bool = False
```

创建 `apps/api/app/routers/asr.py`：

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from app.auth import require_auth
from app.schemas import AsrConfigUpdate
from app.services.asr import (
    MAX_SECONDS,
    MAX_UPLOAD_BYTES,
    MAX_WAV_BYTES,
    AsrError,
    transcode_to_wav,
    transcribe_wav,
    wav_duration_seconds,
)
from app.services.settings_store import (
    AsrSection,
    SettingsStore,
    get_settings_store,
)

router = APIRouter(
    prefix="/api/asr", tags=["asr"], dependencies=[Depends(require_auth)]
)


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings_store", None)
    return store if store is not None else get_settings_store()


def _config_out(section: AsrSection) -> dict[str, object]:
    return {
        "enabled": bool(section.enabled),
        "has_api_key": bool((section.api_key or "").strip()),
    }


@router.get("")
async def get_asr(request: Request) -> dict[str, object]:
    return _config_out((await _store(request).get()).asr)


@router.put("")
async def update_asr(
    payload: AsrConfigUpdate, request: Request
) -> dict[str, object]:
    try:
        section = await _store(request).update_asr(
            enabled=payload.enabled,
            api_key=payload.api_key,
            clear_api_key=payload.clear_api_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _config_out(section)


@router.post("/transcribe")
async def transcribe_audio(
    request: Request, file: UploadFile = File(...)
) -> dict[str, object]:
    section = (await _store(request).get()).asr
    if not section.enabled or not (section.api_key or "").strip():
        raise HTTPException(
            status_code=409,
            detail="语音识别未启用，请先在配置页填写 DeepSeek API Key",
        )
    source = await file.read()
    if len(source) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="录音文件过大")
    try:
        wav = await transcode_to_wav(source)
        if wav_duration_seconds(wav) > MAX_SECONDS:
            raise AsrError(400, "录音超过 2 分钟")
        if len(wav) > MAX_WAV_BYTES:
            raise AsrError(413, "录音文件过大")
        client = getattr(request.app.state, "asr_client", None)
        return await transcribe_wav(wav, section.api_key.strip(), client=client)
    except AsrError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
```

- [ ] **Step 4: 注册路由**

在 `apps/api/app/main.py` 的 import 区，`app_settings` import 之后插入：

```python
from app.routers.asr import router as asr_router
```

在 `app.include_router(app_settings_router)` 之后插入：

```python
    app.include_router(asr_router)
```

- [ ] **Step 5: 运行接口测试确认通过**

Run: `.venv/bin/python -m pytest apps/api/app/tests/test_asr_api.py -v`

Expected: 全部 PASS。`TestClient` 在当前 Starlette 版本提供 `.portal`，`client.portal.call(client.app.state.asr_client.aclose)` 可在同步测试中关闭注入的异步客户端。

- [ ] **Step 6: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add apps/api/app/routers/asr.py apps/api/app/schemas.py apps/api/app/main.py apps/api/app/tests/test_asr_api.py
git commit -m "Add ASR config and transcription routes

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: 前端类型、API 与配置页

**Files:**
- Modify: `apps/web/src/types.ts`
- Modify: `apps/web/src/api.ts`
- Create: `apps/web/src/components/AsrSettings.tsx`
- Modify: `apps/web/src/components/SettingsPage.tsx`
- Modify: `apps/web/src/components/Sidebar.tsx`

- [ ] **Step 1: 增加类型**

`apps/web/src/types.ts`：

- `SettingsTab` 改为：

```ts
export type SettingsTab = "provider" | "agent" | "knowledge" | "maps" | "asr" | "runtime";
```

- 在 `MapsConfig` 之后插入：

```ts
export type AsrConfig = {
  enabled: boolean;
  has_api_key: boolean;
};

export type TranscribeResult = {
  text: string;
  duration_ms: number;
};
```

- [ ] **Step 2: 增加 API 方法**

`apps/web/src/api.ts`：

- import 列表加入 `AsrConfig` 与 `TranscribeResult`。
- 在 `updateMapsConfig` 之后插入：

```ts
  getAsrConfig: () => request<AsrConfig>("/api/asr"),
  updateAsrConfig: (payload: {
    enabled?: boolean;
    api_key?: string;
    clear_api_key?: boolean;
  }) =>
    request<AsrConfig>("/api/asr", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  transcribeAudio: (file: File): Promise<TranscribeResult> => {
    const form = new FormData();
    form.append("file", file);
    return fetch("/api/asr/transcribe", {
      method: "POST",
      credentials: "include",
      body: form,
    }).then(async (response) => {
      if (!response.ok) throw await responseError(response);
      return (await response.json()) as TranscribeResult;
    });
  },
```

- [ ] **Step 3: 创建配置组件**

创建 `apps/web/src/components/AsrSettings.tsx`：

```tsx
import { FormEvent, useEffect, useState } from "react";

import { api, errorText } from "../api";
import type { AsrConfig } from "../types";
import { ConfirmDialog } from "./ConfirmDialog";

export function AsrSettings() {
  const [config, setConfig] = useState<AsrConfig | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    void api
      .getAsrConfig()
      .then((next) => {
        if (!active) return;
        setConfig(next);
        setEnabled(next.enabled);
      })
      .catch((exc) => {
        if (active) setError(errorText(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadAttempt]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!config || saving || clearing || clearOpen) return;
    const trimmed = apiKey.trim();
    if (enabled && !config.has_api_key && !trimmed) {
      setError("请先填写 DeepSeek API Key");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const next = await api.updateAsrConfig({
        enabled,
        ...(trimmed ? { api_key: trimmed } : {}),
      });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已保存。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSaving(false);
    }
  }

  async function clearKey() {
    if (clearing) return;
    setClearing(true);
    setClearOpen(false);
    setError("");
    setNotice("");
    try {
      const next = await api.updateAsrConfig({ clear_api_key: true });
      setConfig(next);
      setEnabled(next.enabled);
      setApiKey("");
      setNotice("已清除 DeepSeek API Key 并停用语音输入。");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setClearing(false);
    }
  }

  const saveDisabled =
    !config ||
    saving ||
    clearing ||
    (enabled && !config.has_api_key && !apiKey.trim());

  return (
    <>
      <form className="editor" onSubmit={save}>
        <label className="toggle-row" htmlFor="asr-enabled">
          <input
            id="asr-enabled"
            type="checkbox"
            checked={enabled}
            disabled={!config || saving || clearing}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          <span>
            <strong>启用语音输入</strong>
            <small>聊天输入框出现麦克风按钮，所有 Agent 共享</small>
          </span>
        </label>

        <label className="field-label" htmlFor="asr-key">
          DeepSeek API Key
        </label>
        <input
          id="asr-key"
          type="password"
          maxLength={200}
          autoComplete="off"
          value={apiKey}
          disabled={!config || saving || clearing}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={
            config?.has_api_key
              ? "已配置，留空保持不变，输入新 Key 可替换"
              : "输入 DeepSeek API Key"
          }
        />

        <div className="hint">
          音频只用于转写，不会保存到工作区或会话记录；单次录音最长 2 分钟。
          ASR 按音频时长计费。
        </div>

        {config?.has_api_key && (
          <div className="form-actions">
            <span className="key-badge ok">密钥已配置</span>
            <button
              type="button"
              className="ghost-button"
              onClick={() => setClearOpen(true)}
              disabled={saving || clearing}
            >
              清除密钥
            </button>
          </div>
        )}

        {error && (
          <div className="error-banner" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="hint" role="status">
            {notice}
          </div>
        )}

        <div className="form-actions">
          {config ? (
            <button className="primary" type="submit" disabled={saveDisabled}>
              {saving ? "保存中…" : "保存语音识别配置"}
            </button>
          ) : (
            <button
              type="button"
              className="ghost-button"
              onClick={() => setLoadAttempt((value) => value + 1)}
              disabled={loading}
            >
              {loading ? "加载中…" : "重新加载"}
            </button>
          )}
        </div>
      </form>

      <ConfirmDialog
        open={clearOpen}
        title="清除 DeepSeek API Key？"
        description="清除后语音输入会停用，需要重新填写 Key 才能恢复。"
        confirmLabel="清除"
        danger
        onClose={() => setClearOpen(false)}
        onConfirm={() => void clearKey()}
      />
    </>
  );
}
```

- [ ] **Step 4: 接入配置页**

`apps/web/src/components/SettingsPage.tsx`：

- import 区加入：

```tsx
import { AsrSettings } from "./AsrSettings";
```

- 在 `{tab === "maps" && (...)}` 区块之后插入：

```tsx
          {tab === "asr" && (
          <section className="settings-card">
            <div className="settings-card-head">
              <div>
                <h2>语音识别</h2>
                <p>DeepSeek 语音识别；音频仅用于转写，不保存；按音频时长计费。</p>
              </div>
            </div>
            <AsrSettings />
          </section>
          )}
```

- [ ] **Step 5: 侧栏入口**

`apps/web/src/components/Sidebar.tsx`：在「地图」按钮之后、「运行环境」按钮之前插入：

```tsx
              <button
                className={`sidebar-action ${props.settingsActive && props.settingsTab === "asr" ? "active" : ""}`}
                onClick={() => props.onOpenSettingsTab("asr")}
              >
                <span className="sidebar-action-icon">
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="9" y="3" width="6" height="11" rx="3" />
                    <path d="M5 11a7 7 0 0 0 14 0" />
                    <path d="M12 18v3" />
                  </svg>
                </span>
                <span>语音识别</span>
              </button>
```

- [ ] **Step 6: 构建**

Run: `cd apps/web && npm run build`

Expected: `tsc --noEmit` 无错误，vite 构建成功。若 dist 中混入其它会话未提交的前端改动，先停下来按 Step 0.2 处理。

- [ ] **Step 7: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add apps/web/src/types.ts apps/web/src/api.ts apps/web/src/components/AsrSettings.tsx apps/web/src/components/SettingsPage.tsx apps/web/src/components/Sidebar.tsx apps/web/dist
git commit -m "Add ASR settings UI and API client

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: 聊天输入框录音与转写

**Files:**
- Modify: `apps/web/src/components/ChatPanel.tsx`
- Modify: `apps/web/src/styles.css`

- [ ] **Step 1: 增加录音状态与配置读取**

在 `ChatPanel` 组件内、现有 `const throttledStreamReasoning = ...` 之后插入：

```tsx
  const [asrEnabled, setAsrEnabled] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState<
    "idle" | "starting" | "recording" | "transcribing"
  >("idle");
  const [voiceSeconds, setVoiceSeconds] = useState(0);
  const [voiceNotice, setVoiceNotice] = useState("");
  const recorderRef = useRef<MediaRecorder | null>(null);
  const voiceStreamRef = useRef<MediaStream | null>(null);
  const voiceChunksRef = useRef<Blob[]>([]);
  const voiceTimerRef = useRef<number | null>(null);
  const voiceStartedAtRef = useRef(0);
  const voiceLimitRef = useRef(false);
  const mountedRef = useRef(true);
  const voiceStatusRef = useRef(voiceStatus);
  voiceStatusRef.current = voiceStatus;
  const draftRef = useRef(props.draft);
  draftRef.current = props.draft;
```

在现有 `useEffect`（`focusSignal` 那个）之后插入：

```tsx
  useEffect(() => {
    let active = true;
    void api
      .getAsrConfig()
      .then((config) => {
        if (active) setAsrEnabled(config.enabled);
      })
      .catch(() => {
        if (active) setAsrEnabled(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (voiceTimerRef.current !== null) {
        window.clearInterval(voiceTimerRef.current);
        voiceTimerRef.current = null;
      }
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        recorder.onstop = null;
        recorder.stop();
      }
      voiceStreamRef.current?.getTracks().forEach((track) => track.stop());
      recorderRef.current = null;
      voiceStreamRef.current = null;
      voiceChunksRef.current = [];
    };
  }, []);
```

- [ ] **Step 2: 增加录音状态机**

在 `useCallback` 定义的 `reloadSession` 之后、`return (` 之前插入：

```tsx
  function releaseVoiceStream() {
    if (voiceTimerRef.current !== null) {
      window.clearInterval(voiceTimerRef.current);
      voiceTimerRef.current = null;
    }
    voiceStreamRef.current?.getTracks().forEach((track) => track.stop());
    voiceStreamRef.current = null;
    recorderRef.current = null;
  }

  function appendVoiceText(text: string) {
    const current = draftRef.current;
    const next = current.trim()
      ? /\s$/.test(current)
        ? current + text
        : `${current} ${text}`
      : text;
    onDraftChangeRef.current(next);
    textareaRef.current?.focus();
  }

  async function finishVoiceRecording(mimeType: string) {
    const chunks = voiceChunksRef.current;
    voiceChunksRef.current = [];
    releaseVoiceStream();
    const seconds = (Date.now() - voiceStartedAtRef.current) / 1000;
    if (seconds < 0.5) {
      setVoiceStatus("idle");
      setVoiceNotice("录音太短，请重试");
      return;
    }
    const type = mimeType || "audio/webm";
    const extension = type.includes("mp4")
      ? "mp4"
      : type.includes("ogg")
        ? "ogg"
        : "webm";
    const file = new File(chunks, `recording.${extension}`, { type });
    setVoiceStatus("transcribing");
    setVoiceNotice(
      voiceLimitRef.current ? "已到 2 分钟上限，正在转写…" : "正在转写…",
    );
    try {
      const result = await api.transcribeAudio(file);
      const text = result.text.trim();
      if (!text) {
        setVoiceNotice("没有识别到语音内容");
      } else {
        appendVoiceText(text);
        setVoiceNotice("");
      }
    } catch (exc) {
      setVoiceNotice(describeApiError(exc));
    } finally {
      voiceLimitRef.current = false;
      setVoiceStatus("idle");
    }
  }

  function stopVoiceRecording(reason: "user" | "limit") {
    const recorder = recorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    if (reason === "limit") {
      voiceLimitRef.current = true;
      setVoiceNotice("已到 2 分钟上限，正在转写…");
    }
    recorder.stop();
  }

  async function startVoiceRecording() {
    if (voiceStatusRef.current !== "idle" || !props.canSend) return;
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setVoiceNotice("当前浏览器无法录音");
      return;
    }
    setVoiceNotice("");
    voiceLimitRef.current = false;
    setVoiceStatus("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const candidates = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ];
      const mimeType =
        candidates.find((item) => MediaRecorder.isTypeSupported(item)) ?? "";
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream);
      voiceChunksRef.current = [];
      voiceStreamRef.current = stream;
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) voiceChunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        void finishVoiceRecording(recorder.mimeType || mimeType);
      };
      voiceStartedAtRef.current = Date.now();
      setVoiceSeconds(0);
      setVoiceStatus("recording");
      recorder.start(1000);
      voiceTimerRef.current = window.setInterval(() => {
        const seconds = Math.floor(
          (Date.now() - voiceStartedAtRef.current) / 1000,
        );
        setVoiceSeconds(seconds);
        if (seconds >= 120) stopVoiceRecording("limit");
      }, 500);
    } catch {
      releaseVoiceStream();
      setVoiceStatus("idle");
      setVoiceNotice("无法访问麦克风，请检查浏览器权限");
    }
  }

  function voiceClock(seconds: number) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  }
```

- [ ] **Step 3: 录音或转写时禁用发送**

把现有：

```tsx
  const canSubmit =
    props.canSend &&
    !props.pendingSend &&
    (props.draft.trim().length > 0 ||
      props.attachments.length > 0 ||
      props.quote.trim().length > 0);
```

改为：

```tsx
  const canSubmit =
    props.canSend &&
    !props.pendingSend &&
    voiceStatus !== "recording" &&
    voiceStatus !== "transcribing" &&
    (props.draft.trim().length > 0 ||
      props.attachments.length > 0 ||
      props.quote.trim().length > 0);
```

- [ ] **Step 4: 加入麦克风按钮**

在 `.composer-tools` 内，找到 `{menuOpen && ( ... )}` 区块的结束 `)}`，在它之后、`</div>`（`.composer-tools` 的结束标签）之前插入：

```tsx
            {asrEnabled && (
              <button
                type="button"
                className={`composer-voice ${
                  voiceStatus === "recording" ? "recording" : ""
                }`}
                aria-label={
                  voiceStatus === "recording"
                    ? "停止录音并转写"
                    : voiceStatus === "transcribing"
                      ? "正在转写"
                      : "语音输入"
                }
                title={
                  voiceStatus === "recording"
                    ? "停止录音并转写"
                    : voiceStatus === "transcribing"
                      ? "正在转写"
                      : "语音输入"
                }
                disabled={
                  !props.canSend ||
                  voiceStatus === "starting" ||
                  voiceStatus === "transcribing"
                }
                onClick={() => {
                  if (voiceStatus === "recording") stopVoiceRecording("user");
                  else void startVoiceRecording();
                }}
              >
                <svg
                  viewBox="0 0 24 24"
                  width="16"
                  height="16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0" />
                  <path d="M12 18v3" />
                </svg>
              </button>
            )}
```

- [ ] **Step 5: 加入录音状态提示**

在 `composer-card` 的结束标签之后插入。定位原文：

```tsx
        </div>
        <input
          ref={fileInputRef}
```

改为：

```tsx
        </div>
        {asrEnabled && voiceStatus !== "idle" && (
          <div className="composer-voice-status" role="status">
            {voiceStatus === "recording"
              ? `${voiceClock(voiceSeconds)} / 2:00 · 再点麦克风停止`
              : voiceNotice || "正在转写…"}
          </div>
        )}
        {asrEnabled && voiceStatus === "idle" && voiceNotice && (
          <div className="composer-voice-status error" role="status">
            {voiceNotice}
          </div>
        )}
        <input
          ref={fileInputRef}
```

- [ ] **Step 6: 加样式**

`apps/web/src/styles.css`，在 `.composer-plus:hover, .composer-plus.active { ... }` 规则之后插入：

```css
.composer-voice {
  width: 30px;
  height: 30px;
  display: inline-grid;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: #fff;
  color: var(--muted);
  transition: color 120ms ease, border-color 120ms ease, background 120ms ease;
}

.composer-voice:hover:not(:disabled) {
  color: var(--accent);
  border-color: rgb(var(--accent-rgb) / 0.3);
  background: var(--accent-soft);
}

.composer-voice.recording {
  color: #fff;
  border-color: #e2483d;
  background: #e2483d;
  animation: voice-pulse 1.2s ease-in-out infinite;
}

.composer-voice:disabled {
  opacity: 0.5;
  cursor: default;
}

@keyframes voice-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(226, 72, 61, 0.35);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(226, 72, 61, 0);
  }
}

.composer-voice-status {
  max-width: var(--chat-lane);
  margin: 6px auto 0;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}

.composer-voice-status.error {
  color: #c0392b;
}
```

在 `@media (max-width: 860px)` 内把：

```css
  .composer-plus {
    width: 32px;
    height: 32px;
  }
```

改为：

```css
  .composer-plus,
  .composer-voice {
    width: 32px;
    height: 32px;
  }
```

在 pearl 主题 `.pearl .composer-plus:hover, .pearl .composer-plus.active { ... }` 之后插入：

```css
.pearl .composer-voice {
  border-color: rgba(26, 30, 38, 0.1);
  background: rgba(255, 255, 255, 0.7);
}

.pearl .composer-voice:hover:not(:disabled) {
  border-color: rgba(122, 108, 255, 0.35);
  background: rgba(255, 255, 255, 0.95);
  color: #5b4bf0;
}

.pearl .composer-voice.recording {
  border-color: #e2483d;
  background: #e2483d;
  color: #fff;
}
```

- [ ] **Step 7: 构建**

Run: `cd apps/web && npm run build`

Expected: 无 TypeScript 错误，构建成功。

- [ ] **Step 8: 浏览器验证**

Run（两个终端或预览工具）：

```bash
cd apps/api && ../../.venv/bin/uvicorn app.main:app --port 8000
```

```bash
cd apps/web && npm run dev
```

打开 `http://localhost:5173` 并登录，然后：

1. 配置页 →「语音识别」：未启用时聊天页没有 `.composer-voice`；启用并填一个 Key 后回到聊天，按钮出现。
2. 点麦克风：浏览器弹出麦克风授权；允许后按钮变红并显示 `0:0x / 2:00`。
3. 再点一次：显示「正在转写…」。本地没有 ffmpeg 时应看到「服务器未安装 ffmpeg，无法处理语音」；该错误说明前端到后端链路已打通。
4. 拒绝麦克风权限：显示「无法访问麦克风，请检查浏览器权限」。
5. 录音中切换会话或离开聊天页：录音停止，不出现上传。
6. 手机尺寸（≤860px）：按钮尺寸 32px，点击与停止正常。
7. 有真实 Key 且本机已安装 ffmpeg（`brew install ffmpeg`）时，确认转写文字追加到草稿；没有条件则记录为未验证，部署到生产后补测。

- [ ] **Step 9: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add apps/web/src/components/ChatPanel.tsx apps/web/src/styles.css apps/web/dist
git commit -m "Add voice input recording to the chat composer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

注意：`styles.css` 可能同时含有其它会话的改动。只提交语音相关改动；若无法区分，先与用户确认再提交。

---

## Task 6: 文档同步与整体验证

**Files:**
- Modify: `docs/intent.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: 更新 intent 编号与 F27**

`docs/intent.md`：

- 第 5 行改为：

```markdown
- 编号水位：F27
```

- F2 功能点第 2 条在「附件、引用」之后补上「语音输入」：

```markdown
2. 网页 Chat：流式输出、历史会话、停止、运行中排队追问、Markdown/公式渲染、附件、引用（整条与划词）、语音输入；聊天页不显示顶部标题栏；失败或停止后重新发送即可，不提供单条消息重试。
```

- 在 F26 小节之后追加：

```markdown
### F27 网页语音输入

- 状态：现行
- 日期：2026-11-18
- 来源：2026-11-18 网页语音输入设计（`docs/superpowers/specs/2026-11-18-voice-input-asr-design.md`）

#### 功能点

1. 配置页新增「语音识别」分区：全局启用开关与 DeepSeek API Key，存在 `settings.json` 的 `asr` 节；接口只返回 `enabled` 与 `has_api_key`。
2. 聊天输入框在启用后显示麦克风按钮：点击开始录音，再点停止；录音中显示计时，最长 120 秒后自动停止。
3. 停止后把原始录音上传 `POST /api/asr/transcribe`；后端用 ffmpeg 转成 16 kHz 单声道 16-bit WAV，再带服务端 Key 调用 DeepSeek `POST /v1/tools/asr`。
4. 转写成功把文字追加到当前草稿（空草稿直接写入），用户确认后自行发送；空结果提示「没有识别到语音内容」。
5. 音频只在内存中流转：不写工作区、不进 transcript、不落临时文件；Key 不返回前端、不写日志。
6. 录音或转写期间发送按钮禁用；切换会话、离开聊天页或组件卸载时停止录音并丢弃内容。
7. 浏览器录音要求 HTTPS 或 localhost；生产通过 HTTPS 反向代理访问。

#### 验收标准

- 未启用时聊天页没有麦克风按钮；启用后按钮出现，授权后可录音、停止并转写。
- 转写文字追加到草稿，可正常发送；已有草稿时不会覆盖。
- 120 秒自动停止；短于 0.5 秒提示「录音太短」。
- 超过 15 MiB、超过 120 秒、ffmpeg 缺失、DeepSeek 异常分别返回对应中文提示。
- 音频不落盘；转写失败后可立即重录，不保留上一段音频。
- 权限拒绝、非安全上下文、录音中切换会话都有明确提示或安全丢弃。
- 后端测试与前端构建通过。
```

- [ ] **Step 2: 更新 CLAUDE.md**

- 把配置分区相关句子从「Provider、Agent、知识库、地图、运行环境五个配置分区」改为「Provider、Agent、知识库、地图、语音识别、运行环境六个配置分区」。
- 在网页聊天条目末尾追加：

```markdown
语音输入：配置页「语音识别」用全局 DeepSeek API Key；输入框麦克风按钮录音后上传 `/api/asr/transcribe`，后端 ffmpeg 转 16 kHz 单声道 WAV 再调 DeepSeek ASR，文字追加草稿；单次最长 120 秒，音频不落盘；生产机已装 ffmpeg，本地需自行安装。
```

- 运行约定或目录说明不需要新增文件；不新增环境变量。

- [ ] **Step 3: 更新 README.md**

- 聊天功能列表补充语音输入与 120 秒限制；配置页分区列表加「语音识别」。
- 本地开发依赖说明补充：真实转码需要 `brew install ffmpeg`（macOS）。
- 部署说明补充：生产机已预装 ffmpeg；麦克风需要 HTTPS 或 localhost 访问。

- [ ] **Step 4: 全量回归**

Run: `.venv/bin/python -m pytest apps/api/app/tests`

Expected: 全部通过（含新增的 ASR 测试）。

Run: `cd apps/web && npm run build`

Expected: 构建成功。

- [ ] **Step 5: 最终浏览器检查**

按 Task 5 Step 8 的清单再走一遍，并补充：

1. 配置页保存、清除 Key 后接口返回与页面徽标一致。
2. 未配置 Key 时直接调用转写接口返回 409，且没有请求到 DeepSeek。
3. 转写成功后文字完整追加，发送后会话历史与普通文字消息一致。
4. 桌面上传附件、引用、排队追问等既有功能不受影响。

- [ ] **Step 6: 提交**

```bash
cd /Users/wulang/Desktop/AI/SuperPersonalPlatform
git status --short
git add docs/intent.md CLAUDE.md README.md
git commit -m "Document voice input and ASR configuration

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

若 `docs/intent.md`、`CLAUDE.md` 同时含其它会话的未提交改动，只提交语音相关段落，必要时先与用户确认。

---

## Self-Review

- **Spec 覆盖**：配置节与脱敏（Task 1、3）、ffmpeg 转码与 120 秒/8 MiB/15 MiB 边界（Task 2、3）、DeepSeek 状态映射（Task 2）、前端录音状态机与追加草稿（Task 5）、配置页与侧栏入口（Task 4）、隐私与不落盘（Task 5/6 文档）、F27 与文档同步（Task 6）。
- **占位符**：无 TBD/TODO；每个代码步骤给出完整代码或明确原文替换。
- **类型一致性**：`AsrConfig {enabled, has_api_key}`、`TranscribeResult {text, duration_ms}` 在类型、API、组件之间一致；后端返回键 `text` / `duration_ms` 与前端类型一致；路由常量 `MAX_UPLOAD_BYTES` 从 `app.services.asr` 导入并在测试中按 `app.routers.asr.MAX_UPLOAD_BYTES` 打补丁。
- **共享 checkout**：每个提交都使用定向 `git add`，并明确要求不提交其它会话的未提交改动。
