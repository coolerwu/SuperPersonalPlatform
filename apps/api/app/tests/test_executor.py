from types import SimpleNamespace

import pytest

from app.services.executor import DeepAgentExecutor


@pytest.mark.asyncio
async def test_executor_fails_without_api_key(tmp_path) -> None:
    provider = SimpleNamespace(api_key="", base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    executor = DeepAgentExecutor(provider=provider, checkpoint_dir=tmp_path, agent_name="test")
    events = [
        event
        async for event in executor.run(instructions="test", session_id="no-key", content="hi")
    ]
    assert events
    assert events[0].type == "run_failed"
    assert events[0].payload["error"]["type"] == "ProviderError"
