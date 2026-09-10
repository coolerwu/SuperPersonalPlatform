"""Run-local usage ledger. Only newly completed model callbacks are counted."""
from typing import Any

from server.domain.agent_config import ModelDefinition
from server.domain.run_events import ModelUsagePayload


def add_call(ledger: dict[str, Any], payload: ModelUsagePayload, model: ModelDefinition) -> None:
    calls = ledger.setdefault("calls", {})
    if payload.call_id in calls:
        return
    call = payload.to_json()
    call["provider_id"] = model.id
    call["pricing"] = {
        "input_price_per_million": model.input_price_per_million,
        "output_price_per_million": model.output_price_per_million,
        "currency": model.price_currency,
    }
    call["estimated_cost"] = None
    if all(value is not None for value in (
        payload.input_tokens, payload.output_tokens,
        model.input_price_per_million, model.output_price_per_million,
    )):
        call["estimated_cost"] = (
            payload.input_tokens * model.input_price_per_million
            + payload.output_tokens * model.output_price_per_million
        ) / 1_000_000
    calls[payload.call_id] = call


def summarize_usage(ledger: dict[str, Any]) -> dict[str, Any]:
    calls = list(ledger.get("calls", {}).values())
    known = [call for call in calls if call.get("input_tokens") is not None and call.get("output_tokens") is not None]
    costs: dict[str, float] = {}
    for call in calls:
        if call.get("estimated_cost") is not None:
            currency = call["pricing"]["currency"]
            costs[currency] = costs.get(currency, 0) + call["estimated_cost"]
    return {
        "model_calls": len(calls),
        "reported_calls": len(known),
        "unknown_calls": len(calls) - len(known),
        "unpriced_calls": sum(call.get("estimated_cost") is None for call in calls),
        "input_tokens": sum(call["input_tokens"] for call in known) if known else None,
        "output_tokens": sum(call["output_tokens"] for call in known) if known else None,
        "cached_input_tokens": sum(call.get("cached_input_tokens", 0) for call in known) if known else None,
        "estimated_costs": costs,
        "execution_seconds": round(ledger.get("execution_seconds", 0), 3),
        "models": sorted({call["model"] for call in calls}),
        "interrupted_segments": sum(not segment.get("finished") for segment in ledger.get("segments", {}).values()),
    }
