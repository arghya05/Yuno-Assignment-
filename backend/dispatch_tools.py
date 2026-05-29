"""Tools that bridge LLM calls into the dispatch bus + MEMORY.

Importing this module registers `dispatch` and `set_open_tasks` into the
shared tool registry. main.py imports it at startup.
"""
from __future__ import annotations

from typing import Optional

from .dispatch import enqueue
from .memory import set_open_tasks as _set_open_tasks_file
from .runtime import load_soul
from .state import caller_agent
from .tools import tool


def _allowed_targets(agent_name: str) -> list[str]:
    soul = load_soul(agent_name)
    out = []
    for d in soul.frontmatter.get("dispatches_to") or []:
        if isinstance(d, dict):
            target = d.get("agent")
            if target:
                out.append(target)
        else:
            out.append(d)
    return out


@tool(
    name="dispatch",
    description=(
        "Send a message to another agent. Returns the new dispatch_id. "
        "You may only dispatch to agents in your SOUL.md `dispatches_to:` whitelist. "
        "Use `reply_to` (the inbound dispatch id) when responding to a previous request."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "target_agent": {"type": "string"},
            "message": {"type": "string"},
            "reply_to": {"type": "string"},
        },
        "required": ["target_agent", "message"],
    },
)
def dispatch_tool(target_agent: str, message: str, reply_to: Optional[str] = None):
    caller = caller_agent.get("")
    if not caller:
        return {"error": "dispatch tool called with no caller context"}
    allowed = _allowed_targets(caller)
    if target_agent not in allowed:
        return {"error": f"{caller} not allowed to dispatch to {target_agent}", "allowed": allowed}
    dispatch_id = enqueue(target_agent, message, from_agent=caller, reply_to=reply_to)
    return {"dispatch_id": dispatch_id, "queued_for": target_agent}


@tool(
    name="set_open_tasks",
    description=(
        "Replace your open_tasks list with the provided list. This is your "
        "agent-local memory of outstanding tasks across turns. Structure of "
        "each task object is up to you, but should include enough info to "
        "correlate inbound replies to the task that prompted them."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
        "required": ["tasks"],
    },
)
def set_open_tasks_tool(tasks: list[dict]):
    caller = caller_agent.get("")
    if not caller:
        return {"error": "set_open_tasks called with no caller context"}
    _set_open_tasks_file(caller, tasks)
    return {"saved": len(tasks)}
