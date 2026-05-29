from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Run(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    agent_name: str
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    cost_usd: float = 0.0
    status: str = "running"  # running | completed | budget_breach | failed
    input_text: str = ""
    output_text: str = ""


class RunStep(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="run.id")
    step_type: str  # llm_call | tool_call | dispatch_out | dispatch_in
    content: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_delta: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Dispatch(SQLModel, table=True):
    id: Optional[str] = Field(default=None, primary_key=True)  # uuid hex
    from_agent: str = ""   # empty string = external (cron, telegram, http)
    to_agent: str
    message: str
    reply_to: Optional[str] = None
    status: str = "queued"  # queued | processing | done | failed
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    run_id: Optional[int] = Field(default=None, foreign_key="run.id")
