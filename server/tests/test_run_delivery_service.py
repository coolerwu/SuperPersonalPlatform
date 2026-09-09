import asyncio

from server.app.run_delivery_service import RunDeliveryService
from server.app.run_service import RunService
from server.app.system_log_service import SystemLogService
from server.domain.run_approval import RunApprovalAction, RunApprovalInterrupt, RunApprovalRequest


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
agents:
  definitions:
    - id: assistant
      name: Assistant
      system_prompt: Be direct.
      model_id: default
      deepagent:
        interrupt_on:
          - write_context
"""


class FakeChannelDeliveryService:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.deliveries = []

    async def deliver_text(self, **kwargs):
        self.deliveries.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise TimeoutError("temporary WeChat timeout")
        return {"ok": True}


def test_delivery_retries_with_stable_client_id_and_does_not_duplicate(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, **kwargs):
        return "最终结果"

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)
    monkeypatch.setattr("server.app.run_delivery_service.DELIVERY_RETRY_DELAYS_SECONDS", (0, 0, 0, 0, 0, 0))
    run_service = RunService(tmp_path)
    run = asyncio.run(
        run_service.create_run(
            content="开始",
            agent_id="assistant",
            source="wechat",
            metadata={
                "account_id": "default",
                "peer_id": "wxid_user",
                "peer_type": "private",
                "from_user_id": "wxid_user",
                "to_user_id": "wxid_bot",
                "context_token": "reply-token",
            },
        )
    )
    run_id = run["run_id"]
    asyncio.run(run_service.execute_run(run_id))
    channel = FakeChannelDeliveryService(failures=1)
    delivery_service = RunDeliveryService(
        run_service=run_service,
        channel_delivery_service=channel,
        system_log_service=SystemLogService(tmp_path),
        poll_interval_seconds=0.001,
    )

    first = asyncio.run(delivery_service.process_run(run_id))
    second = asyncio.run(delivery_service.process_run(run_id))
    asyncio.run(delivery_service.process_run(run_id))

    assert first["status"] == "failed"
    assert second["status"] == "delivered"
    assert len(channel.deliveries) == 2
    assert channel.deliveries[0]["client_id"] == channel.deliveries[1]["client_id"] == f"spp-{run_id}-final"
    assert channel.deliveries[-1]["to_user_id"] == "wxid_user"
    assert channel.deliveries[-1]["text"] == "最终结果"


def test_delivery_notifies_approval_then_delivers_resumed_result(tmp_path, monkeypatch) -> None:
    (tmp_path / "config.yaml").write_text(CONFIG, encoding="utf-8")

    async def fake_run(self, **kwargs):
        if kwargs.get("resume") is not None:
            return "审批后的结果"
        return RunApprovalRequest(
            interrupts=(
                RunApprovalInterrupt(
                    interrupt_id="interrupt-1",
                    actions=(
                        RunApprovalAction(
                            name="write_context",
                            args={"path": "/files/result.md"},
                            description="写入结果文档",
                            allowed_decisions=("approve", "reject"),
                        ),
                    ),
                ),
            ),
        )

    monkeypatch.setattr("server.infrastructure.deepagent_runtime.DeepAgentRuntime.run", fake_run)
    run_service = RunService(tmp_path)
    run = asyncio.run(
        run_service.create_run(
            content="写入文档",
            agent_id="assistant",
            source="schedule",
            metadata={
                "schedule_id": "daily",
                "delivery": {
                    "channel": "wechat",
                    "account_id": "default",
                    "peer_id": "wxid_user",
                    "peer_type": "private",
                    "to_user_id": "wxid_user",
                    "context_token": "schedule-token",
                },
            },
        )
    )
    run_id = run["run_id"]
    asyncio.run(run_service.execute_run(run_id))
    channel = FakeChannelDeliveryService()
    delivery_service = RunDeliveryService(
        run_service=run_service,
        channel_delivery_service=channel,
        system_log_service=SystemLogService(tmp_path),
        poll_interval_seconds=0.001,
    )

    asyncio.run(delivery_service.process_run(run_id))
    asyncio.run(delivery_service.process_run(run_id))

    assert len(channel.deliveries) == 1
    assert "此任务需要审批" in channel.deliveries[0]["text"]
    assert f"/approve {run_id}" in channel.deliveries[0]["text"]
    assert run_service.get_run(run_id)["delivery"]["approval_notification"]["status"] == "delivered"

    run_service.approve_run(run_id)
    asyncio.run(run_service.execute_run(run_id))
    asyncio.run(delivery_service.process_run(run_id))
    asyncio.run(delivery_service.process_run(run_id))

    assert len(channel.deliveries) == 2
    assert channel.deliveries[1]["text"] == "审批后的结果"
    assert channel.deliveries[0]["client_id"] != channel.deliveries[1]["client_id"]
    assert run_service.get_run(run_id)["delivery"]["status"] == "delivered"
