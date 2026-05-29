"""Workflow + budget + dispatch tests with a mocked Anthropic client."""
from __future__ import annotations

import textwrap

import pytest


# Local helpers (the FakeAnthropic factory in conftest consumes these via fixture).
class _Usage:
    def __init__(self, i, o): self.input_tokens, self.output_tokens = i, o


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        for k, v in kw.items(): setattr(self, k, v)


class _Resp:
    def __init__(self, content, stop_reason, i, o):
        self.content, self.stop_reason, self.usage = content, stop_reason, _Usage(i, o)


def make_text_response(text, input_tokens=50, output_tokens=20):
    return _Resp([_Block("text", text=text)], "end_turn", input_tokens, output_tokens)


def make_tool_call_response(name, input, tool_use_id="tu_1", input_tokens=60, output_tokens=30):
    return _Resp([_Block("tool_use", id=tool_use_id, name=name, input=input)], "tool_use",
                 input_tokens, output_tokens)


def write_soul(agents_dir, name, frontmatter_yaml, body):
    (agents_dir / f"{name}.md").write_text(f"---\n{frontmatter_yaml}\n---\n{body}\n")


@pytest.mark.asyncio
async def test_invoke_returns_text(isolated_agents, fake_anthropic):
    write_soul(isolated_agents, "echoer", textwrap.dedent("""
        name: echoer
        mode: autonomous
        model: claude-haiku-4-5
        max_cost_usd: 0.10
    """).strip(), "# Role\nEcho.")

    fake_anthropic.set_responses([make_text_response("hi back")])
    from backend.runtime import invoke, load_soul

    soul = load_soul("echoer")
    result = await invoke(soul, "hi")
    assert result.text == "hi back"
    assert not result.aborted
    assert result.cost_usd > 0
    assert len(result.steps) == 1
    assert result.steps[0]["type"] == "llm_call"


@pytest.mark.asyncio
async def test_invoke_budget_breach(isolated_agents, fake_anthropic):
    """A hugely expensive first call exceeds max_cost_usd; result is aborted."""
    write_soul(isolated_agents, "spender", textwrap.dedent("""
        name: spender
        mode: autonomous
        model: claude-haiku-4-5
        max_cost_usd: 0.00001
    """).strip(), "# Role\nSpend money.")

    # 1M tokens at haiku rates = $1 input + $5 output → trivially over the cap
    fake_anthropic.set_responses([make_text_response("burn", input_tokens=1_000_000, output_tokens=1_000_000)])
    from backend.runtime import invoke, load_soul

    soul = load_soul("spender")
    result = await invoke(soul, "go")
    assert result.aborted is True
    assert "budget" in (result.abort_reason or "")
    assert result.cost_usd > soul.max_cost_usd


@pytest.mark.asyncio
async def test_invoke_runs_tool_loop(isolated_agents, fake_anthropic):
    """First response is a tool_use; runtime executes the tool and continues until end_turn."""
    write_soul(isolated_agents, "tooly", textwrap.dedent("""
        name: tooly
        mode: autonomous
        model: claude-haiku-4-5
        max_cost_usd: 0.10
        tools: [recent_transactions]
    """).strip(), "# Role\nUse tools.")

    fake_anthropic.set_responses([
        make_tool_call_response("recent_transactions", {}, tool_use_id="tu_1"),
        make_text_response("found 5 txns"),
    ])
    from backend.runtime import invoke, load_soul

    soul = load_soul("tooly")
    result = await invoke(soul, "scan")
    assert result.text == "found 5 txns"
    tool_steps = [s for s in result.steps if s["type"] == "tool_call"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["name"] == "recent_transactions"
    assert isinstance(tool_steps[0]["result"], list)


def test_memory_append_and_load(isolated_memory):
    from backend.memory import append_dispatch, build_context, load_open_tasks, set_open_tasks

    append_dispatch("alpha", "in", {"id": "d1", "from": "ext", "message": "hello"})
    append_dispatch("alpha", "out", {"id": "d2", "to": "beta", "message": "reply"})

    set_open_tasks("alpha", [{"task_id": "t1", "txn_id": "x"}])
    assert load_open_tasks("alpha") == [{"task_id": "t1", "txn_id": "x"}]

    block = build_context("alpha")
    assert "# MEMORY" in block
    assert "t1" in block
    assert "hello" in block
    assert "reply" in block


def test_dispatch_tool_whitelist(isolated_agents, isolated_memory, monkeypatch):
    """The dispatch tool refuses targets not in the caller's dispatches_to whitelist."""
    write_soul(isolated_agents, "boss", textwrap.dedent("""
        name: boss
        mode: autonomous
        model: claude-haiku-4-5
        dispatches_to:
          - agent: worker
            when: "task arrives"
    """).strip(), "# Role")
    write_soul(isolated_agents, "worker", "name: worker\nmodel: claude-haiku-4-5", "# Role")

    # patch the enqueue used inside dispatch_tools to avoid touching the real bus
    captured = {}
    def fake_enqueue(to_agent, message, from_agent="", reply_to=None):
        captured["to"] = to_agent
        captured["from"] = from_agent
        return "fake_dispatch_id"
    monkeypatch.setattr("backend.dispatch_tools.enqueue", fake_enqueue)

    from backend.state import caller_agent
    from backend.dispatch_tools import dispatch_tool

    # allowed target
    token = caller_agent.set("boss")
    try:
        out = dispatch_tool(target_agent="worker", message="hi")
        assert out["dispatch_id"] == "fake_dispatch_id"
        assert captured["to"] == "worker"
        assert captured["from"] == "boss"

        # disallowed target
        out2 = dispatch_tool(target_agent="ghost", message="hi")
        assert "error" in out2
        assert "not allowed" in out2["error"]
    finally:
        caller_agent.reset(token)


def test_dispatch_tool_rejects_when_no_caller(monkeypatch):
    monkeypatch.setattr("backend.dispatch_tools.enqueue", lambda **k: "x")
    from backend.dispatch_tools import dispatch_tool
    out = dispatch_tool(target_agent="anyone", message="hi")
    assert "error" in out
    assert "caller" in out["error"]
