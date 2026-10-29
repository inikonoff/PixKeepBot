"""
Process-wide objects shared by handlers and services: the Bot instance, the
local timezone, per-chat locks, the chat-action "heartbeat" and FSM states.
"""
import asyncio
import contextlib
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.fsm.state import State, StatesGroup

from pixkeep.config import config

bot = Bot(token=config.BOT_TOKEN)

# Used as the display/fallback timezone (EXIF timestamps have no timezone,
# Telegram's message.date is UTC) — "today" the way a person in Belarus sees it.
LOCAL_TZ = ZoneInfo("Europe/Minsk")


class Flow(StatesGroup):
    waiting_folder_name = State()
    waiting_comment = State()
    waiting_rename = State()
    waiting_deadline = State()
    waiting_parent_name = State()


# Serializes batch processing per chat. Without it two batches for the same
# chat could run concurrently and both call ensure_folder() for the same
# auto-date folder name: both search Drive, both find nothing, both create a
# folder — two "31.08.26" folders with part of the files in each. Holding the
# lock through processing, upload and cleanup means a later batch always
# finds and reuses the folder an earlier one created.
chat_locks: dict[int, asyncio.Lock] = {}


def get_chat_lock(chat_id: int) -> asyncio.Lock:
    lock = chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        chat_locks[chat_id] = lock
    return lock


# Telegram's native chat-action indicator ("отправляет файл…" under the chat
# title) instead of filler messages during long silent stretches. One
# sendChatAction lasts ~5s, so it is re-sent periodically.
CHAT_ACTION_INTERVAL_SECONDS = 4.0


async def _chat_action_loop(chat_id: int, action: str) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, action)
            await asyncio.sleep(CHAT_ACTION_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        pass


@contextlib.asynccontextmanager
async def heartbeat(chat_id: int, action: str = ChatAction.UPLOAD_DOCUMENT):
    """Keeps the chat-action indicator alive while the wrapped code runs;
    always stopped on the way out, success or failure alike."""
    task = asyncio.create_task(_chat_action_loop(chat_id, action))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
