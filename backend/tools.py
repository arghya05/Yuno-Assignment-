"""LLM-callable tools. `@tool` registers a Python fn so the runtime can expose it to Claude."""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

_REGISTRY: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, input_schema: dict[str, Any]) -> Callable:
    def decorator(fn: Callable) -> Callable:
        _REGISTRY[name] = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "fn": fn,
        }
        return fn
    return decorator


def get_tool_specs(allowed: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for n, t in _REGISTRY.items()
        if n in allowed
    ]


async def call_tool(name: str, args: dict[str, Any]) -> Any:
    fn = _REGISTRY[name]["fn"]
    if inspect.iscoroutinefunction(fn):
        return await fn(**args)
    return fn(**args)


# ---- mock payment tools ----

_TXNS = [
    {"id": "txn_001", "amount": 4200.00, "merchant": "XYZ Electronics", "country": "RO"},
    {"id": "txn_002", "amount": 89.50, "merchant": "Coffee Place", "country": "US"},
    {"id": "txn_003", "amount": 1250.00, "merchant": "Hotel Bookings", "country": "FR"},
    {"id": "txn_004", "amount": 35.20, "merchant": "Grocery Store", "country": "US"},
    {"id": "txn_005", "amount": 2890.00, "merchant": "Crypto Exchange", "country": "BG"},
]


@tool(
    name="recent_transactions",
    description="Return transactions from the last hour. Takes no arguments.",
    input_schema={"type": "object", "properties": {}, "required": []},
)
def recent_transactions() -> list[dict]:
    return _TXNS


@tool(
    name="lookup_transaction",
    description="Look up full details and history for one transaction by id.",
    input_schema={
        "type": "object",
        "properties": {"txn_id": {"type": "string"}},
        "required": ["txn_id"],
    },
)
def lookup_transaction(txn_id: str) -> dict:
    for t in _TXNS:
        if t["id"] == txn_id:
            return {
                **t,
                "history": ["2 chargebacks in last 30 days", "card first seen 14 days ago"],
            }
    return {"error": f"transaction {txn_id} not found"}


@tool(
    name="fraud_score",
    description="Compute a fraud risk score in [0, 1] for a transaction id.",
    input_schema={
        "type": "object",
        "properties": {"txn_id": {"type": "string"}},
        "required": ["txn_id"],
    },
)
def fraud_score(txn_id: str) -> dict:
    # Deterministic mock so demo replays consistently. txn_001 and txn_005 score high.
    high = {"txn_001": 0.92, "txn_005": 0.78}
    return {"txn_id": txn_id, "score": high.get(txn_id, 0.15)}
