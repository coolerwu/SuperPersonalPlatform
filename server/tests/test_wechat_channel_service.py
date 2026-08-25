import asyncio
import base64
from pathlib import Path
from typing import Any

from server.app.session_service import SessionService
from server.app.wechat_channel_service import WechatChannelService


CONFIG = """\
auth:
  token: secret-token
llm:
  default_model_id: default
  models:
    - id: default
      name: Default
      provider: openai_compatible
      base_url: https://api.openai.com/v1
      api_key: test-key
      model: gpt-4o-mini
      supports_images: true
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
      context_ids: []
      deepagent:
        max_iterations: 7
channels:
  wechat_personal:
    accounts:
      - id: default
        default_agent_id: assistant
"""


class FakeRunService:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_run(
        self,
        *,
        content: str,
        agent_id: str,
        source: str,
        session_id: str,
        attachments: tuple[dict[str, Any], ...],
        metadata: dict[str, Any],
    ) -> dict[str, str]:
        run_id = f"run-{len(self.created) + 1}"
        self.created.append(
            {
                "run_id": run_id,
                "content": content,
                "agent_id": agent_id,
                "source": source,
                "session_id": session_id,
                "attachments": attachments,
                "metadata": metadata,
            }
        )
        return {"run_id": run_id}

    async def execute_run(self, run_id: str) -> dict[str, Any]:
        return {"result": {"content": f"reply for {run_id}"}}


class FakeWechatClient:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_message(self, baseurl: str, bot_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append({"baseurl": baseurl, "bot_token": bot_token, "payload": payload})
        return {"_debug_status": 200}


def _image_message(context_token: str = "imgctx") -> dict[str, Any]:
    return {
        "from_user_id": "wxid_user",
        "to_user_id": "wxid_bot",
        "context_token": context_token,
        "item_list": [
            {
                "image_item": {
                    "id": "photo-1",
                    "filename": "photo.png",
                    "mime": "image/png",
                    "content_base64": base64.b64encode(b"image-bytes").decode("ascii"),
                }
            }
        ],
    }


def _text_message(text: str, context_token: str = "textctx") -> dict[str, Any]:
    return {
        "from_user_id": "wxid_user",
        "to_user_id": "wxid_bot",
        "context_token": context_token,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


def _service(tmp_path: Path) -> tuple[WechatChannelService, FakeRunService, FakeWechatClient]:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")
    run_service = FakeRunService()
    client = FakeWechatClient()
    service = WechatChannelService(tmp_path, run_service, session_service=SessionService(tmp_path))
    service._client = client
    service._baseurl = "https://ilink.example"
    service._bot_token = "bot-token"
    service._pending_input_delay_seconds = 0.03
    return service, run_service, client


def test_wechat_text_waits_for_pending_window_before_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_text_message("你好"))
        assert run_service.created == []

        await asyncio.sleep(0.06)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "你好"
        assert created["metadata"]["batched_messages"] == 1
        assert created["metadata"]["delay_seconds"] == 0.03
        assert client.sent[0]["payload"]["context_token"] == "textctx"

    asyncio.run(scenario())


def test_wechat_image_then_text_waits_and_merges_into_one_run(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_image_message())
        assert run_service.created == []

        await service._process_message(_text_message("看看这张图"))
        assert run_service.created == []
        await asyncio.sleep(0.05)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "看看这张图"
        assert len(created["attachments"]) == 1
        assert created["attachments"][0]["filename"] == "photo.png"
        assert created["metadata"]["merged_pending_images"] == 1
        assert created["metadata"]["batched_messages"] == 2
        assert created["metadata"]["context_token"] == "textctx"
        assert client.sent[0]["payload"]["context_token"] == "textctx"

    asyncio.run(scenario())


def test_wechat_image_only_flushes_after_short_pending_window(tmp_path) -> None:
    async def scenario() -> None:
        service, run_service, client = _service(tmp_path)

        await service._process_message(_image_message(context_token="img-only"))
        await asyncio.sleep(0.06)

        assert len(run_service.created) == 1
        created = run_service.created[0]
        assert created["content"] == "用户发送了一张图片。"
        assert len(created["attachments"]) == 1
        assert created["metadata"]["image_only_flush"] is True
        assert created["metadata"]["batched_messages"] == 1
        assert client.sent[0]["payload"]["context_token"] == "img-only"

    asyncio.run(scenario())
