import json

import pytest
from pathlib import Path

from server.app.code_execution_service import CodeExecutionService
from server.infrastructure.config import CodeExecutionConfig, CodeExecutionDockerConfig, parse_settings
from server.infrastructure.docker_gvisor_sandbox import DockerGVisorSandbox, SandboxOutputFile, SandboxResult
from server.infrastructure.tool_runtime import PlatformToolContext, build_platform_tools


def test_code_execution_config_defaults_to_docker_gvisor_on() -> None:
    settings = parse_settings({"auth": {"token": "secret-token"}})

    assert settings.code_execution.enabled is True
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
    assert not service._sandbox.execution_root.exists()
    assert payload["script_path"].startswith("/scratch/exec_")
    assert payload["stdout"] == "ok\n"
    assert payload["files"][0]["path"].startswith("/artifacts/exec_")
    artifact_path = tmp_path / "agents" / "assistant" / "workspace" / payload["files"][0]["path"].lstrip("/")
    assert artifact_path.read_text(encoding="utf-8") == "result"


def test_execute_code_platform_tool_is_authorized_explicitly(tmp_path) -> None:
    (tmp_path / "config.yaml").write_text(
        "auth:\n  token: secret-token\ncode_execution:\n  enabled: false\n",
        encoding="utf-8",
    )

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
        self.execution_root = kwargs["execution_root"]
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


@pytest.mark.parametrize("outcome", ["success", "failed", "timeout", "cancelled"])
def test_execution_retains_script_collects_outputs_and_cleans_mounts(tmp_path, monkeypatch, outcome):
    import asyncio
    from server.infrastructure import docker_gvisor_sandbox as sandbox_module
    service = CodeExecutionService(tmp_path, CodeExecutionConfig())
    events = []
    roots = []

    async def capability():
        return {"ok": True}
    service._sandbox.capability_check = capability

    class Process:
        returncode = None
        async def communicate(self):
            if outcome == "cancelled":
                raise asyncio.CancelledError()
            if outcome == "timeout":
                raise asyncio.TimeoutError()
            self.returncode = 0 if outcome == "success" else 1
            return b"hello", b"" if self.returncode == 0 else b"failed"
        def kill(self):
            events.append("killed")
        async def wait(self):
            self.returncode = -9
            return -9

    async def spawn(*command, **kwargs):
        mounts = [command[i + 1] for i, part in enumerate(command) if part == "-v"]
        work = Path(next(m.split(":")[0] for m in mounts if ":/workspace/work:" in m))
        output = Path(next(m.split(":")[0] for m in mounts if ":/workspace/output:" in m))
        roots.append(work.parent.parent)
        assert (work / "main.py").read_text() == "print('hello')"
        assert "browser" not in str(mounts)
        (output / "answer.txt").write_text("answer")
        return Process()

    async def remove(command, **kwargs):
        assert command[:3] == ("docker", "rm", "-f")
        assert roots[0].exists(), "mounts must remain until container stops"
        events.append("removed")
        return {"exit_code": 0, "stderr": "", "stdout": ""}

    monkeypatch.setattr(sandbox_module.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(sandbox_module, "_run_capture", remove)
    call = service.execute(language="python", code="print('hello')", agent_id="assistant", run_id="run_test")
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(call)
    else:
        result = asyncio.run(call)
        assert result["ok"] == (outcome == "success")
        assert result["script_path"].endswith(".py")
        if outcome in {"success", "failed"}:
            artifact = tmp_path / "agents/assistant/workspace" / result["files"][0]["path"].lstrip("/")
            assert artifact.read_text() == "answer"
    assert len(list((tmp_path / "agents/assistant/workspace/scratch").glob("*.py"))) == 1
    assert not roots[0].exists()
    if outcome in {"timeout", "cancelled"}:
        assert events == ["killed", "removed"]


def test_artifact_copy_failure_retains_recovery_directory(tmp_path, monkeypatch):
    import asyncio
    import shutil
    source = tmp_path / "result.txt"
    source.write_text("answer")
    service = CodeExecutionService(tmp_path, CodeExecutionConfig())
    service._sandbox = FakeSandbox(source)
    def fail_copy(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr("server.app.code_execution_service.shutil.copy2", fail_copy)
    result = asyncio.run(service.execute(language="python", code="pass", agent_id="assistant", run_id="r"))
    assert result["error"]["type"] == "ArtifactCollectionError"
    recovery = Path(result["recovery_path"])
    assert recovery.is_dir()
    assert (tmp_path / "agents/assistant/workspace" / result["script_path"].lstrip("/")).exists()
    shutil.rmtree(recovery)


def test_parallel_executions_use_distinct_scripts_artifacts_and_temporary_roots(tmp_path):
    import asyncio
    source = tmp_path / "result.txt"
    source.write_text("answer")
    service = CodeExecutionService(tmp_path, CodeExecutionConfig())
    roots = []
    class ParallelSandbox(FakeSandbox):
        async def execute(self, **kwargs):
            roots.append(kwargs["execution_root"])
            await asyncio.sleep(0)
            return await super().execute(**kwargs)
    service._sandbox = ParallelSandbox(source)
    async def run():
        return await asyncio.gather(*[service.execute(language="python", code="pass", agent_id="assistant", run_id="same") for _ in range(2)])
    results = asyncio.run(run())
    assert len(set(roots)) == 2 and not any(p.exists() for p in roots)
    assert results[0]["script_path"] != results[1]["script_path"]
    assert results[0]["files"][0]["path"] != results[1]["files"][0]["path"]
