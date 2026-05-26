from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from server.app.agent_chat_service import AgentChatCheckpoint, AgentChatService
from server.app.agent_tool_service import AgentToolRuntime
from server.app.agent_skill_service import AgentSkillService
from server.app.job_service import JobService
from server.domain.jobs import JobStatus
from server.infrastructure.async_command_runner import AsyncCommandResult, AsyncCommandRunner


DEFAULT_REPO_URL = "https://github.com/coolerwu/SuperPersonalPlatform.git"


@dataclass(frozen=True)
class SelfDevTask:
    id: str
    goal: str
    agent_id: str
    repo_url: str
    branch: str
    status: str
    repo_path: str
    created_at: str
    updated_at: str
    result: str = ""
    error: str = ""
    recommendation: str = ""


class SelfDevService:
    def __init__(
        self,
        workspace: Path,
        agent_chat_service: AgentChatService | None,
        job_service: JobService | None = None,
        command_runner: AsyncCommandRunner | None = None,
    ) -> None:
        self._workspace = workspace
        self._tasks_dir = workspace / "self-dev" / "tasks"
        self._agent_chat_service = agent_chat_service
        self._job_service = job_service or JobService(workspace)
        self._command_runner = command_runner or AsyncCommandRunner()
        # Rebuilt from task.json job state on service construction. The durable
        # task directory remains the source of truth after process restarts.
        self._running_tasks: dict[str, str] = self._rebuild_running_tasks()

    def create_task(self, goal: str, agent_id: str, repo_url: str = DEFAULT_REPO_URL) -> SelfDevTask:
        task_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
        task_dir = self._task_dir(task_id)
        repo_dir = task_dir / "repo"
        branch = f"agent/self-dev-{task_id}"
        now = self._now()
        task = SelfDevTask(
            id=task_id,
            goal=goal.strip(),
            agent_id=agent_id.strip(),
            repo_url=repo_url.strip() or DEFAULT_REPO_URL,
            branch=branch,
            status="created",
            repo_path=str(repo_dir),
            created_at=now,
            updated_at=now,
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        self._write_task(task)
        self._append_event(task.id, "created", {"goal": task.goal, "repo_url": task.repo_url})
        return task

    def get_task(self, task_id: str) -> dict[str, object]:
        task = self._read_task(task_id)
        return {
            **asdict(task),
            "events": self._read_events(task_id),
            "status_text": self._git(["status", "--short"], Path(task.repo_path)) if Path(task.repo_path).exists() else "",
            "diff": self._git(["diff"], Path(task.repo_path)) if Path(task.repo_path).exists() else "",
        }

    def list_tasks(self) -> list[SelfDevTask]:
        tasks = []
        for path in sorted(self._tasks_dir.glob("*/task.json"), reverse=True):
            try:
                tasks.append(self._task_from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except Exception:
                continue
        return tasks

    def is_task_running(self, task_id: str) -> bool:
        """Check durable task.json state instead of process-local asyncio tasks."""
        try:
            task = self._read_task(task_id)
        except FileNotFoundError:
            return False
        raw_job = self._raw_job(task_id)
        return task.status == "running" or (
            isinstance(raw_job, dict) and raw_job.get("status") == JobStatus.RUNNING.value
        )

    def cancel_task(self, task_id: str, reason: str = "user cancelled") -> SelfDevTask:
        task = self._read_task(task_id)
        raw_job = self._raw_job(task_id)
        if isinstance(raw_job, dict) and raw_job.get("id"):
            job_status = str(raw_job.get("status") or "")
            if job_status in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
                self._job_service.cancel(str(raw_job["id"]), reason=reason)
                self._append_event(task_id, "status", {"status": "cancelled", "reason": reason})
                return self._read_task(task_id)

        if task.status not in {"queued", "running"}:
            raise ValueError("task has no active job")

        task = self._replace_task(
            task,
            status="cancelled",
            error="",
            result=task.result,
            recommendation=task.recommendation,
        )
        self._append_event(task_id, "status", {"status": "cancelled", "reason": reason})
        return task

    async def run_task(
        self,
        task_id: str,
        allow_push: bool = False,
        instruction: str = "",
    ) -> SelfDevTask:
        """Enqueue a durable job and return immediately for API compatibility."""
        if self._agent_chat_service is None:
            raise RuntimeError("Agent service is unavailable")

        if self.is_task_running(task_id):
            return self._read_task(task_id)

        task = self._read_task(task_id)
        job = self._job_service.enqueue(
            task_id,
            "self_dev.run_task",
            {"allow_push": allow_push, "instruction": instruction},
        )
        self._running_tasks = self._rebuild_running_tasks()
        task = self._read_task(task_id)
        self._append_event(task.id, "status", {"status": task.status, "job_id": job.id})
        return task

    async def _run_task_internal(
        self,
        task_id: str,
        allow_push: bool = False,
        instruction: str = "",
    ) -> SelfDevTask:
        """Internal method that actually runs the task."""
        try:
            task = self._read_task(task_id)
            repo = Path(task.repo_path)

            # Step 1: Clone repository
            self._append_event(task.id, "log", {"level": "info", "message": "🚀 开始执行开发任务"})
            if not repo.exists():
                self._append_event(task.id, "log", {"level": "info", "message": f"📦 克隆仓库: {task.repo_url}"})
                await self._run_git_process_async(
                    ["clone", task.repo_url, str(repo)],
                    self._task_dir(task_id),
                    on_stdout=lambda text: self._append_event(task.id, "log", {"level": "info", "message": text.rstrip()}),
                    on_stderr=lambda text: self._append_event(task.id, "log", {"level": "info", "message": text.rstrip()}),
                )
                self._append_event(task.id, "log", {"level": "success", "message": "✅ 仓库克隆完成"})
            else:
                self._append_event(task.id, "log", {"level": "info", "message": "📦 使用已存在的仓库"})

            # Step 2: Checkout branch
            self._append_event(task.id, "log", {"level": "info", "message": f"🌿 检出分支: {task.branch}"})
            await self._run_git_process_async(
                ["checkout", "-B", task.branch],
                repo,
                on_stdout=lambda text: self._append_event(task.id, "log", {"level": "info", "message": text.rstrip()}),
                on_stderr=lambda text: self._append_event(task.id, "log", {"level": "info", "message": text.rstrip()}),
            )
            self._append_event(task.id, "log", {"level": "success", "message": "✅ 分支准备就绪"})

            # Step 3: Prepare agent and runtime
            self._append_event(task.id, "log", {"level": "info", "message": "🤖 初始化 Agent 环境..."})
            skill_service = AgentSkillService(self._workspace)
            platform_agent = self._agent_chat_service._load_platform().get_agent(task.agent_id)
            runtime = AgentToolRuntime(
                skill_tools=skill_service.toolbox(platform_agent),
                repo_root=repo,
                allow_push=allow_push,
            )
            self._append_event(task.id, "log", {"level": "info", "message": f"🔧 可用工具: {len(skill_service.toolbox(platform_agent))} 个"})

            # Step 4: Run agent with checkpoint logging
            self._append_event(task.id, "log", {"level": "info", "message": "💬 开始与 AI Agent 对话..."})
            if instruction:
                self._append_event(task.id, "log", {"level": "info", "message": f"📝 补充指令: {instruction[:100]}..."})

            async def checkpoint(event: AgentChatCheckpoint) -> None:
                emoji = {"goal": "🎯", "reason": "🤔", "answer": "✨", "checkpoint": "📍"}.get(event.stage, "📍")
                self._append_event(
                    task.id,
                    "checkpoint",
                    {"stage": event.stage, "title": event.title, "detail": event.detail},
                )
                self._append_event(
                    task.id,
                    "log",
                    {"level": "info", "message": f"{emoji} [{event.stage}] {event.title}"},
                )

            result = await self._agent_chat_service.run_with_tool_runtime(
                task.agent_id,
                (
                    f"请在任务仓库中完成这个开发任务：{task.goal}\n"
                    f"{'补充说明：' + instruction.strip() if instruction.strip() else ''}\n"
                    "完成后给出改动摘要、测试结果和后续建议。"
                ),
                runtime,
                checkpoint,
            )

            # Step 5: Process result
            self._append_event(task.id, "log", {"level": "success", "message": "✅ Agent 执行完成"})
            recommendation = self._recommendation_from_result(result)
            self._append_event(task.id, "log", {"level": "info", "message": f"📊 AI 建议: {recommendation}"})

            task = self._replace_task(task, status="needs_review", result=result, recommendation=recommendation)
            self._append_event(task.id, "result", {"result": result, "recommendation": recommendation})
            self._append_event(task.id, "log", {"level": "success", "message": "🎉 任务执行完成，等待审查"})
            return task

        except Exception as exc:
            task = self._read_task(task_id)
            error_msg = str(exc)
            self._append_event(task.id, "log", {"level": "error", "message": f"❌ 执行错误: {error_msg}"})
            task = self._replace_task(task, status="failed", error=error_msg)
            self._append_event(task.id, "error", {"error": error_msg})
            return task

    async def accept_task(self, task_id: str, note: str) -> SelfDevTask:
        task = self._read_task(task_id)
        if task.status not in {"needs_review", "accepted"}:
            raise ValueError("task is not waiting for review")
        self._append_event(task.id, "accept", {"note": note.strip(), "recommendation": task.recommendation})
        if task.recommendation == "push":
            repo = Path(task.repo_path)
            add = self._git_result(["add", "."], repo)
            commit = self._git_result(["commit", "-m", note.strip() or f"Implement self-dev task {task.id}"], repo)
            push = self._git_result(["push", "origin", "HEAD"], repo)
            self._append_event(task.id, "push", {"add": add, "commit": commit, "push": push})
            failures = [
                result for result in (add, commit, push)
                if int(result["returncode"]) != 0 and "nothing to commit" not in str(result["output"])
            ]
            if failures:
                error = str(failures[-1]["output"]).strip() or "git push failed"
                self._append_event(task.id, "error", {"error": error})
                return self._replace_task(task, status="failed", error=error)
            return self._replace_task(task, status="pushed", error="")
        return await self.run_task(
            task.id,
            instruction=(
                f"用户接受了你的建议：{task.recommendation or 'review'}。\n"
                f"用户补充说明：{note.strip() or '无'}\n"
                "请根据这个接受决策执行下一步，并在完成后重新给出是否建议 push。"
            ),
        )

    async def reject_task(self, task_id: str, reason: str) -> SelfDevTask:
        task = self._read_task(task_id)
        if task.status != "needs_review":
            raise ValueError("task is not waiting for review")
        self._append_event(task.id, "reject", {"reason": reason.strip()})
        return await self.run_task(
            task.id,
            instruction=(
                f"用户拒绝了你的建议：{task.recommendation or 'review'}。\n"
                f"拒绝原因或修改意见：{reason.strip() or '无'}\n"
                "请按用户拒绝意见继续修改或重新评估，并在完成后重新给出是否建议 push。"
            ),
        )

    def _recommendation_from_result(self, result: str) -> str:
        normalized = result.lower()
        push_markers = ("建议 push", "可以 push", "ready to push", "recommend push", "push")
        hold_markers = ("不要 push", "不建议 push", "not ready", "do not push", "hold")
        if any(marker in normalized for marker in hold_markers):
            return "hold"
        if any(marker in normalized for marker in push_markers):
            return "push"
        return "review"

    def _task_dir(self, task_id: str) -> Path:
        if "/" in task_id or ".." in task_id:
            raise ValueError("invalid task id")
        return self._tasks_dir / task_id

    def _read_task(self, task_id: str) -> SelfDevTask:
        return self._task_from_dict(json.loads((self._task_dir(task_id) / "task.json").read_text(encoding="utf-8")))

    def _write_task(self, task: SelfDevTask) -> None:
        path = self._task_dir(task.id) / "task.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")

    def _replace_task(self, task: SelfDevTask, **changes: str) -> SelfDevTask:
        next_task = SelfDevTask(**{**asdict(task), **changes, "updated_at": self._now()})
        self._write_task(next_task)
        return next_task

    def _task_from_dict(self, raw: dict[str, object]) -> SelfDevTask:
        return SelfDevTask(**{field: str(raw.get(field) or "") for field in SelfDevTask.__dataclass_fields__})

    def _append_event(self, task_id: str, event_type: str, payload: dict[str, object]) -> None:
        path = self._task_dir(task_id) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.open("a", encoding="utf-8").write(
            json.dumps({"timestamp": self._now(), "type": event_type, **payload}, ensure_ascii=False) + "\n"
        )

    def _read_events(self, task_id: str) -> list[dict[str, object]]:
        path = self._task_dir(task_id) / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _git(self, args: list[str], cwd: Path) -> str:
        return str(self._git_result(args, cwd)["output"])

    def _git_result(self, args: list[str], cwd: Path) -> dict[str, object]:
        result = self._run_git_process(args, cwd, check=False)
        return {
            "command": "git " + " ".join(args),
            "returncode": result.returncode,
            "output": (result.stdout + result.stderr)[-20000:],
        }

    def _run_git_process(self, args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=check,
        )

    async def _run_git_process_async(
        self,
        args: list[str],
        cwd: Path,
        check: bool = True,
        timeout: float | None = None,
        on_stdout=None,
        on_stderr=None,
    ) -> AsyncCommandResult:
        return await self._command_runner.run(
            ["git", *args],
            cwd=cwd,
            timeout=timeout,
            check=check,
            on_stdout=on_stdout,
            on_stderr=on_stderr,
        )

    def _raw_job(self, task_id: str) -> dict[str, object] | None:
        try:
            raw = json.loads((self._task_dir(task_id) / "task.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        job = raw.get("job")
        return job if isinstance(job, dict) else None

    def _rebuild_running_tasks(self) -> dict[str, str]:
        running: dict[str, str] = {}
        for job in self._job_service.list_active():
            if job.status == JobStatus.RUNNING:
                running[job.task_id] = job.id
        return running

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
