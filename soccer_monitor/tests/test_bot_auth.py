"""
Regression test: the Telegram control panel was unauthenticated whenever
TELEGRAM_CHAT_ID was unset or not a number.

int(chat_id) failure was swallowed by a bare `pass`, leaving allowed_chat_id at None,
and the middleware read `if allowed_chat_id and chat_id != allowed_chat_id` -- which is
False for everyone when there is nobody to compare against, so every chat was admitted.
Polling starts whenever the bot *token* alone is set, so this was reachable, and the
panel can change price thresholds and toggle leagues.

It now fails closed: the middleware denies when there is no configured chat, and
start_bot_polling refuses to run at all without one.

Run: python tests/test_bot_auth.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot_ui
from bot_ui import AuthMiddleware

logging.basicConfig(level=logging.CRITICAL)

OWNER = 4242
STRANGER = 9999


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    """Passes isinstance(event, Message) via the class swap below."""

    def __init__(self, chat_id):
        self.chat = FakeChat(chat_id)


async def call_middleware(configured, from_chat):
    """Returns True if the event reached the handler."""
    bot_ui.allowed_chat_id = configured
    reached = []

    async def handler(event, data):
        reached.append(event)
        return "handled"

    original = bot_ui.Message
    bot_ui.Message = FakeMessage  # isinstance() check in the middleware
    try:
        await AuthMiddleware()(handler, FakeMessage(from_chat), {})
    finally:
        bot_ui.Message = original
    return bool(reached)


async def test_unconfigured_chat_id_denies_everyone():
    assert not await call_middleware(None, STRANGER), (
        "with no configured chat the bot admitted a stranger")
    assert not await call_middleware(None, OWNER), (
        "with no configured chat the bot must deny even a plausible id")


async def test_configured_chat_id_admits_only_the_owner():
    assert await call_middleware(OWNER, OWNER), "the owner must still get through"
    assert not await call_middleware(OWNER, STRANGER), "a stranger must not"


async def test_polling_refuses_to_start_without_a_chat_id():
    started = []

    class ExplodingBot:
        def __init__(self, token=None):
            started.append(token)

    original_bot = bot_ui.Bot
    bot_ui.Bot = ExplodingBot
    bot_ui.allowed_chat_id = None
    try:
        for bad in (None, "", "not-a-number"):
            bot_ui.allowed_chat_id = None
            await bot_ui.start_bot_polling("token", config_stub, bad)
            assert started == [], f"polling started with TELEGRAM_CHAT_ID={bad!r}"
    finally:
        bot_ui.Bot = original_bot
        bot_ui.allowed_chat_id = None


class config_stub:
    pass


async def main():
    await test_unconfigured_chat_id_denies_everyone()
    await test_configured_chat_id_admits_only_the_owner()
    await test_polling_refuses_to_start_without_a_chat_id()
    print("OK: the control bot fails closed without a valid TELEGRAM_CHAT_ID")


if __name__ == "__main__":
    asyncio.run(main())
