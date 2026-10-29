"""
Deferred (scheduled) upload.

While a batch waits, only its receipts (MediaRef: Telegram file_id, name,
size, message_id) sit in Supabase; the files stay on Telegram's servers.
Nothing is downloaded, nothing is held in memory, no CPU is used: per user
there is one asyncio task that sleeps until the next "tick" and exists only
while the queue is not empty.

A tick takes ONE whole batch (everything sent together), runs the usual
pipeline and upload, and schedules the next tick after a random pause in
[deferred_min, deferred_max] minutes. If that pause would cross today's
deadline, the next tick is put at the deadline and uploads everything that
is left. Ticks don't report one by one: a single status message is edited,
and one summary comes when the queue is empty.

Failed files go back to the head of the queue (attempts + 1) and are dropped
with a notice after MAX_ATTEMPTS. Revoked Google access pauses the queue.
"""
import asyncio
import contextlib
import logging
import os
import random
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from pixkeep import db, texts
from pixkeep.config import config
from pixkeep.delivery import delete_sources, reconnect_keyboard, upload_and_record
from pixkeep.drive import account_quota
from pixkeep.media import MediaRef, pick_folder_date
from pixkeep.oauth import TokenRevokedError
from pixkeep.pipeline import prepare_batch
from pixkeep.runtime import LOCAL_TZ, bot, get_chat_lock

MAX_ATTEMPTS = 3
INTERVAL_PRESETS = [(5, 10), (10, 15), (15, 30)]
DEADLINE_PRESETS = ["12:00", "14:00", "18:00"]


# ------------------------------------------------------------- planning -----
def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    try:
        h, m = (int(x) for x in (value or "").strip().replace(".", ":").split(":"))
    except ValueError:
        return None
    return (h, m) if 0 <= h < 24 and 0 <= m < 60 else None


def plan_next(now: datetime, lo: int, hi: int, deadline: str | None,
              rnd: random.Random | None = None) -> tuple[datetime, bool]:
    """(when, flush) for the next tick. `now` is timezone-aware. The pause is
    uniformly random in [lo, hi] minutes (seconds random too). If it would
    reach today's deadline (LOCAL_TZ) and the deadline is still ahead, the
    tick moves to the deadline itself and flush=True: upload everything left.
    A deadline already passed doesn't apply."""
    rnd = rnd or random
    lo, hi = sorted((max(1, lo), max(1, hi)))
    when = now + timedelta(seconds=rnd.uniform(lo * 60, hi * 60))
    hm = parse_hhmm(deadline)
    if hm:
        local = now.astimezone(LOCAL_TZ)
        dl = local.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if local < dl <= when.astimezone(LOCAL_TZ):
            return dl.astimezone(timezone.utc), True
    return when, False


def _deadline_ahead(deadline: str | None, now: datetime | None = None) -> bool:
    hm = parse_hhmm(deadline)
    if not hm:
        return False
    local = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    return local < local.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


# ------------------------------------------------------------- scheduler ----
_tasks: dict[int, asyncio.Task] = {}
_wake: dict[int, asyncio.Event] = {}


def _utc(iso: str | None) -> datetime | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_running(telegram_id: int) -> None:
    task = _tasks.get(telegram_id)
    if task is None or task.done():
        _wake[telegram_id] = asyncio.Event()
        _tasks[telegram_id] = asyncio.create_task(_loop(telegram_id))


async def resume_all() -> None:
    """At startup: continue the schedule of everyone with a non-empty queue."""
    for telegram_id in await db.queue_users():
        ensure_running(telegram_id)


async def enqueue(telegram_id: int, chat_id: int, refs: list[MediaRef], folder_name: str | None) -> None:
    await db.queue_add(telegram_id, chat_id, folder_name, [r.to_dict() for r in refs])
    state = await db.get_queue_state(telegram_id)
    if state is None or (not state.get("next_tick_at") and not state.get("paused")):
        await _schedule(telegram_id, chat_id)
    added = texts.queue_added(len(refs), folder_name)
    await refresh_status(telegram_id, chat_id, added=added, resend=True)
    ensure_running(telegram_id)


