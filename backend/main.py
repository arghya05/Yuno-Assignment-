from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import select

from . import dispatch_tools  # noqa: F401  — registers dispatch + set_open_tasks tools
from . import scheduler as agent_scheduler
from . import telegram as telegram_channel  # noqa: F401  — registers send_telegram_message
from .db import get_session, init_db
from .dispatch import enqueue, list_pending, queue_depth, start_workers
from .models import Dispatch, Run, RunStep
from .runtime import AGENTS_DIR, invoke, load_soul

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    agent_names = [p.stem for p in sorted(AGENTS_DIR.glob("*.md"))]
    start_workers(agent_names)
    agent_scheduler.start()
    await telegram_channel.start()
    yield


app = FastAPI(title="Yuno Agents", lifespan=lifespan)
FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


@app.get("/")
def root():
    if FRONTEND.exists():
        return FileResponse(FRONTEND)
    return {
        "status": "ok",
        "ui": "frontend not yet built",
        "try": ["GET /agents", "POST /agents/{name}/invoke"],
    }


@app.get("/agents")
def list_agents():
    return [p.stem for p in sorted(AGENTS_DIR.glob("*.md"))]


@app.get("/agents/commits")
def list_agent_commits():
    """Recent git commits that touched agents/. Empty list if not a git repo or no commits."""
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-n", "30", "--", "agents/"],
            capture_output=True, text=True, check=True, cwd=Path(__file__).parent.parent,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    commits = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        sha, _, message = line.partition(" ")
        commits.append({"sha": sha, "message": message})
    return commits


@app.get("/agents/diff")
def get_agent_diff(
    from_sha: str = Query(..., alias="from", min_length=4, max_length=40, pattern=r"^[0-9a-f]+$"),
    to_sha: str = Query(..., alias="to", min_length=4, max_length=40, pattern=r"^[0-9a-f]+$"),
):
    """git diff <from>..<to> -- agents/ as plain text. Shas validated as hex."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{from_sha}..{to_sha}", "--", "agents/"],
            capture_output=True, text=True, check=True, cwd=Path(__file__).parent.parent,
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(500, f"git diff failed: {exc.stderr.strip() or exc}")
    return {"from": from_sha, "to": to_sha, "diff": result.stdout}


@app.get("/agents/{name}")
def get_agent(name: str):
    try:
        soul = load_soul(name)
    except FileNotFoundError:
        raise HTTPException(404, f"agent {name} not found")
    return {"name": soul.name, "frontmatter": soul.frontmatter, "body": soul.body}


@app.put("/agents/{name}")
def update_agent(name: str, payload: dict):
    """Write SOUL.md to disk. Next dispatch will load the new version (no restart)."""
    import yaml
    path = AGENTS_DIR / f"{name}.md"
    if not path.exists():
        raise HTTPException(404, f"agent {name} not found")
    frontmatter = payload.get("frontmatter", {})
    body = payload.get("body", "")
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    path.write_text(f"---\n{yaml_text}\n---\n{body}\n")
    return {"saved": name}


@app.post("/agents/{name}/invoke")
async def invoke_agent(name: str, payload: dict):
    try:
        soul = load_soul(name)
    except FileNotFoundError:
        raise HTTPException(404, f"agent {name} not found")
    user_message = payload.get("message", "")

    with get_session() as session:
        run = Run(agent_name=name, input_text=user_message)
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    result = await invoke(soul, user_message)

    with get_session() as session:
        run = session.get(Run, run_id)
        run.ended_at = datetime.utcnow()
        run.cost_usd = result.cost_usd
        run.status = "budget_breach" if result.aborted else "completed"
        run.output_text = result.text
        session.add(run)
        for s in result.steps:
            session.add(RunStep(
                run_id=run_id,
                step_type=s.get("type", "unknown"),
                content=str(s),
                tokens_in=s.get("tokens_in", 0),
                tokens_out=s.get("tokens_out", 0),
                cost_delta=s.get("cost", 0.0),
            ))
        session.commit()

    return {
        "run_id": run_id,
        "text": result.text,
        "cost_usd": result.cost_usd,
        "aborted": result.aborted,
        "abort_reason": result.abort_reason,
        "steps": result.steps,
    }


@app.get("/runs")
def list_runs():
    with get_session() as session:
        runs = session.exec(select(Run).order_by(Run.started_at.desc()).limit(50)).all()
        return [r.model_dump() for r in runs]


@app.post("/dispatch")
def post_dispatch(payload: dict):
    """Inject a dispatch from outside (cron, Telegram inbound, manual testing)."""
    to_agent = payload.get("to_agent")
    message = payload.get("message", "")
    if not to_agent:
        raise HTTPException(400, "to_agent is required")
    if (AGENTS_DIR / f"{to_agent}.md").exists() is False:
        raise HTTPException(404, f"agent {to_agent} not found")
    dispatch_id = enqueue(
        to_agent=to_agent,
        message=message,
        from_agent=payload.get("from_agent", ""),
        reply_to=payload.get("reply_to"),
    )
    return {"dispatch_id": dispatch_id, "queued_for": to_agent, "queue_depth": queue_depth(to_agent)}


@app.get("/dispatches")
def list_dispatches(limit: int = 50):
    with get_session() as session:
        rows = session.exec(
            select(Dispatch).order_by(Dispatch.created_at.desc()).limit(limit)
        ).all()
        return [r.model_dump() for r in rows]


@app.get("/dispatches/pending")
def get_pending():
    return list_pending()


@app.get("/scheduler/jobs")
def scheduler_jobs():
    return agent_scheduler.list_jobs()


@app.post("/scheduler/fire/{agent_name}")
def scheduler_fire(agent_name: str):
    if not (AGENTS_DIR / f"{agent_name}.md").exists():
        raise HTTPException(404, f"agent {agent_name} not found")
    dispatch_id = agent_scheduler.fire_now(agent_name)
    return {"dispatch_id": dispatch_id, "fired": agent_name}


@app.get("/metrics")
def get_metrics():
    """Aggregates for the dashboard: per-agent run count + cost, channel status, queue depths."""
    with get_session() as session:
        runs = session.exec(select(Run)).all()
    by_agent: dict[str, dict] = {}
    total_cost = 0.0
    completed = 0
    budget_breach = 0
    for r in runs:
        total_cost += r.cost_usd
        a = by_agent.setdefault(r.agent_name, {
            "runs": 0, "cost_usd": 0.0, "completed": 0, "budget_breach": 0,
            "last_run_at": None, "avg_cost_usd": 0.0,
        })
        a["runs"] += 1
        a["cost_usd"] += r.cost_usd
        if r.status == "completed":
            a["completed"] += 1
            completed += 1
        elif r.status == "budget_breach":
            a["budget_breach"] += 1
            budget_breach += 1
        ts = r.ended_at or r.started_at
        if a["last_run_at"] is None or (ts and ts.isoformat() > a["last_run_at"]):
            a["last_run_at"] = ts.isoformat() if ts else None
    for a in by_agent.values():
        a["avg_cost_usd"] = a["cost_usd"] / a["runs"] if a["runs"] else 0.0
    return {
        "total_runs": len(runs),
        "total_cost_usd": total_cost,
        "completed": completed,
        "budget_breach": budget_breach,
        "by_agent": by_agent,
        "telegram": telegram_channel.status(),
        "queue_depths": {
            name: queue_depth(name)
            for name in [p.stem for p in sorted(AGENTS_DIR.glob("*.md"))]
        },
    }
