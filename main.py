"""
PixKeep Bot: Telegram bot (admin-only) that takes photos and any files,
optionally cleans metadata / fixes dark photos and uploads them to Google Drive
(several Google accounts per user, one active at a time).

Flow: files -> (settings applied silently) -> folder name -> upload to the
active Google account's Drive (per-file, continue on error) -> report +
public link -> delete succeeded messages.

Code lives in the pixkeep/ package; this file only wires it together.
Version history: CHANGELOG.md.
"""
import asyncio
import logging
import os
import shutil

from aiogram import Dispatcher
from aiogram.filters import BaseFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from pixkeep.config import config
from pixkeep.db import keep_alive_loop
from pixkeep.handlers import routers
from pixkeep.runtime import bot
from pixkeep.web import run_web

logging.basicConfig(level=logging.INFO)


class IsAdmin(BaseFilter):
    """Global gate: only config.ADMIN_IDS may use the bot. Everyone else's
    messages/callbacks are simply not handled — no reply, no acknowledgment
    that the bot exists. Root-level filters on the dispatcher are checked
    before any router is entered, so every handler is covered."""
    async def __call__(self, event) -> bool:
        user = event.from_user
        return user is not None and user.id in config.ADMIN_IDS


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.filter(IsAdmin())
    dp.callback_query.filter(IsAdmin())
    dp.include_routers(*routers())
    return dp


BOT_COMMANDS = [
    BotCommand(command="start", description="Подключить Диск / статус"),
    BotCommand(command="uploads", description="Мои загрузки: ссылки и удаление"),
    BotCommand(command="accounts", description="Аккаунты: список и переключение"),
    BotCommand(command="settings", description="Настройки очистки и переименования"),
    BotCommand(command="logout", description="Отключить все аккаунты"),
    BotCommand(command="help", description="Справка"),
    BotCommand(command="version", description="Какая версия кода сейчас работает"),
]



async def main():
    shutil.rmtree(config.TEMP_DIR, ignore_errors=True)   # leftovers of a previous run
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    dp = build_dispatcher()
    await bot.set_my_commands(BOT_COMMANDS)
    await run_web()
    keepalive_task = asyncio.create_task(keep_alive_loop())
    try:
        await dp.start_polling(bot)
    finally:
        keepalive_task.cancel()
        await asyncio.gather(keepalive_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
