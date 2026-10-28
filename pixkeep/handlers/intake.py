"""
Incoming files: collect a burst into one batch, prepare it (pipeline), ask for
the folder name (or use the capture date), then hand over to delivery.
"""
import asyncio
import contextlib
import logging
import os
import shutil
import time
import uuid
from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatAction
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pixkeep import db, texts
from pixkeep.config import config
from pixkeep.delivery import deliver, retry_store
from pixkeep.drive import with_drive
from pixkeep.drive_utils import list_recent_folders
from pixkeep.media import MediaRef, pick_folder_date
from pixkeep.pipeline import prepare_batch
from pixkeep.runtime import LOCAL_TZ, Flow, bot, get_chat_lock, heartbeat

router = Router(name="intake")

# One per-chat buffer for an entire incoming burst. Telegram caps an album at
# 10 items, so 11+ photos arrive as two media groups; every arrival (album or
# loose file) goes into the same list and resets the same silence timer.
# Processing starts once nothing new has arrived for BATCH_SILENCE_SECONDS,
# so a 10+1 split lands in one batch with one folder-name question.
BATCH_SILENCE_SECONDS = 4.0
pending_batch: dict[int, list[Message]] = {}
pending_batch_tasks: dict[int, asyncio.Task] = {}

# FSM state (waiting_folder_name + its items/work_dir) is per chat, not per
# batch. If a batch is already waiting for its folder name, a new one is
# queued here instead of overwriting it, and gets its own question once the
# one ahead is answered or cancelled. Entries: {"items", "work_dir", "note"}.
folder_name_queue: dict[int, list[dict]] = {}


@router.message(F.photo | F.document | F.video | F.audio | F.voice | F.video_note | F.animation)
async def handle_media(message: Message, state: FSMContext):
    chat_id = message.chat.id
    if chat_id not in pending_batch and not await db.get_active_account(message.from_user.id):
        await message.answer("Сначала подключи Google Drive командой /start.")
        return
    pending_batch.setdefault(chat_id, []).append(message)
    # Reschedule the flush on every arrival — the whole burst becomes one batch.
    old_task = pending_batch_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()
    pending_batch_tasks[chat_id] = asyncio.create_task(_finish_batch(chat_id, state))


async def _finish_batch(chat_id: int, state: FSMContext):
    try:
        await asyncio.sleep(BATCH_SILENCE_SECONDS)
    except asyncio.CancelledError:
        return  # a newer file arrived and rescheduled us; that task will run
    messages = pending_batch.pop(chat_id, [])
    pending_batch_tasks.pop(chat_id, None)
    if messages:
        async with get_chat_lock(chat_id):
            await _process(messages, state, chat_id)


async def _process(messages: list[Message], state: FSMContext, chat_id: int):
    """Prepare the batch with the user's standing settings, then either upload
    straight into the capture-date folder or ask for a folder name."""
    telegram_id = messages[0].from_user.id
    settings = await db.get_settings(telegram_id)
    refs = [r for r in (MediaRef.from_message(m) for m in messages) if r]
    work_dir = os.path.join(config.TEMP_DIR, str(chat_id), uuid.uuid4().hex)

    # TYPING here (vs. UPLOAD_DOCUMENT during the Drive upload) just so the
    # indicator differs between the two stretches.
    async with heartbeat(chat_id, action=ChatAction.TYPING):
        batch = await prepare_batch(refs, settings, work_dir)

    if not batch.items:
        await bot.send_message(chat_id, texts.nothing_received(batch.skipped), parse_mode="HTML")
        shutil.rmtree(work_dir, ignore_errors=True)
        return

    note = texts.intake_note(batch, settings)
    if settings["auto_date_folder"]:
        folder_name = pick_folder_date(batch.exif_dates, [r.date for r in refs]).strftime("%d.%m.%y")
        await bot.send_message(chat_id, note + texts.auto_folder_line(folder_name, bool(batch.exif_dates)), parse_mode="HTML")
        await deliver(chat_id, telegram_id, batch.items, work_dir, folder_name)
        return

    if await state.get_state() == Flow.waiting_folder_name.state:
        folder_name_queue.setdefault(chat_id, []).append(
            {"items": batch.items, "work_dir": work_dir, "note": note})
        await bot.send_message(chat_id, note + texts.queued_line(), parse_mode="HTML")
        return

    await _ask_folder_name(chat_id, state, batch.items, work_dir, note)


_folders_cache: dict[int, tuple[float, list[str]]] = {}


async def _recent_folders(account: dict) -> list[str]:
    hit = _folders_cache.get(account["id"])
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    try:
        names = await asyncio.wait_for(
            with_drive(account, lambda s, t: list_recent_folders(s, t, 6)), 6)
    except Exception as e:
        logging.warning(f"recent folders fetch failed: {e}")
        return []
    _folders_cache[account["id"]] = (time.monotonic(), names)
    return names


