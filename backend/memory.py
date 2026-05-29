"""Per-agent file-backed MEMORY: dispatches.jsonl (audit) + open_tasks.json (state)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

MEMORY_ROOT = Path(__file__).parent.parent / "memory"


def _agent_dir(agent_name: str) -> Path:
    d = MEMORY_ROOT / agent_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def append_dispatch(agent_name: str, direction: str, payload: dict[str, Any]) -> None:
    """direction = 'in' (received by this agent) or 'out' (sent by this agent)."""
    line = {
        "ts": datetime.utcnow().isoformat(),
        "direction": direction,
        **payload,
    }
    path = _agent_dir(agent_name) / "dispatches.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(line) + "\n")


def load_open_tasks(agent_name: str) -> list[dict]:
    path = _agent_dir(agent_name) / "open_tasks.json"
    if not path.exists():
        return []
    return json.loads(path.read_text() or "[]")


def set_open_tasks(agent_name: str, tasks: list[dict]) -> None:
    path = _agent_dir(agent_name) / "open_tasks.json"
    path.write_text(json.dumps(tasks, indent=2))


def tail_dispatches(agent_name: str, max_chars: int = 8000) -> str:
    path = _agent_dir(agent_name) / "dispatches.jsonl"
    if not path.exists():
        return ""
    text = path.read_text()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def build_context(agent_name: str) -> str:
    """Render MEMORY as a string block to inject into the system prompt."""
    open_tasks = load_open_tasks(agent_name)
    tail = tail_dispatches(agent_name)
    parts = [
        "# MEMORY",
        "## Open tasks (state you maintain across turns; update with set_open_tasks)",
        json.dumps(open_tasks, indent=2) if open_tasks else "(none)",
        "## Recent dispatches (audit log, most recent last)",
        tail.strip() if tail else "(none)",
    ]
    return "\n\n".join(parts)
