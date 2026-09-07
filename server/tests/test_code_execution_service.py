import json
from pathlib import Path

from server.app.code_execution_service import CodeExecutionService
from server.infrastructure.config import CodeExecutionConfig, CodeExecutionDockerConfig, parse_settings
from server.infrastructure.docker_gvisor_sandbox import DockerGVisorSandbox, SandboxOutputFile, SandboxResult
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


def test_code_execution_config_defaults_to_docker_gvisor_off() -> None:
    settings = parse_settings({"auth": {"token": "secret-token"}})

    assert settings.code_execution.enabled is False
    assert settings.code_execution.runtime == "docker_gvisor"
    assert settings.code_execution.languages == ("python", "shell")
    assert settings.code_execution.docker.runtime == "runsc"
    assert settings.code_execution.docker.image == "python:3.12-slim-bookworm"
    assert settings.code_execution.docker.network == "none"


def test_docker_gvisor_sandbox_uses_locked_down_docker_run(tmp_path) -> None:
    config = CodeExecutionConfig(
        enabled=True,
        docker=CodeExecutionDockerConfig(image="python:3.12-slim-bookworm"),
    )
    sandbox = DockerGVisorSandbox(config)

    command = sandbox._docker_command(
        language="shell",
        script_name="script.sh",
        container_name="spp-code-test",
        input_dir=tmp_path / "input",
        work_dir=tmp_path / "work",
        output_dir=tmp_path / "output",
    )

    assert "--runtime=runsc" in command
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "no-new-privileges" in command
    assert "python:3.12-slim-bookworm" in command
    assert command[-2:] == ("/bin/sh", "/workspace/work/script.sh")


def test_execute_code_returns_disabled_observation(tmp_path) -> None:
    service = CodeExecutionService(tmp_path, CodeExecutionConfig(enabled=False))

    result = service.execute_sync(
        language="python",
        code="print('hello')",
        agent_id="assistant",
        run_id="run_1",
    )
    payload = json.loads(result)

    assert payload["ok"] is False
    assert payload["tool"] == "execute_code"
    assert payload["error"]["type"] == "CodeExecutionDisabledError"


def test_execute_code_copies_output_files_to_agent_artifacts(tmp_path) -> None:
    source = tmp_path / "sandbox-output.txt"
    source.write_text("result", encoding="utf-8")
    service = CodeExecutionService(
        tmp_path,
        CodeExecutionConfig(enabled=True, docker=CodeExecutionDockerConfig(image="python:3.12-slim-bookworm")),
    )
    service._sandbox = FakeSandbox(source)

    payload = json.loads(
        service.execute_sync(
            language="python",
            code="print('ok')",
            agent_id="assistant",
            run_id="run_1",
        )
    )

    assert payload["ok"] is True
    assert payload["stdout"] == "ok\n"
    assert payload["files"][0]["path"].startswith("/artifacts/code_runs/run_1/exec_")
    artifact_path = tmp_path / "agents" / "assistant" / payload["files"][0]["path"].lstrip("/")
    assert artifact_path.read_text(encoding="utf-8") == "result"


def test_execute_code_platform_tool_is_authorized_explicitly(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text("auth:\n  token: secret-token\n", encoding="utf-8")

    tools = build_platform_tools(
        ("execute_code",),
        context_workspace=tmp_path / "context",
        tool_context=PlatformToolContext(
            run_id="run_1",
            source="web_chat",
            agent_id="assistant",
            session_id="session_1",
            metadata={},
        ),
    )

    assert [tool.name for tool in tools] == ["execute_code"]
    result = json.loads(tools[0].invoke({"language": "python", "code": "print('x')"}))
    assert result["ok"] is False
    assert result["error"]["type"] == "CodeExecutionDisabledError"


class FakeSandbox:
    def __init__(self, output_file: Path) -> None:
        self._output_file = output_file

    async def capability_check(self):
        return {"ok": True, "runtime": "runsc", "image": "python:3.12-slim-bookworm"}

    async def execute(self, **kwargs):
        return SandboxResult(
            ok=True,
            language=kwargs["language"],
            exit_code=0,
            stdout="ok\n",
            stderr="",
            files=(
                SandboxOutputFile(
                    path=self._output_file,
                    relative_path="result.txt",
                    mime="text/plain",
                    size=self._output_file.stat().st_size,
                ),
            ),
            duration_ms=12,
        )
