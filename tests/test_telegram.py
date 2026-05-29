"""Telegram channel: graceful boot without token, send-tool errors, inbound parsing."""
from __future__ import annotations

import pytest


def test_status_disabled_when_no_token(monkeypatch):
    from backend import telegram as tg
    monkeypatch.setattr(tg, "_enabled", False)
    monkeypatch.setattr(tg, "_bot_username", "")
    s = tg.status()
    assert s == {"enabled": False, "bot_username": None}


def test_status_enabled_returns_username(monkeypatch):
    from backend import telegram as tg
    monkeypatch.setattr(tg, "_enabled", True)
    monkeypatch.setattr(tg, "_bot_username", "yuno_bot")
    s = tg.status()
    assert s == {"enabled": True, "bot_username": "yuno_bot"}


@pytest.mark.asyncio
async def test_send_tool_returns_error_when_disabled(monkeypatch):
    from backend import telegram as tg
    monkeypatch.setattr(tg, "_enabled", False)
    result = await tg.send_telegram_message(chat_id=123, text="hi")
    assert "error" in result
    assert "telegram not enabled" in result["error"]


@pytest.mark.asyncio
async def test_send_tool_posts_when_enabled(monkeypatch):
    """When enabled, send_telegram_message POSTs to sendMessage with the right payload."""
    captured = {}

    async def fake_post(method, data):
        captured["method"] = method
        captured["data"] = data
        return {"ok": True}

    from backend import telegram as tg
    monkeypatch.setattr(tg, "_enabled", True)
    monkeypatch.setattr(tg, "_post", fake_post)
    result = await tg.send_telegram_message(chat_id=999, text="hello there")

    assert result["sent"] is True
    assert captured["method"] == "sendMessage"
    assert captured["data"] == {"chat_id": 999, "text": "hello there"}


def test_inbound_format_is_documented():
    """The inbound-format contract is part of concierge's prompt. If it changes, the LLM
    will have to be re-prompted. This test pins the convention so it can't silently break."""
    from pathlib import Path
    root = Path(__file__).parent.parent
    concierge = (root / "agents" / "concierge.md").read_text()
    assert "telegram_inbound chat_id" in concierge
    assert "send_telegram_message" in concierge
