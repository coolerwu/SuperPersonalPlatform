from dataclasses import dataclass
import json
from typing import Any, Awaitable, Callable

from server.app.agent_skill_service import AgentSkillToolbox
from server.domain.agents import AgentConfigError, AgentDefinition, SkillDefinition


ToolHandler = Callable[[dict[str, Any], "AgentToolRuntime"], Awaitable[str]]
SUPPORTED_SCENES = {"mcp", "dag", "agent"}
EMPTY_OBJECT_SCHEMA = {"type": "object", "properties": {}}


@dataclass(frozen=True)
class AgentToolDefinition:
    name: str
    display_name: str
    description: str
    input: dict[str, Any]
    handler: ToolHandler
    output: dict[str, Any] | None = None
    support_scene: tuple[str, ...] = ("agent",)

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise AgentConfigError("tool name is required")
        if not self.display_name.strip():
            raise AgentConfigError(f"tool {self.name} display_name is required")
        if not self.description.strip():
            raise AgentConfigError(f"tool {self.name} description is required")
        self._validate_schema(self.input, "input")
        output = self.output or EMPTY_OBJECT_SCHEMA
        object.__setattr__(self, "output", output)
        self._validate_schema(output, "output")
        if not self.support_scene or len(set(self.support_scene)) != len(self.support_scene):
            raise AgentConfigError(f"tool {self.name} support_scene must be non-empty and unique")
        unknown = set(self.support_scene) - SUPPORTED_SCENES
        if unknown:
            raise AgentConfigError(f"tool {self.name} support_scene is unsupported: {sorted(unknown)[0]}")

    def _validate_schema(self, schema: dict[str, Any], field: str) -> None:
        if not isinstance(schema, dict) or schema.get("type") not in {"object", "array", "string", "number", "integer", "boolean"}:
            raise AgentConfigError(f"tool {self.name} {field} must be a JSON Schema object")

    def schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input,
            },
        }

    def public_definition(self) -> dict[str, object]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "input": self.input,
            "output": self.output,
            "support_scene": list(self.support_scene),
        }


@dataclass(frozen=True)
class AgentToolRuntime:
    skill_tools: AgentSkillToolbox
    portfolio_service: object | None = None


class AgentToolRegistry:
    def __init__(self, definitions: tuple[AgentToolDefinition, ...] | None = None) -> None:
        definitions = definitions if definitions is not None else (
                AgentToolDefinition(
                    "list_skill",
                    "列出 Skill",
                    "列出当前 Agent 显式绑定且存在的 skills。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._list_skill,
                ),
                AgentToolDefinition(
                    "read_skill",
                    "读取 Skill",
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
                    "查看持仓",
                    "查看当前所有投资持仓列表。",
                    {"type": "object", "properties": {}, "additionalProperties": False},
                    self._list_portfolio_holdings,
                ),
                AgentToolDefinition(
                    "add_portfolio_holding",
                    "添加持仓",
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
                    "修改持仓",
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
                    "删除持仓",
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
        names = [definition.name for definition in definitions]
        if len(set(names)) != len(names):
            raise AgentConfigError("duplicate tool name")
        self._tools = {definition.name: definition for definition in definitions}

    def resolve_tools(
        self,
        agent: AgentDefinition,
        skill_definitions: tuple[SkillDefinition, ...] = (),
    ) -> tuple[str, ...]:
        names: set[str] = set()
        skill_by_id = {skill.id: skill for skill in skill_definitions}
        for skill_id in agent.skill_ids:
            skill = skill_by_id.get(skill_id)
            if skill is None:
                continue
            names.update(skill.tools.allow)
        unknown = sorted(name for name in names if name not in self._tools)
        if unknown:
            raise AgentConfigError(f"tools contains unsupported tool: {unknown[0]}")
        return tuple(name for name in self._tools if name in names)

    def public_definitions(self) -> tuple[dict[str, object], ...]:
        return tuple(self._tools[name].public_definition() for name in sorted(self._tools))

    def validate_tool_names(self, names: tuple[str, ...]) -> None:
        unknown = sorted(name for name in names if name not in self._tools)
        if unknown:
            raise AgentConfigError(f"tools contains unsupported tool: {unknown[0]}")

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