async def flush_now(telegram_id: int, chat_id: int) -> None:
    """«⏩ Загрузить всё сейчас» (also resumes a paused queue). Taken under the
    chat lock, so a tick in progress can't overwrite it with its own schedule."""
    async with get_chat_lock(chat_id):
        await db.set_queue_state(telegram_id, chat_id, next_tick_at=datetime.now(timezone.utc).isoformat(),
                                 flush=True, paused=False)
    ensure_running(telegram_id)
    _wake[telegram_id].set()


async def resume(telegram_id: int, chat_id: int) -> None:
    """«▶️ Продолжить очередь» after a pause: back to the normal schedule."""
    async with get_chat_lock(chat_id):
        await db.set_queue_state(telegram_id, chat_id, paused=False, next_tick_at=None, flush=False)
    ensure_running(telegram_id)
    _wake[telegram_id].set()


async def _schedule(telegram_id: int, chat_id: int) -> None:
    s = await db.get_settings(telegram_id)
    when, flush = plan_next(datetime.now(timezone.utc), s["deferred_min"], s["deferred_max"],
                            s["deferred_deadline"])
    await db.set_queue_state(telegram_id, chat_id, next_tick_at=when.isoformat(), flush=flush)


async def _loop(telegram_id: int) -> None:
    try:
        while True:
            state = await db.get_queue_state(telegram_id)
            entries = await db.queue_list(telegram_id)
            if not entries:
                if state:
                    await _finish(telegram_id, state)
                if await db.queue_list(telegram_id):
                    continue      # a batch arrived just as the queue emptied
                return
            chat_id = (state or {}).get("chat_id") or entries[0]["chat_id"]
            if state and state.get("paused"):
                return            # waits for «Продолжить» / «Загрузить всё сейчас»
            when = _utc((state or {}).get("next_tick_at"))
            if when is None:
                await _schedule(telegram_id, chat_id)
                await refresh_status(telegram_id, chat_id)
                continue
            delay = (when - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                event = _wake[telegram_id]
                event.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(event.wait(), timeout=delay)
                continue          # re-read the state: woken early or time is up
            async with get_chat_lock(chat_id):
                await _tick(telegram_id, chat_id, entries, bool(state and state.get("flush")))
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("deferred upload loop crashed")
        with contextlib.suppress(Exception):
            entries = await db.queue_list(telegram_id)
            if entries:
                await bot.send_message(entries[0]["chat_id"], texts.queue_crashed(), parse_mode="HTML",
                                       disable_notification=True)


async def _tick(telegram_id: int, chat_id: int, entries: list[dict], flush: bool) -> None:
    account = await db.get_active_account(telegram_id)
    if not account:
        await _pause(telegram_id, chat_id, texts.queue_no_account())
        return
    todo = entries if flush else entries[:1]
    need = sum(f.get("size") or 0 for e in todo for f in e["files"])
    q = await account_quota(account, fresh=True)
    if q and q[1] and q[0] + need > q[1]:
        await _pause(telegram_id, chat_id, texts.queue_no_space(account["email"], max(q[1] - q[0], 0), need))
        return
    for entry in todo:
        try:
            await _process_entry(telegram_id, chat_id, account, entry)
        except TokenRevokedError:
            await _pause(telegram_id, chat_id, texts.queue_revoked(), reconnect=True)
            return
        except Exception as e:
            logging.exception("scheduled batch failed")
            await _retry_or_drop(telegram_id, chat_id, entry, entry["files"], str(e))
    if await db.queue_list(telegram_id):
        await _schedule(telegram_id, chat_id)
        await refresh_status(telegram_id, chat_id)
    else:
        await db.set_queue_state(telegram_id, chat_id, next_tick_at=None, flush=False)


async def _process_entry(telegram_id: int, chat_id: int, account: dict, entry: dict) -> None:
    refs = [MediaRef.from_dict(f) for f in entry["files"]]
    settings = await db.get_settings(telegram_id)
    work_dir = os.path.join(config.TEMP_DIR, str(chat_id), uuid.uuid4().hex)
    try:
        batch = await prepare_batch(refs, settings, work_dir)
        folder = entry["folder_name"] or pick_folder_date(
            batch.exif_dates, [r.date for r in refs]).strftime("%d.%m.%y")
        out = None
        if batch.items:
            out = await upload_and_record(account, telegram_id, batch.items, folder, work_dir)
            await delete_sources(chat_id, out.results)
        ok_ids = {r["message_id"] for r in (out.succeeded if out else [])}
        left = [f for f in entry["files"] if f["message_id"] not in ok_ids]
        if left:
            reasons = "; ".join(f"{n}: {r}" for n, r in batch.skipped[:3]) or "ошибка загрузки"
            # the folder is fixed now, so the retry lands in the same one
            await _retry_or_drop(telegram_id, chat_id, {**entry, "folder_name": folder}, left, reasons,
                                 delete_entry=False)
        await db.queue_delete(entry["id"])
        if out:
            await _add_summary(telegram_id, chat_id, out, batch)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _retry_or_drop(telegram_id: int, chat_id: int, entry: dict, files: list[dict],
                         reason: str, delete_entry: bool = True) -> None:
    attempts = entry["attempts"] + 1
    if attempts < MAX_ATTEMPTS:
        if delete_entry and len(files) == len(entry["files"]):
            await db.queue_update(entry["id"], attempts=attempts)
            return
        await db.queue_add(telegram_id, chat_id, entry["folder_name"], files, attempts)
    else:
        await bot.send_message(chat_id, texts.queue_dropped([f["name"] for f in files], reason),
                               parse_mode="HTML", disable_notification=True)
    if delete_entry:
        await db.queue_delete(entry["id"])


async def _add_summary(telegram_id: int, chat_id: int, out, batch) -> None:
    state = await db.get_queue_state(telegram_id) or {}
    summary = list(state.get("summary") or [])
    summary.append({
        "folder_id": out.folder_id, "folder_name": out.folder_name, "link": out.link,
        "uploaded": len(out.uploaded), "duplicates": len(out.duplicates),
        "unclean": sum(1 for it in batch.items if it.get("clean_failed")),
    })
    await db.set_queue_state(telegram_id, chat_id, summary=summary)


async def _pause(telegram_id: int, chat_id: int, text: str, reconnect: bool = False) -> None:
    await db.set_queue_state(telegram_id, chat_id, paused=True, next_tick_at=None, flush=False)
    rows = [[InlineKeyboardButton(text="▶️ Продолжить очередь", callback_data="q:go")]]
    if reconnect:
        rows = reconnect_keyboard(telegram_id).inline_keyboard + rows
    await bot.send_message(chat_id, text, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await refresh_status(telegram_id, chat_id)


async def _finish(telegram_id: int, state: dict) -> None:
    chat_id = state["chat_id"]
    with contextlib.suppress(Exception):
        if state.get("status_message_id"):
            await bot.delete_message(chat_id, state["status_message_id"])
    if state.get("summary"):
        await bot.send_message(chat_id, texts.queue_summary(state["summary"]), parse_mode="HTML",
                               link_preview_options=LinkPreviewOptions(is_disabled=True))
    await db.clear_queue_state(telegram_id)


async def refresh_status(telegram_id: int, chat_id: int, added: str | None = None,
                         resend: bool = False) -> None:
    """The one status message of the queue: edited in place after ticks;
    re-sent (old one deleted) when a batch is added, so it stays at the bottom."""
    state = await db.get_queue_state(telegram_id) or {}
    entries = await db.queue_list(telegram_id)
    s = await db.get_settings(telegram_id)
    deadline = s["deferred_deadline"] if _deadline_ahead(s["deferred_deadline"]) else None
    text = texts.queue_status(entries, _utc(state.get("next_tick_at")), bool(state.get("flush")),
                              bool(state.get("paused")), deadline, added)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⏩ Загрузить всё сейчас", callback_data="q:now")]]) if entries else None
    msg_id = state.get("status_message_id")
    if msg_id and not resend:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id,
                                        parse_mode="HTML", reply_markup=kb)
            return
        except Exception as e:
            if "not modified" in str(e):
                return
    if msg_id:
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id, msg_id)
    msg = await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb,
                                 disable_notification=True)
    await db.set_queue_state(telegram_id, chat_id, status_message_id=msg.message_id)
