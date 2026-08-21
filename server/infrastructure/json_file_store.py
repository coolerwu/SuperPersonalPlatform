from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    MatchCondition,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)


class JsonFileStore(BaseStore):
    """Small LangGraph store backed by one JSON file.

    This intentionally implements key-value search only. Semantic/vector search is
    left to a future database or vector-backed store.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        with self._lock:
            data = self._read_data()
            results: list[Result] = []
            changed = False
            for op in ops:
                result, did_change = self._apply_op(data, op)
                results.append(result)
                changed = changed or did_change
            if changed:
                self._write_data(data)
            return results

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        return await asyncio.to_thread(self.batch, list(ops))

    def _apply_op(self, data: dict[str, Any], op: Op) -> tuple[Result, bool]:
        if isinstance(op, GetOp):
            return self._get(data, op.namespace, op.key), False
        if isinstance(op, SearchOp):
            return self._search(data, op), False
        if isinstance(op, PutOp):
            self._put(data, op)
            return None, True
        if isinstance(op, ListNamespacesOp):
            return self._list_namespaces(data, op), False
        raise TypeError(f"unsupported store operation: {type(op).__name__}")

    def _get(self, data: dict[str, Any], namespace: tuple[str, ...], key: str) -> Item | None:
        item = _namespace_items(data, namespace).get(key)
        return _item_from_payload(namespace, key, item) if isinstance(item, dict) else None

    def _search(self, data: dict[str, Any], op: SearchOp) -> list[SearchItem]:
        matches: list[SearchItem] = []
        for namespace, key, payload in _iter_items(data):
            if not _namespace_startswith(namespace, op.namespace_prefix):
                continue
            item = _item_from_payload(namespace, key, payload)
            if item is None or not _matches_filter(item.value, op.filter):
                continue
            if op.query and op.query.lower() not in json.dumps(item.value, ensure_ascii=False).lower():
                continue
            matches.append(
                SearchItem(
                    namespace=item.namespace,
                    key=item.key,
                    value=item.value,
                    created_at=item.created_at,
                    updated_at=item.updated_at,
                    score=None,
                )
            )
        matches.sort(key=lambda item: (item.namespace, item.key))
        start = max(0, op.offset)
        end = start + max(0, op.limit)
        return matches[start:end]

    def _put(self, data: dict[str, Any], op: PutOp) -> None:
        namespace_items = _namespace_items(data, op.namespace)
        if op.value is None:
            namespace_items.pop(op.key, None)
            return
        now = datetime.now(UTC).isoformat()
        existing = namespace_items.get(op.key)
        created_at = existing.get("created_at") if isinstance(existing, dict) else now
        namespace_items[op.key] = {
            "value": op.value,
            "created_at": created_at,
            "updated_at": now,
        }

    def _list_namespaces(self, data: dict[str, Any], op: ListNamespacesOp) -> list[tuple[str, ...]]:
        namespaces = sorted({namespace for namespace, _, _ in _iter_items(data)})
        filtered: list[tuple[str, ...]] = []
        for namespace in namespaces:
            if not _matches_namespace_conditions(namespace, op.match_conditions):
                continue
            if op.max_depth is not None and len(namespace) > op.max_depth:
                namespace = namespace[: op.max_depth]
            if namespace not in filtered:
                filtered.append(namespace)
        start = max(0, op.offset)
        end = start + max(0, op.limit)
        return filtered[start:end]

    def _read_data(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"schema_version": 1, "items": {}}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"schema_version": 1, "items": {}}
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            return {"schema_version": 1, "items": {}}
        return data

    def _write_data(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self._path)


def _namespace_key(namespace: tuple[str, ...]) -> str:
    return "\x1f".join(namespace)


def _namespace_from_key(key: str) -> tuple[str, ...]:
    if not key:
        return ()
    return tuple(part for part in key.split("\x1f") if part)


def _namespace_items(data: dict[str, Any], namespace: tuple[str, ...]) -> dict[str, Any]:
    items = data.setdefault("items", {})
    namespace_key = _namespace_key(namespace)
    namespace_items = items.setdefault(namespace_key, {})
    if not isinstance(namespace_items, dict):
        namespace_items = {}
        items[namespace_key] = namespace_items
    return namespace_items


def _iter_items(data: dict[str, Any]):
    items = data.get("items")
    if not isinstance(items, dict):
        return
    for raw_namespace, namespace_items in items.items():
        if not isinstance(namespace_items, dict):
            continue
        namespace = _namespace_from_key(str(raw_namespace))
        for key, payload in namespace_items.items():
            if isinstance(payload, dict):
                yield namespace, str(key), payload


def _item_from_payload(namespace: tuple[str, ...], key: str, payload: dict[str, Any] | None) -> Item | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), dict):
        return None
    created_at = str(payload.get("created_at") or datetime.now(UTC).isoformat())
    updated_at = str(payload.get("updated_at") or created_at)
    return Item(
        namespace=namespace,
        key=key,
        value=payload["value"],
        created_at=datetime.fromisoformat(created_at),
        updated_at=datetime.fromisoformat(updated_at),
    )


def _namespace_startswith(namespace: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    return len(namespace) >= len(prefix) and namespace[: len(prefix)] == prefix


def _matches_filter(value: dict[str, Any], filter_value: dict[str, Any] | None) -> bool:
    if not filter_value:
        return True
    for key, expected in filter_value.items():
        actual = value.get(key)
        if isinstance(expected, dict):
            for op, operand in expected.items():
                if op == "$eq" and actual != operand:
                    return False
                if op == "$ne" and actual == operand:
                    return False
                if op == "$gt" and not (actual is not None and actual > operand):
                    return False
                if op == "$gte" and not (actual is not None and actual >= operand):
                    return False
                if op == "$lt" and not (actual is not None and actual < operand):
                    return False
                if op == "$lte" and not (actual is not None and actual <= operand):
                    return False
        elif actual != expected:
            return False
    return True


def _matches_namespace_conditions(
    namespace: tuple[str, ...],
    conditions: tuple[MatchCondition, ...] | None,
) -> bool:
    if not conditions:
        return True
    return all(_matches_namespace_condition(namespace, condition) for condition in conditions)


def _matches_namespace_condition(namespace: tuple[str, ...], condition: MatchCondition) -> bool:
    path = tuple(str(part) for part in condition.path)
    if len(path) > len(namespace):
        return False
    if condition.match_type == "prefix":
        return _parts_match(namespace[: len(path)], path)
    if condition.match_type == "suffix":
        return _parts_match(namespace[-len(path) :], path)
    return False


def _parts_match(actual: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    return len(actual) == len(pattern) and all(expected == "*" or item == expected for item, expected in zip(actual, pattern))
