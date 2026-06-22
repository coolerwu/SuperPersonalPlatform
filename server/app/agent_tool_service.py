from dataclasses import dataclass
import json
from typing import Any, Awaitable, Callable

from server.app.agent_skill_service import AgentSkillToolbox
from server.domain.agents import AgentConfigError, AgentDefinition, SkillDefinition, ToolAccessDefinition


ToolHandler = Callable[[dict[str, Any], "AgentToolRuntime"], Awaitable[str]]


PROFILE_TOOLS = {
    "default": (),
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
    portfolio_service: object | None = None


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
                                "description": "Skill id, such as common:research or private:daily.",
                            }
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    self._read_skill,
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
        skill_definitions: tuple[SkillDefinition, ...] = (),
    ) -> tuple[str, ...]:
        names = set(PROFILE_TOOLS[platform_tools.profile])
        denied = set(platform_tools.deny)
        names.update(legacy_common_tools)
        names.update(platform_tools.allow)
        skill_by_id = {skill.id: skill for skill in skill_definitions}
        for skill_id in agent.skill_ids:
            skill = skill_by_id.get(skill_id)
            if skill is None:
                continue
            names.update(PROFILE_TOOLS[skill.tools.profile])
            names.update(skill.tools.allow)
            denied.update(skill.tools.deny)
        names.difference_update(denied)
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

DEFAULT_AGENT_TOOL_REGISTRY = AgentToolRegistry()
