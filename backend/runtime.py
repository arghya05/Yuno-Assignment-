"""Custom runtime: load a SOUL.md, call Claude with its tools, track cost.

One agent turn = one LLM cycle: read input → decide → emit text or tool calls.
Tool calls run in-process; the loop continues until stop_reason != 'tool_use'
or the per-agent max_cost_usd is exceeded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from anthropic import AsyncAnthropic

from .tools import call_tool, get_tool_specs

# Rough Anthropic pricing in USD per 1M tokens (input, output). Update from console.
PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-opus-4-7": (15.00, 75.00),
}

AGENTS_DIR = Path(__file__).parent.parent / "agents"


@dataclass
class SoulMd:
    name: str
    body: str
    frontmatter: dict[str, Any]
    path: Path

    @property
    def model(self) -> str:
        return self.frontmatter.get("model", "claude-haiku-4-5-20251001")

    @property
    def tools(self) -> list[str]:
        return self.frontmatter.get("tools") or []

    @property
    def max_cost_usd(self) -> float:
        return float(self.frontmatter.get("max_cost_usd", 0.05))

    def system_prompt(self, memory_block: str = "") -> str:
        meta = self.frontmatter
        lines = [f"You are agent `{meta.get('name', self.name)}`."]
        dispatches = meta.get("dispatches_to") or []
        if dispatches:
            lines.append(
                "\nYou may dispatch to other agents using the `dispatch` tool. "
                "Routing guidance (use these as conditions to decide when to call dispatch):"
            )
            for d in dispatches:
                if isinstance(d, dict):
                    lines.append(f"  - {d.get('agent')}: when {d.get('when', '(unspecified)')}")
                else:
                    lines.append(f"  - {d}")
        prompt = "\n".join(lines) + "\n\n" + self.body
        if memory_block:
            prompt += "\n\n" + memory_block
        return prompt


def load_soul(name: str) -> SoulMd:
    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text()
    if text.startswith("---"):
        _, fm_text, body = text.split("---", 2)
        frontmatter = yaml.safe_load(fm_text) or {}
    else:
        frontmatter = {}
        body = text
    return SoulMd(
        name=frontmatter.get("name", name),
        body=body.strip(),
        frontmatter=frontmatter,
        path=path,
    )


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = PRICING.get(model, (3.00, 15.00))
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


@dataclass
class InvokeResult:
    text: str
    cost_usd: float
    steps: list[dict] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str | None = None


async def invoke(soul: SoulMd, user_message: str, memory_block: str = "") -> InvokeResult:
    client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    # `dispatch` and `set_open_tasks` are always available to any agent that
    # declares dispatches_to or has channels (they're harmless if unused).
    allowed = list(soul.tools)
    if soul.frontmatter.get("dispatches_to"):
        allowed.append("dispatch")
    allowed.append("set_open_tasks")
    tool_specs = get_tool_specs(allowed)
    messages: list[dict] = [{"role": "user", "content": user_message}]
    total_cost = 0.0
    steps: list[dict] = []

    while True:
        kwargs: dict[str, Any] = {
            "model": soul.model,
            "max_tokens": 1024,
            "system": soul.system_prompt(memory_block),
            "messages": messages,
        }
        if tool_specs:
            kwargs["tools"] = tool_specs

        resp = await client.messages.create(**kwargs)
        step_cost = cost_for(soul.model, resp.usage.input_tokens, resp.usage.output_tokens)
        total_cost += step_cost
        steps.append({
            "type": "llm_call",
            "tokens_in": resp.usage.input_tokens,
            "tokens_out": resp.usage.output_tokens,
            "cost": step_cost,
            "stop_reason": resp.stop_reason,
        })

        if total_cost > soul.max_cost_usd:
            return InvokeResult(
                text="(aborted: budget breach)",
                cost_usd=total_cost,
                steps=steps,
                aborted=True,
                abort_reason=f"budget ${total_cost:.4f} exceeded cap ${soul.max_cost_usd:.4f}",
            )

        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text_out = "".join(b.text for b in resp.content if b.type == "text")
            return InvokeResult(text=text_out, cost_usd=total_cost, steps=steps)

        tool_results = []
        for block in resp.content:
            if block.type != "tool_use":
                continue
            try:
                result = await call_tool(block.name, dict(block.input))
                steps.append({
                    "type": "tool_call",
                    "name": block.name,
                    "input": dict(block.input),
                    "result": result,
                })
            except Exception as exc:
                result = {"error": str(exc)}
                steps.append({
                    "type": "tool_call",
                    "name": block.name,
                    "input": dict(block.input),
                    "error": str(exc),
                })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })
        messages.append({"role": "user", "content": tool_results})
