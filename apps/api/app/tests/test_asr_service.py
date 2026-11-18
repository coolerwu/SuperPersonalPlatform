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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await transcribe_wav(b"wav-bytes", "key-1", client=client)

    assert result == {"text": "你好", "duration_ms": 1200}
    assert seen["url"] == ASR_URL
    assert seen["auth"] == "Bearer key-1"
    assert 'filename="speech.wav"' in seen["body"].decode("latin-1")


@pytest.mark.asyncio
async def test_transcribe_allows_empty_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"request_id": "r", "text": "", "duration_ms": 0}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await transcribe_wav(b"wav-bytes", "key", client=client)

    assert result == {"text": "", "duration_ms": 0}


@pytest.mark.parametrize(
    ("status", "platform_status", "detail"),
    [
        (400, 400, "语音识别无法处理这段音频"),
        (401, 400, "DeepSeek API Key 无效或已失效，请在配置页重新填写"),
        (403, 403, "DeepSeek 账号无权使用语音识别，请检查账号权限"),
        (408, 408, "音频上传超时，请重试"),
        (413, 413, "录音文件过大"),
        (429, 429, "语音识别服务繁忙，请稍后再试"),
        (503, 503, "语音识别服务暂不可用"),
        (504, 504, "语音识别超时，请重试"),
    ],
)
@pytest.mark.asyncio
async def test_transcribe_maps_status(
    status: int, platform_status: int, detail: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == platform_status
    assert info.value.detail == detail


@pytest.mark.asyncio
async def test_transcribe_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == 502


@pytest.mark.asyncio
async def test_transcribe_rejects_bad_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": 1})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AsrError) as info:
            await transcribe_wav(b"wav-bytes", "key", client=client)

    assert info.value.status_code == 502
