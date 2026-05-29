"""Shared test fixtures.

Tests run offline: no Anthropic API calls, no Telegram, no real cron firing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure backend is importable
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Stub the API key so .env loading is not required for tests.
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture
def isolated_memory(tmp_path, monkeypatch):
    """Redirect MEMORY writes to a tmp dir so tests don't touch the real memory/."""
    from backend import memory
    monkeypatch.setattr(memory, "MEMORY_ROOT", tmp_path / "memory")
    return tmp_path / "memory"


@pytest.fixture
def isolated_agents(tmp_path, monkeypatch):
    """Redirect agent loading to a tmp dir."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    from backend import runtime
    monkeypatch.setattr(runtime, "AGENTS_DIR", agents_dir)
    return agents_dir


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeBlock:
    def __init__(self, type: str, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class FakeResponse:
    def __init__(self, content, stop_reason, input_tokens, output_tokens):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = FakeUsage(input_tokens, output_tokens)


def make_text_response(text: str, input_tokens: int = 50, output_tokens: int = 20) -> FakeResponse:
    return FakeResponse(
        content=[FakeBlock("text", text=text)],
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def make_tool_call_response(tool_name: str, tool_input: dict, tool_use_id: str = "tu_1",
                            input_tokens: int = 60, output_tokens: int = 30) -> FakeResponse:
    return FakeResponse(
        content=[FakeBlock("tool_use", id=tool_use_id, name=tool_name, input=tool_input)],
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class FakeMessages:
    """Records calls and returns canned responses in order."""
    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            return make_text_response("(no more canned responses)")
        return self._responses.pop(0)


class FakeAnthropic:
    def __init__(self, responses: list[FakeResponse], **kwargs):
        self.messages = FakeMessages(responses)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Patch backend.runtime.AsyncAnthropic to a fake. Configure per-test via .set_responses()."""
    holder = {"client": None}

    def factory(**kwargs):
        return holder["client"]

    monkeypatch.setattr("backend.runtime.AsyncAnthropic", factory)

    class Controller:
        def set_responses(self, responses):
            holder["client"] = FakeAnthropic(responses)
            return holder["client"]

    return Controller()
