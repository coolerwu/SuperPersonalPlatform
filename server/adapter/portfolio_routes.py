import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from server.adapter.dependencies import AppContainer
from server.adapter.security import require_authenticated
from server.app.portfolio_service import PortfolioService
from server.domain.portfolio import HoldingNotFoundError


# ── Pydantic request/response models ──────────────────────────────────

class CreateHoldingPayload(BaseModel):
    type: str
    symbol: str
    name: str = ""
    quantity: float
    avg_cost: float
    currency: str = "CNY"
    notes: str = ""


class UpdateHoldingPayload(BaseModel):
    type: str | None = None
    symbol: str | None = None
    name: str | None = None
    quantity: float | None = None
    avg_cost: float | None = None
    currency: str | None = None
    notes: str | None = None


class ChatPayload(BaseModel):
    message: str


def _holding_to_response(h) -> dict:
    return {
        "id": h.id,
        "type": h.type,
        "symbol": h.symbol,
        "name": h.name,
        "quantity": h.quantity,
        "avg_cost": h.avg_cost,
        "total_cost": h.total_cost,
        "currency": h.currency,
        "notes": h.notes,
        "created_at": h.created_at,
        "updated_at": h.updated_at,
    }


# ── Tool functions for AI ─────────────────────────────────────────────

def _make_ai_tools(service: PortfolioService) -> list[dict[str, Any]]:
    """Build OpenAI-compatible tool definitions for holding operations."""

    async def tool_list_holdings() -> str:
        holdings = service.list_holdings()
        if not holdings:
            return "当前没有任何持仓记录。"
        lines = ["当前持仓列表："]
        for h in holdings:
            lines.append(
                f"- {h.name} ({h.symbol}) | 类型: {h.type} | "
                f"数量: {h.quantity} | 均价: {h.avg_cost}{h.currency} | "
                f"总成本: {h.total_cost}{h.currency}"
            )
        return "\n".join(lines)

    async def tool_create_holding(
        type_: str, symbol: str, name: str,
        quantity: float, avg_cost: float,
        currency: str = "CNY", notes: str = "",
    ) -> str:
        try:
            h = service.create_holding(type_, symbol, name, quantity, avg_cost, currency, notes)
            return (
                f"已创建持仓: {h.name}({h.symbol}), "
                f"数量 {h.quantity}, 均价 {h.avg_cost}{h.currency}"
            )
        except ValueError as e:
            return f"创建失败: {e}"

    async def tool_delete_holding(holding_id: str) -> str:
        try:
            h = service.get_holding(holding_id)
            service.delete_holding(holding_id)
            return f"已删除持仓: {h.name}({h.symbol})"
        except HoldingNotFoundError:
            return f"未找到持仓: {holding_id}"

    async def tool_update_holding(
        holding_id: str,
        type_: str | None = None,
        symbol: str | None = None,
        name: str | None = None,
        quantity: float | None = None,
        avg_cost: float | None = None,
        currency: str | None = None,
        notes: str | None = None,
    ) -> str:
        try:
            h = service.update_holding(
                holding_id, type_=type_, symbol=symbol, name=name,
                quantity=quantity, avg_cost=avg_cost,
                currency=currency, notes=notes,
            )
            return f"已更新持仓: {h.name}({h.symbol})"
        except HoldingNotFoundError:
            return f"未找到持仓: {holding_id}"

    return [
        {
            "type": "function",
            "function": {
                "name": "list_holdings",
                "description": "列出所有持仓记录",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_holding",
                "description": "创建一条新持仓记录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "type_": {
                            "type": "string",
                            "enum": ["stock", "fund", "crypto"],
                            "description": "资产类型: stock(股票), fund(基金), crypto(加密货币)",
                        },
                        "symbol": {"type": "string", "description": "交易代码, 如 AAPL, 00700"},
                        "name": {"type": "string", "description": "资产名称, 如 苹果, 腾讯控股"},
                        "quantity": {"type": "number", "description": "持有数量"},
                        "avg_cost": {"type": "number", "description": "每股/份均价"},
                        "currency": {
                            "type": "string",
                            "enum": ["CNY", "USD", "HKD"],
                            "description": "货币单位, 默认 CNY",
                        },
                        "notes": {"type": "string", "description": "备注"},
                    },
                    "required": ["type_", "symbol", "name", "quantity", "avg_cost"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_holding",
                "description": "删除一条持仓记录",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "holding_id": {"type": "string", "description": "持仓 ID"},
                    },
                    "required": ["holding_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_holding",
                "description": "更新一条持仓记录的部分字段",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "holding_id": {"type": "string", "description": "持仓 ID"},
                        "type_": {
                            "type": "string",
                            "enum": ["stock", "fund", "crypto"],
                            "description": "资产类型",
                        },
                        "symbol": {"type": "string", "description": "交易代码"},
                        "name": {"type": "string", "description": "资产名称"},
                        "quantity": {"type": "number", "description": "持有数量"},
                        "avg_cost": {"type": "number", "description": "均价"},
                        "currency": {
                            "type": "string",
                            "enum": ["CNY", "USD", "HKD"],
                        },
                        "notes": {"type": "string", "description": "备注"},
                    },
                    "required": ["holding_id"],
                },
            },
        },
    ], {
        "list_holdings": tool_list_holdings,
        "create_holding": tool_create_holding,
        "delete_holding": tool_delete_holding,
        "update_holding": tool_update_holding,
    }


