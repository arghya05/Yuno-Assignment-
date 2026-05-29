"""Telegram long-polling client + send_telegram_message tool.

Boots gracefully without a token (channel = disabled). When TELEGRAM_BOT_TOKEN
is set and validated via getMe, the poller spawns and routes every inbound
text message to concierge's dispatch queue.

Inbound message format injected into concierge:
    [telegram_inbound chat_id=<chat_id>] <user text>

Concierge is expected to extract chat_id and reply via send_telegram_message.
It should also persist chat_id in open_tasks so it can correlate replies.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

from .dispatch import enqueue
from .tools import tool

BASE_URL = "https://api.telegram.org"

_token: str = ""
_enabled: bool = False
_poll_task: Optional[asyncio.Task] = None
_bot_username: str = ""


def is_enabled() -> bool:
    return _enabled


def status() -> dict[str, Any]:
    return {"enabled": _enabled, "bot_username": _bot_username if _enabled else None}


async def _get(method: str, params: Optional[dict] = None, timeout: float = 35.0) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(f"{BASE_URL}/bot{_token}/{method}", params=params or {})
        r.raise_for_status()
        return r.json()


async def _post(method: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(f"{BASE_URL}/bot{_token}/{method}", json=data)
        r.raise_for_status()
        return r.json()


async def _poll_loop() -> None:
    offset = 0
    print("[telegram] long-polling started")
    while True:
        try:
            resp = await _get("getUpdates", {"offset": offset, "timeout": 30}, timeout=35.0)
            for u in resp.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message")
                if not msg or not msg.get("text"):
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                enqueue(
                    to_agent="concierge",
                    message=f"[telegram_inbound chat_id={chat_id}] {text}",
                    from_agent=f"telegram:{chat_id}",
                )
                print(f"[telegram] inbound chat_id={chat_id}: {text[:80]}")
        except Exception as exc:
            print(f"[telegram] poll error: {exc}")
            await asyncio.sleep(2)


async def start() -> None:
    """Validate token via getMe, then spawn poll loop. No-op if no token."""
    global _token, _enabled, _poll_task, _bot_username
    _token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not _token:
        print("[telegram] disabled (no TELEGRAM_BOT_TOKEN)")
        return
    try:
        me = await _get("getMe", timeout=5.0)
        _bot_username = me["result"]["username"]
    except Exception as exc:
        print(f"[telegram] disabled (getMe failed: {exc})")
        return
    _enabled = True
    print(f"[telegram] enabled as @{_bot_username}")
    loop = asyncio.get_event_loop()
    _poll_task = loop.create_task(_poll_loop(), name="telegram:poll")


@tool(
    name="send_telegram_message",
    description=(
        "Send a Telegram message to a chat. Use this to reply to a human user. "
        "The chat_id is provided in the inbound message format "
        "`[telegram_inbound chat_id=<chat_id>] ...` and you should also persist "
        "it in your open_tasks so you can reach the same user on subsequent turns."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "chat_id": {"type": ["string", "integer"]},
            "text": {"type": "string"},
        },
        "required": ["chat_id", "text"],
    },
)
async def send_telegram_message(chat_id, text):
    if not _enabled:
        return {"error": "telegram not enabled; set TELEGRAM_BOT_TOKEN and restart"}
    await _post("sendMessage", {"chat_id": chat_id, "text": text})
    return {"sent": True, "chat_id": chat_id}
