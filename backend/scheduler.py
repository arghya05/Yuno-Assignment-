"""APScheduler: read SOUL.md `schedule:` field, fire autonomous agents on cron.

Each agent with a `schedule:` field in its frontmatter gets a CronTrigger that
enqueues a scheduled-wakeup dispatch into that agent's queue.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import runtime
from .dispatch import enqueue
from .runtime import load_soul

_scheduler: Optional[AsyncIOScheduler] = None


def start() -> None:
    global _scheduler
    _scheduler = AsyncIOScheduler()
    for path in sorted(runtime.AGENTS_DIR.glob("*.md")):
        soul = load_soul(path.stem)
        schedule = soul.frontmatter.get("schedule")
        if not schedule:
            continue
        try:
            trigger = CronTrigger.from_crontab(schedule)
        except Exception as exc:
            print(f"[scheduler] {soul.name}: invalid cron '{schedule}': {exc}")
            continue
        _scheduler.add_job(
            _fire_agent,
            trigger=trigger,
            id=f"agent:{soul.name}",
            args=[soul.name],
            replace_existing=True,
        )
        print(f"[scheduler] registered {soul.name} on cron '{schedule}'")
    _scheduler.start()


def _fire_agent(agent_name: str) -> None:
    enqueue(
        to_agent=agent_name,
        message=f"Scheduled wake-up at {datetime.utcnow().isoformat()}. Run your standard workflow.",
        from_agent="scheduler",
    )


def list_jobs() -> list[dict]:
    if not _scheduler:
        return []
    return [
        {
            "id": j.id,
            "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
        }
        for j in _scheduler.get_jobs()
    ]


def fire_now(agent_name: str) -> str:
    return enqueue(
        to_agent=agent_name,
        message=f"Manual scheduler trigger at {datetime.utcnow().isoformat()}. Run your standard workflow.",
        from_agent="scheduler:manual",
    )
