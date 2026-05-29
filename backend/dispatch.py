"""Async dispatch bus: one asyncio.Queue per agent + per-agent worker coroutines.

An agent's lifecycle is one LLM cycle per inbound dispatch: load SOUL.md, load
MEMORY, invoke LLM, persist outbound dispatches. No nested call stack; replies
are just new dispatches in the other direction.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import select

from .db import get_session
from .memory import append_dispatch, build_context
from .models import Dispatch, Run, RunStep
from .runtime import invoke, load_soul
from .state import caller_agent

# Per-agent inbound queues. Populated by start_workers().
_QUEUES: dict[str, asyncio.Queue] = {}
_WORKERS: dict[str, asyncio.Task] = {}


def enqueue(to_agent: str, message: str, from_agent: str = "", reply_to: Optional[str] = None) -> str:
    """Create a Dispatch row + put it on the target's queue. Returns dispatch id."""
    dispatch_id = uuid.uuid4().hex
    with get_session() as s:
        s.add(Dispatch(
            id=dispatch_id,
            from_agent=from_agent,
            to_agent=to_agent,
            message=message,
            reply_to=reply_to,
        ))
        s.commit()
    if to_agent in _QUEUES:
        _QUEUES[to_agent].put_nowait({
            "id": dispatch_id,
            "from": from_agent,
            "message": message,
            "reply_to": reply_to,
        })
    return dispatch_id


async def _worker(agent_name: str) -> None:
    queue = _QUEUES[agent_name]
    while True:
        item = await queue.get()
        try:
            await _process_one(agent_name, item)
        except Exception as exc:
            with get_session() as s:
                d = s.get(Dispatch, item["id"])
                if d:
                    d.status = "failed"
                    d.completed_at = datetime.utcnow()
                    s.add(d)
                    s.commit()
            print(f"[worker {agent_name}] dispatch {item['id']} failed: {exc}")
        finally:
            queue.task_done()


async def _process_one(agent_name: str, item: dict) -> None:
    soul = load_soul(agent_name)
    append_dispatch(agent_name, "in", {"id": item["id"], "from": item["from"], "message": item["message"]})

    with get_session() as s:
        run = Run(agent_name=agent_name, input_text=item["message"])
        s.add(run)
        s.commit()
        s.refresh(run)
        run_id = run.id
        d = s.get(Dispatch, item["id"])
        if d:
            d.status = "processing"
            d.run_id = run_id
            s.add(d)
            s.commit()

    memory_block = build_context(agent_name)
    token = caller_agent.set(agent_name)
    try:
        result = await invoke(soul, item["message"], memory_block=memory_block)
    finally:
        caller_agent.reset(token)

    append_dispatch(agent_name, "result", {"id": item["id"], "text": result.text, "cost_usd": result.cost_usd})

    with get_session() as s:
        run = s.get(Run, run_id)
        run.ended_at = datetime.utcnow()
        run.cost_usd = result.cost_usd
        run.status = "budget_breach" if result.aborted else "completed"
        run.output_text = result.text
        s.add(run)
        for step in result.steps:
            s.add(RunStep(
                run_id=run_id,
                step_type=step.get("type", "unknown"),
                content=str(step),
                tokens_in=step.get("tokens_in", 0),
                tokens_out=step.get("tokens_out", 0),
                cost_delta=step.get("cost", 0.0),
            ))
        d = s.get(Dispatch, item["id"])
        if d:
            d.status = "done"
            d.completed_at = datetime.utcnow()
            s.add(d)
        s.commit()


def start_workers(agent_names: list[str]) -> None:
    """Spawn one inbox queue + worker task per agent. Idempotent."""
    loop = asyncio.get_event_loop()
    for name in agent_names:
        if name not in _QUEUES:
            _QUEUES[name] = asyncio.Queue()
            _WORKERS[name] = loop.create_task(_worker(name), name=f"worker:{name}")


def queue_depth(agent_name: str) -> int:
    q = _QUEUES.get(agent_name)
    return q.qsize() if q else -1


def list_pending() -> list[dict]:
    with get_session() as s:
        rows = s.exec(
            select(Dispatch).where(Dispatch.status.in_(["queued", "processing"])).order_by(Dispatch.created_at)
        ).all()
        return [r.model_dump() for r in rows]
