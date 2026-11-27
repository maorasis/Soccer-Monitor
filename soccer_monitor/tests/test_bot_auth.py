"""Regression test: the Telegram control panel must fail closed.

It was once unauthenticated whenever TELEGRAM_CHAT_ID was unset or not a number. The
middleware now denies when there is no configured chat, and start_bot_polling refuses
to run at all without one.
"""
import asyncio
import logging
import unittest
from unittest.mock import patch

import bot_ui
from bot_ui import AuthMiddleware

OWNER = 4242
STRANGER = 9999


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat_id):
        self.chat = FakeChat(chat_id)


async def reaches_handler(configured, from_chat):
    reached = []

    async def handler(event, data):
        reached.append(event)

    with patch.object(bot_ui, "allowed_chat_id", configured), patch.object(bot_ui, "Message", FakeMessage):
        await AuthMiddleware()(handler, FakeMessage(from_chat), {})
    return bool(reached)


class BotAuthTests(unittest.TestCase):
    def test_unconfigured_chat_id_denies_everyone(self):
        self.assertFalse(asyncio.run(reaches_handler(None, STRANGER)))
        self.assertFalse(asyncio.run(reaches_handler(None, OWNER)))

    def test_configured_chat_id_admits_only_the_owner(self):
        self.assertTrue(asyncio.run(reaches_handler(OWNER, OWNER)))
        self.assertFalse(asyncio.run(reaches_handler(OWNER, STRANGER)))

    def test_polling_refuses_to_start_without_a_chat_id(self):
        started = []

        class RecordingBot:
            def __init__(self, token=None):
                started.append(token)

        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        with patch.object(bot_ui, "Bot", RecordingBot), patch.object(bot_ui, "config", bot_ui.config), \
                patch.object(bot_ui, "allowed_chat_id", None):
            for bad in (None, "", "not-a-number"):
                bot_ui.allowed_chat_id = None
                asyncio.run(bot_ui.start_bot_polling("token", object(), bad))
        self.assertEqual(started, [])


if __name__ == "__main__":
    unittest.main()
