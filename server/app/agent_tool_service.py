from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any, Awaitable, Callable

from server.app.agent_skill_service import AgentSkillToolbox
from server.domain.agents import AgentConfigError, AgentDefinition, ToolAccessDefinition


ToolHandler = Callable[[dict[str, Any], "AgentToolRuntime"], Awaitable[str]]


PROFILE_TOOLS = {
    "default": (),
    "self-dev": (
        "list_skill",
        "read_skill",
        "repo_search",
        "repo_read_file",
        "repo_write_file",
        "repo_run_command",
        "repo_status",
        "repo_diff",
        "repo_commit",
        "repo_push",
    ),
    "portfolio": (
        "list_portfolio_holdings",
        "add_portfolio_holding",
        "update_portfolio_holding",
        "delete_portfolio_holding",
    ),
}


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    group: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class AgentToolRuntime:
    skill_tools: AgentSkillToolbox
    repo_root: Path | None = None
    portfolio_service: object | None = None
    allowed_commands: tuple[str, ...] = (
        "git status",
        "git diff",
        "python -m pytest",
        ".venv/bin/python -m pytest",
        "npm test",
        "npm run build",
    )
    allow_push: bool = False

    def require_repo(self) -> Path:
        if self.repo_root is None:
            raise AgentConfigError("repo tool requires a self-dev task repo")
        return self.repo_root.resolve()

    def resolve_repo_path(self, relative_path: str) -> Path:
        repo_root = self.require_repo()
        requested = (repo_root / relative_path).resolve()
        if requested != repo_root and repo_root not in requested.parents:
            raise AgentConfigError("path escapes task repo")
        return requested


