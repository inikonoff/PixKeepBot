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
import contextlib
import logging
import os
import shutil

from aiogram import Dispatcher
from aiogram.filters import BaseFilter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from pixkeep import db, scheduler
from pixkeep.config import config
from pixkeep.handlers import routers
from pixkeep.housekeeping import housekeeping_loop
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



async def _check_schema() -> None:
    """Scheduled upload needs migrations/002_deferred_upload.sql. Without it
    the bot still runs (that mode is just unavailable) and tells the admins."""
    try:
        problems = await db.check_schema()
    except Exception:
        logging.exception("schema check failed")
        return
    if not problems:
        return
    logging.warning("schema incomplete, scheduled upload disabled: %s", problems)
    for admin in config.ADMIN_IDS:
        with contextlib.suppress(Exception):
            await bot.send_message(
                admin, "⚠️ В базе нет таблиц для загрузки по расписанию — этот режим выключен, "
                       "остальное работает. Выполни в Supabase SQL Editor файл "
                       "migrations/002_deferred_upload.sql и перезапусти бота.")


async def main():
    shutil.rmtree(config.TEMP_DIR, ignore_errors=True)   # leftovers of a previous run
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    dp = build_dispatcher()
    if config.TOKEN_KEY:
        try:
            n = await db.encrypt_existing_tokens()
            if n:
                logging.info(f"encrypted Google tokens of {n} account(s)")
        except Exception:
            logging.exception("token encryption migration failed (will retry on next start)")
    else:
        logging.warning("TOKEN_KEY is not set: Google tokens are stored unencrypted")
    await bot.set_my_commands(BOT_COMMANDS)
    await _check_schema()
    await run_web()
    keepalive_task = asyncio.create_task(db.keep_alive_loop())
    housekeeping_task = asyncio.create_task(housekeeping_loop())
    if db.deferred_available:
        try:
            await scheduler.resume_all()   # continue queues left by the previous run
        except Exception:
            logging.exception("couldn't resume scheduled uploads")
    try:
        await dp.start_polling(bot)
    finally:
        keepalive_task.cancel()
        housekeeping_task.cancel()
        await asyncio.gather(keepalive_task, housekeeping_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