async def _ask_folder_name(chat_id: int, state: FSMContext, items: list[dict],
                           work_dir: str, note: str) -> None:
    """Puts one batch into Flow.waiting_folder_name and sends its prompt with
    quick-pick buttons: today's date + the folders this bot created most
    recently on the active account (private chat: chat_id == user id)."""
    names = [datetime.now(LOCAL_TZ).strftime("%d.%m.%y")]
    try:
        account = await db.get_active_account(chat_id)
        if account:
            names += [n for n in await _recent_folders(account) if n not in names]
    except Exception:
        logging.exception("folder suggestions failed")
    names = names[:6]

    await state.update_data(items=items, work_dir=work_dir, folder_suggestions=names)
    await state.set_state(Flow.waiting_folder_name)

    kb_rows = [[InlineKeyboardButton(text=f"📁 {n}"[:60], callback_data=f"fld:{i}")]
               for i, n in enumerate(names)]
    kb_rows.append([InlineKeyboardButton(text="❌ Отменить загрузку", callback_data="cancel")])
    await bot.send_message(chat_id, note + texts.ask_folder_line(), parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))


async def _ask_next_queued(chat_id: int, state: FSMContext) -> None:
    """Once a batch's folder question is answered or cancelled, ask the next
    queued batch's question, if any."""
    queue = folder_name_queue.get(chat_id)
    if not queue:
        return
    nxt = queue.pop(0)
    if not queue:
        folder_name_queue.pop(chat_id, None)
    await _ask_folder_name(chat_id, state, nxt["items"], nxt["work_dir"], nxt["note"])


@router.callback_query(F.data == "cancel")
async def on_cancel(cq: CallbackQuery, state: FSMContext):
    """Wipes downloaded/cleaned temp files and clears FSM state without
    uploading anything."""
    chat_id = cq.message.chat.id
    data = await state.get_data()
    work_dir = data.get("work_dir")
    if work_dir and os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
    await state.clear()
    await cq.message.edit_text("❌ Отменено. Ничего не загружено, временные файлы удалены.")
    await cq.answer("Отменено")
    async with get_chat_lock(chat_id):
        await _ask_next_queued(chat_id, state)


@router.callback_query(F.data.regexp(r"^retry:\w+$"))
async def on_retry(cq: CallbackQuery):
    token = cq.data.split(":", 1)[1]
    entry = retry_store.get(token)
    if not entry or entry["telegram_id"] != cq.from_user.id or not os.path.exists(entry["work_dir"] or ""):
        retry_store.pop(token, None)
        await cq.answer("Время вышло или бот перезапускался — отправь эти файлы заново.", show_alert=True)
        return
    retry_store.pop(token)
    entry["task"].cancel()
    await cq.answer("Повторяю…")
    with contextlib.suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)
    chat_id = cq.message.chat.id
    async with get_chat_lock(chat_id):
        await deliver(chat_id, cq.from_user.id, entry["items"], entry["work_dir"], entry["folder_name"])


async def _begin_upload(chat_id: int, telegram_id: int, state: FSMContext,
                        data: dict, folder_name: str) -> None:
    items = data.get("items", [])
    work_dir = data.get("work_dir")
    if not items:
        await bot.send_message(chat_id, "Нет файлов для загрузки, начни заново.")
        await state.clear()
        return
    await state.clear()
    # Same per-chat lock as _process: a queued batch can't be asked out of
    # turn, and this upload's ensure_folder() can't race a new batch's.
    async with get_chat_lock(chat_id):
        await deliver(chat_id, telegram_id, items, work_dir, folder_name)
        await _ask_next_queued(chat_id, state)


@router.message(Flow.waiting_folder_name, F.text)
async def on_folder_name(message: Message, state: FSMContext):
    data = await state.get_data()
    await _begin_upload(message.chat.id, message.from_user.id, state, data, message.text.strip())


@router.callback_query(F.data.regexp(r"^fld:\d+$"))
async def on_folder_pick(cq: CallbackQuery, state: FSMContext):
    if await state.get_state() != Flow.waiting_folder_name.state:
        await cq.answer("Сначала заверши ввод комментария/названия или отмени его.", show_alert=True)
        return
    data = await state.get_data()
    names = data.get("folder_suggestions") or []
    idx = int(cq.data.split(":")[1])
    if idx >= len(names):
        await cq.answer("Кнопка устарела — введи имя папки текстом.", show_alert=True)
        return
    await cq.answer()
    with contextlib.suppress(Exception):
        await cq.message.edit_reply_markup(reply_markup=None)
    await _begin_upload(cq.message.chat.id, cq.from_user.id, state, data, names[idx])