class AgentToolRegistry:
    def __init__(self) -> None:
        self._tools = {
            definition.name: definition
            for definition in (
                AgentToolDefinition(
                    "list_skill",
                    "skills",
                    "列出当前 Agent 显式绑定且存在的 skills。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._list_skill,
                ),
                AgentToolDefinition(
                    "read_skill",
                    "skills",
                    "读取当前 Agent 显式绑定的某个 skill 内容。",
                    {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Skill id, such as common:research or private:self-dev.",
                            }
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    self._read_skill,
                ),
                AgentToolDefinition(
                    "repo_search",
                    "fs",
                    "在任务仓库中搜索文本。",
                    {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    self._repo_search,
                ),
                AgentToolDefinition(
                    "repo_read_file",
                    "fs",
                    "读取任务仓库中的相对路径文件。",
                    {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    self._repo_read_file,
                ),
                AgentToolDefinition(
                    "repo_write_file",
                    "fs",
                    "写入任务仓库中的相对路径文件。",
                    {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                    self._repo_write_file,
                ),
                AgentToolDefinition(
                    "repo_run_command",
                    "runtime",
                    "在任务仓库中运行允许列表里的命令。",
                    {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                        "additionalProperties": False,
                    },
                    self._repo_run_command,
                ),
                AgentToolDefinition(
                    "repo_status",
                    "git",
                    "查看任务仓库 git status --short。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._repo_status,
                ),
                AgentToolDefinition(
                    "repo_diff",
                    "git",
                    "查看任务仓库 git diff。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._repo_diff,
                ),
                AgentToolDefinition(
                    "repo_commit",
                    "git",
                    "在任务仓库提交全部变更。",
                    {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    self._repo_commit,
                ),
                AgentToolDefinition(
                    "repo_push",
                    "git",
                    "将任务分支 push 到远端。只有任务明确允许 push 时才会执行。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._repo_push,
                ),
                AgentToolDefinition(
                    "list_portfolio_holdings",
                    "portfolio",
                    "查看当前所有投资持仓列表。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._list_portfolio_holdings,
                ),
                AgentToolDefinition(
                    "add_portfolio_holding",
                    "portfolio",
                    "添加新的投资持仓。",
                    {
                        "type": "object",
                        "properties": {
                            "type_": {"type": "string", "description": "持仓类型: stock(股票), fund(基金), crypto(加密货币)"},
                            "symbol": {"type": "string", "description": "交易代码, 如 AAPL, BTC"},
                            "name": {"type": "string", "description": "持仓名称"},
                            "quantity": {"type": "number", "description": "持有数量"},
                            "avg_cost": {"type": "number", "description": "平均成本单价"},
                            "currency": {"type": "string", "description": "货币: CNY, USD, HKD"},
                            "notes": {"type": "string", "description": "可选备注"},
                        },
                        "required": ["type_", "symbol", "name", "quantity", "avg_cost", "currency"],
                        "additionalProperties": False,
                    },
                    self._add_portfolio_holding,
                ),
                AgentToolDefinition(
                    "update_portfolio_holding",
                    "portfolio",
                    "修改已有投资持仓的信息。",
                    {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "持仓 ID"},
                            "type_": {"type": "string", "description": "可选: 新的持仓类型"},
                            "symbol": {"type": "string", "description": "可选: 新的交易代码"},
                            "name": {"type": "string", "description": "可选: 新的持仓名称"},
                            "quantity": {"type": "number", "description": "可选: 新的持有数量"},
                            "avg_cost": {"type": "number", "description": "可选: 新的平均成本单价"},
                            "currency": {"type": "string", "description": "可选: 新的货币"},
                            "notes": {"type": "string", "description": "可选: 新的备注"},
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    self._update_portfolio_holding,
                ),
                AgentToolDefinition(
                    "delete_portfolio_holding",
                    "portfolio",
                    "删除指定的投资持仓。",
                    {
                        "type": "object",
                        "properties": {"id": {"type": "string", "description": "持仓 ID"}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    self._delete_portfolio_holding,
                ),
            )
        }

    def resolve_tools(
        self,
        platform_tools: ToolAccessDefinition,
        agent: AgentDefinition,
        legacy_common_tools: tuple[str, ...],
    ) -> tuple[str, ...]:
        access = agent.tools or platform_tools
        names = set(PROFILE_TOOLS[access.profile])
        names.update(legacy_common_tools)
        names.update(access.allow)
        names.difference_update(access.deny)
        unknown = sorted(name for name in names if name not in self._tools)
        if unknown:
            raise AgentConfigError(f"tools contains unsupported tool: {unknown[0]}")
        return tuple(name for name in self._tools if name in names)

    def schemas(self, tool_names: tuple[str, ...]) -> list[dict[str, object]]:
        return [self._tools[name].schema() for name in tool_names if name in self._tools]

    async def dispatch(
        self,
        name: str,
        args: dict[str, Any],
        runtime: AgentToolRuntime,
    ) -> str:
        definition = self._tools.get(name)
        if definition is None:
            return json.dumps({"error": f"Unsupported tool: {name}"}, ensure_ascii=False)
        try:
            return await definition.handler(args, runtime)
        except AgentConfigError as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    async def _list_skill(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        return await runtime.skill_tools.list_skill()

    async def _read_skill(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        return await runtime.skill_tools.read_skill(str(args.get("id") or ""))

    async def _repo_search(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        query = str(args.get("query") or "")
        if not query:
            raise AgentConfigError("query is required")
        repo = runtime.require_repo()
        result = await self._run(("rg", "-n", "--", query), repo)
        if result["returncode"] == 127:
            result = await self._run(("grep", "-R", "-n", "--", query, "."), repo)
        return json.dumps(result, ensure_ascii=False)

    async def _repo_read_file(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        path = runtime.resolve_repo_path(str(args.get("path") or ""))
        if not path.is_file():
            raise AgentConfigError("file does not exist")
        return json.dumps(
            {"path": str(path.relative_to(runtime.require_repo())), "content": path.read_text(encoding="utf-8")},
            ensure_ascii=False,
        )

    async def _repo_write_file(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        path = runtime.resolve_repo_path(str(args.get("path") or ""))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content") or ""), encoding="utf-8")
        return json.dumps({"ok": True, "path": str(path.relative_to(runtime.require_repo()))}, ensure_ascii=False)

    async def _repo_run_command(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        command = str(args.get("command") or "").strip()
        if command not in runtime.allowed_commands:
            raise AgentConfigError("command is not allowed")
        return json.dumps(await self._run(tuple(command.split()), runtime.require_repo()), ensure_ascii=False)

    async def _repo_status(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        return json.dumps(await self._run(("git", "status", "--short"), runtime.require_repo()), ensure_ascii=False)

    async def _repo_diff(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        return json.dumps(await self._run(("git", "diff"), runtime.require_repo()), ensure_ascii=False)

    async def _repo_commit(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        message = str(args.get("message") or "").strip()
        if not message:
            raise AgentConfigError("message is required")
        repo = runtime.require_repo()
        add = await self._run(("git", "add", "."), repo)
        commit = await self._run(("git", "commit", "-m", message), repo)
        return json.dumps({"add": add, "commit": commit}, ensure_ascii=False)

    async def _repo_push(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        if not runtime.allow_push:
            raise AgentConfigError("push requires explicit task confirmation")
        return json.dumps(await self._run(("git", "push", "origin", "HEAD"), runtime.require_repo()), ensure_ascii=False)

    async def _list_portfolio_holdings(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        svc = self._portfolio_service(runtime)
        holdings = svc.list_holdings()
        return json.dumps([h.__dict__ for h in holdings], ensure_ascii=False, default=str)

    async def _add_portfolio_holding(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        svc = self._portfolio_service(runtime)
        holding = svc.create_holding(
            type_=str(args.get("type_") or ""),
            symbol=str(args.get("symbol") or ""),
            name=str(args.get("name") or ""),
            quantity=float(args.get("quantity") or 0),
            avg_cost=float(args.get("avg_cost") or 0),
            currency=str(args.get("currency") or ""),
            notes=str(args.get("notes") or ""),
        )
        return json.dumps({"ok": True, "holding": holding.__dict__}, ensure_ascii=False, default=str)

    async def _update_portfolio_holding(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        svc = self._portfolio_service(runtime)
        update_kwargs = {}
        for key in ("type_", "symbol", "name", "quantity", "avg_cost", "currency", "notes"):
            if key in args and args[key] is not None:
                val = args[key]
                update_kwargs[key] = float(val) if key in ("quantity", "avg_cost") else str(val)
        holding = svc.update_holding(str(args.get("id") or ""), **update_kwargs)
        return json.dumps({"ok": True, "holding": holding.__dict__}, ensure_ascii=False, default=str)

    async def _delete_portfolio_holding(self, args: dict[str, Any], runtime: AgentToolRuntime) -> str:
        svc = self._portfolio_service(runtime)
        svc.delete_holding(str(args.get("id") or ""))
        return json.dumps({"ok": True}, ensure_ascii=False)

    @staticmethod
    def _portfolio_service(runtime: AgentToolRuntime):
        svc = runtime.portfolio_service
        if svc is None:
            raise AgentConfigError("portfolio tools are not available in this context")
        return svc

    async def _run(self, command: tuple[str, ...], cwd: Path) -> dict[str, object]:
        process = await subprocess_async(command, cwd)
        return {
            "command": " ".join(command),
            "returncode": process.returncode,
            "stdout": process.stdout[-20000:],
            "stderr": process.stderr[-20000:],
        }


@dataclass(frozen=True)
class CompletedCommand:
    returncode: int
    stdout: str
    stderr: str


async def subprocess_async(command: tuple[str, ...], cwd: Path) -> CompletedCommand:
    proc = await __import__("asyncio").create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdout=__import__("asyncio").subprocess.PIPE,
        stderr=__import__("asyncio").subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return CompletedCommand(
        returncode=int(proc.returncode or 0),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


DEFAULT_AGENT_TOOL_REGISTRY = AgentToolRegistry()