# ── Router factory ────────────────────────────────────────────────────

def create_portfolio_router(container: AppContainer) -> APIRouter:
    def require_auth(request: Request) -> None:
        require_authenticated(request, container)

    router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])
    service = container.portfolio_service
    if service is None:
        return router

    # ── CRUD: holdings ────────────────────────────────────────────────

    @router.get("/holdings", dependencies=[Depends(require_auth)])
    def list_holdings() -> dict:
        return {"holdings": [_holding_to_response(h) for h in service.list_holdings()]}

    @router.post("/holdings", dependencies=[Depends(require_auth)], status_code=status.HTTP_201_CREATED)
    def create_holding(payload: CreateHoldingPayload) -> dict:
        try:
            h = service.create_holding(
                type_=payload.type,
                symbol=payload.symbol,
                name=payload.name,
                quantity=payload.quantity,
                avg_cost=payload.avg_cost,
                currency=payload.currency,
                notes=payload.notes,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.get("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def get_holding(holding_id: str) -> dict:
        try:
            h = service.get_holding(holding_id)
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.put("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def update_holding(holding_id: str, payload: UpdateHoldingPayload) -> dict:
        try:
            h = service.update_holding(
                holding_id,
                type_=payload.type,
                symbol=payload.symbol,
                name=payload.name,
                quantity=payload.quantity,
                avg_cost=payload.avg_cost,
                currency=payload.currency,
                notes=payload.notes,
            )
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
        return {"holding": _holding_to_response(h)}

    @router.delete("/holdings/{holding_id}", dependencies=[Depends(require_auth)])
    def delete_holding(holding_id: str) -> dict:
        try:
            service.delete_holding(holding_id)
        except HoldingNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        return {"ok": True}

    # ── AI Chat ───────────────────────────────────────────────────────

    @router.post("/chat", dependencies=[Depends(require_auth)])
    async def chat(payload: ChatPayload) -> dict:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        config = container.config_file_service.read(include_secrets=True)
        models = config.get("llm", {}).get("models", [])
        default_model_id = config.get("llm", {}).get("default_model_id", "")
        default_agent_id = config.get("agents", {}).get("default_agent_id", "")
        agents_config = config.get("agents", {}).get("definitions", [])

        # Find the default agent's model, or fallback to first model
        model_config = None
        if default_agent_id:
            for a in agents_config:
                if a.get("id") == default_agent_id:
                    mid = a.get("model_id", default_model_id)
                    for m in models:
                        if m.get("id") == mid:
                            model_config = m
                            break
        if model_config is None and default_model_id:
            for m in models:
                if m.get("id") == default_model_id:
                    model_config = m
                    break
        if model_config is None and models:
            model_config = models[0]

        if not model_config or not model_config.get("api_key"):
            return {"reply": "未找到可用的 LLM 配置，请在系统配置中添加模型。"}

        provider = model_config.get("provider", "openai_compatible")

        # Build LLM
        if provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            llm_kwargs: dict[str, Any] = {
                "api_key": model_config["api_key"],
                "model": model_config["model"],
                "temperature": model_config.get("temperature", 0.7),
            }
            if model_config.get("base_url", "").strip():
                llm_kwargs["base_url"] = model_config["base_url"]
            llm = ChatAnthropic(**llm_kwargs)
        else:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                api_key=model_config["api_key"],
                base_url=model_config.get("base_url", ""),
                model=model_config["model"],
                temperature=model_config.get("temperature", 0.7),
            )

        # Build tools + tool name map
        tool_defs, tool_map = _make_ai_tools(service)
        llm_with_tools = llm.bind_tools(tool_defs)

        # System prompt
        system_prompt = (
            "你是一个投资组合助手，帮助用户管理投资持仓。\n\n"
            "你可以使用以下工具：\n"
            "- list_holdings: 查看当前所有持仓\n"
            "- create_holding: 新增持仓\n"
            "- update_holding: 修改持仓\n"
            "- delete_holding: 删除持仓\n\n"
            "用户可以要求你添加、查询、修改或删除持仓。\n"
            "如果需要额外信息（比如股票当前市价），你可以建议用户通过网络搜索获取。\n"
            "请用中文回复。"
        )

        messages: list[Any] = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=payload.message),
        ]

        max_turns = 10
        for _ in range(max_turns):
            response = await llm_with_tools.ainvoke(messages)
            messages.append(response)

            if not hasattr(response, "tool_calls") or not response.tool_calls:
                return {"reply": str(response.content)}

            for tc in response.tool_calls:
                func = tool_map.get(tc["name"])
                if func is None:
                    result = f"未知工具: {tc['name']}"
                else:
                    try:
                        result = await func(**tc["args"])
                    except Exception as e:
                        result = f"工具执行失败: {e}"
                messages.append(
                    ToolMessage(content=str(result), tool_call_id=tc["id"])
                )

        # Max turns reached, force final answer
        messages.append(SystemMessage(content="已达最大对话轮次，请基于已有信息给出最终回复。"))
        final = await llm.ainvoke(messages[-5:])
        return {"reply": str(final.content)}

    return router
