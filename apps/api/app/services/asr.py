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
