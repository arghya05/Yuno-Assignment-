"""Shared async-safe state. Kept tiny to avoid import cycles."""
from __future__ import annotations

from contextvars import ContextVar

# Set by the dispatch worker before invoking an agent's LLM. Read by tools
# (dispatch, set_open_tasks) to know which agent is calling them.
caller_agent: ContextVar[str] = ContextVar("caller_agent", default="")
