"""
main.py
v5.21 - PixKeep Bot (multi-user, multi-account, Google Drive backend, admin-only)

Changelog:
- v5.21: приём ЛЮБЫХ файлов — теперь принимаются и видео, аудио, голосовые,
        кружки и GIF (раньше только фото и документы; остальное молча
        игнорировалось). Исправлено: (1) один не скачавшийся файл (напр.
        больше 20 МБ — лимит Bot API) ронял всю пачку без единого сообщения;
        теперь такие файлы перечисляются в отчёте, остальные грузятся, а
        исходные сообщения непринятых файлов остаются в чате; (2) файлы с
        одинаковым именем в одной пачке перезаписывали друг друга на диске
        (и оба исходных сообщения удалялись) — теперь получают суффикс _2,
        _3; (3) если exiftool не смог почистить файл, бот молча заливал его
        как есть — теперь предупреждает в сообщении и помечает в txt-списке.
- v5.20: комментарии к папке: кнопка «💬 Добавить комментарий» под отчётом
        о загрузке и в карточке загрузки («Мои загрузки»). Текст сохраняется
        в !COMMENT.txt (имя начинается с «!», чтобы файл был первым в списке)
        — один файл на папку, комментарии дописываются с меткой времени.
        Комментарий можно удалить из «Мои загрузки». REQUIRES две новые
        колонки в uploads — SQL в README и в db.py.
- v5.19: photo_fix переписан: решение «тёмное ли фото» и сила подъёма теперь
        считаются по теням (20-й процентиль яркости), а не по медиане, и
        используется кривая подъёма теней вместо общей гаммы. Раньше кадр с
        тёмным передним планом под светлым небом (медиана ~89) в «авто»
        пропускался, а в «всегда» подъём был почти незаметен.
- v5.18: при включённой чистке метаданных фото с тегом EXIF Orientation
        физически поворачиваются до чистки (exiftool -all= удаляет этот тег,
        и вертикальные кадры иначе ложились бы на бок). JPEG пересохраняется
        с quality 95; в txt-списке такие файлы помечены "rotated upright".
- v5.17: (1) автокоррекция тёмных фото (photo_fix.py; /settings: выкл / авто /
        всегда) — только JPEG/PNG, перед чисткой метаданных; (2) настройка
        «txt-список файлов»: в папку кладётся upload_list_<дата>.txt (на
        английском, UTF-8 с BOM) с пометкой edited/original по каждому файлу;
        (3) бот везде называется PixKeep. REQUIRES две новые колонки в
        user_settings — SQL в README и в db.py.
- v5.16: «Мои загрузки» — удаление отдельных файлов из загрузки (список с
        постраничной навигацией; последний удалённый файл убирает и пустую
        папку, как полное удаление).
- v5.15: "📂 Мои загрузки" (кнопка под полем ввода + /uploads): последние 10
        загрузок, по каждой — открыть/скопировать ссылку, закрыть/открыть
        доступ по ссылке, удалить (файлы и пустая папка уходят в корзину
        Drive). REQUIRES table `uploads` in Supabase — SQL in README.
- v5.14: Supabase keep-alive — background task (db.keep_alive_loop) pings the
        database every 10 min so the free project is not paused for inactivity.
- v5.13: removed the TYPING chat-action v5.12 wrapped around message
        deletion. There's no "done" chat-action to end on (Bot API's
        action vocabulary is entirely in-progress states — typing,
        uploading, recording, etc. — nothing means "finished"), so all it
        actually did was flash "печатает..." right after the upload report
        and then silently stop, which read as unfinished rather than
        reassuring. Deletion runs plainly now; the upload report is simply
        the last thing in the chat, nothing flickers after it.
- v5.12: the upload flow used to always end with a routine "🗑 Удалил N
        исходных сообщений из чата" text message AFTER the "✅ Загружено...
        ссылка" report — so the link/report was never actually the last
        thing visible in the chat. Deleting the original messages now just
        runs under the same TYPING chat-action indicator as the rest of the
        pipeline (v5.11) instead of narrating itself with its own message.
        A text message only appears afterward if something genuinely needs
        attention — a handful of messages Telegram wouldn't let get deleted
        (e.g. too old) — since that's information the person has to act on,
        not a routine confirmation. When everything deletes cleanly (the
        normal case), the upload report stays the last word in the chat.
- v5.11: moved the "still working" indicator (v5.9) out of the chat log and
        into Telegram's native chat-action line — the "бот отправляет
        файл..." text that shows right under the chat title, the same spot
        Telegram's own service notices ("Connecting...") show up. v5.9 sent
        random filler text messages into the conversation itself to prove
        the bot hadn't stalled; those are gone now in favor of
        bot.send_chat_action, re-sent every 4s (a chat action only holds
        for ~5s before Telegram clears it) for as long as the wrapped work
        runs, then left to disappear on its own — nothing to clean up.
        Same two spots as before (download+exif+clean in _process, the
        Drive upload in _do_upload), just a different action type per spot
        since Bot API's fixed action vocabulary has no generic "processing"
        option: TYPING for the first, UPLOAD_DOCUMENT (the default) for the
        second.
- v5.10: added a "♻️ Заменять файлы с таким же именем" setting
        (replace_duplicates, /settings — off by default, so nobody's
        existing behavior changes unless they opt in). Google Drive treats
        filenames as plain metadata, not a unique path — uploading a file
        called IMG_1234.jpg into a folder that already has one just creates
        a second object with the same name, no error, no warning. With this
        on, upload now checks the target folder for a file with that exact
        name first (find_file_in_folder) and overwrites its content in
        place (replace_file_content) instead of creating a duplicate —
        same file id, same share link. Matching is by name only within the
        target folder, not by content hash, so this is specifically for
        "I re-sent the same file/named it the same" rather than true
        content-dedup.
        REQUIRES a Supabase migration before deploying — see db.py's
        header (ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS
        replace_duplicates boolean default false;) — get_settings() builds
        its SELECT from DEFAULT_SETTINGS.keys(), so without that column
        every call (including the one behind every single upload) errors
        out until the migration runs.
- v5.9: added a "heartbeat" — a background ping every 6s with a random
        working-on-it phrase (see HEARTBEAT_MESSAGES) — wrapped around the
        two stretches of the pipeline that can run long with literally
        nothing printed to the chat: download-from-Telegram + exiftool
        inspect/clean in _process, and the actual Drive upload in
        _do_upload. Purely cosmetic/reassurance — the messages don't need
        to (and don't) describe the exact operation running at that instant,
        they just prove the bot hasn't silently died on a big or slow
        batch. Implemented as an async context manager (_heartbeat) so it
        starts/stops correctly via try/finally regardless of whether the
        wrapped code finishes normally, raises, or (in principle) gets
        cancelled — no separate bookkeeping needed at each call site.
- v5.8: added a "📋 Скопировать ссылку" button under the final upload report,
        using Telegram's copy_text inline-button type (Bot API 7.x+,
        aiogram's CopyTextButton) — tapping it puts the Drive link straight
        on the clipboard. The link in the message text is still a normal
        tappable URL that opens the folder; this is purely additive, since
        Telegram gives a plain URL exactly one tap behavior (open) and
        there's no way to make that same tap also copy.
- v5.7: replaced the two separate, uncoordinated batch-collection mechanisms
        (one waiting a flat 1s for an album's remaining media_group_id
        messages, one debouncing individually-sent files with no
        media_group_id) with a single per-chat buffer + one silence timer
        (BATCH_SILENCE_SECONDS, still 4.0s). Every incoming photo/document
        for a chat — album or not, whichever album — now goes into the same
        list and resets the same timer; processing starts once nothing new
        has arrived for BATCH_SILENCE_SECONDS. This is the real fix for the
        "Получил 2 файл(ов)" / "Получил 9 файл(ов)" double-prompt: Telegram
        caps albums at 10 items, so sending 11 photos gets split client-side
        into a 10-item and a 1-item media group with two different
        media_group_id values, arriving moments apart — the old code had no
        way to know they were part of the same user action and processed
        them as two unrelated batches. Now, as long as the split pieces
        arrive within the same silence window (which they normally do,
        being one client-side send), they're collected and processed
        together as a single batch with a single folder-name question.
        folder_name_queue (v5.6) and chat_locks (v5.5) are both kept as
        fallbacks — they still matter if a burst is large/slow enough to
        genuinely produce two separate silence windows.
- v5.6: fixed a second, separate bug behind the same symptom as v5.5's
        "10 + 1" case: Telegram itself splits any send of 11+ photos into
        separate media groups (a hard 10-item album cap, nothing this bot
        can do about that) — so with auto_date_folder OFF, both batches'
        _process() calls reached the manual "укажите имя папки" prompt in
        quick succession. That prompt stores its batch's items/work_dir in
        FSM state and waits for a text reply — but FSM state is per chat,
        not per batch, so the second batch's state.update_data() silently
        overwrote the first batch's before its question could be answered:
        whichever name got typed only applied to the last batch, and the
        earlier one's downloaded files were orphaned (never uploaded, temp
        dir never cleaned up). Two "Укажите имя папки" prompts appearing
        back-to-back, before there was time to answer either, was the
        visible symptom. Fixed with a small per-chat queue
        (folder_name_queue): if a batch reaches the manual-name prompt while
        another one is already waiting on FSM state, it's queued instead of
        overwriting that state, and gets its own prompt once the batch ahead
        of it is answered (on_folder_name) or cancelled (on_cancel) — both
        now run under the same chat_locks lock from v5.5, so a newly
        arriving batch's _process() can't check-and-overwrite FSM state in
        the middle of that handoff either.
- v5.5: fixed a batch-splitting/duplicate-folder bug: a burst of ~10+ files
        sent close together (esp. over a slow/unstable connection) could get
        chopped into several smaller batches — either because the singles
        debounce (v3.2) timed out between arrivals, or because a real
        media-group and a singles-burst landed in the same chat at once.
        Each batch ran _process()/_do_upload() as an independent asyncio
        task with no coordination between them, so two batches could both
        call ensure_folder() for the SAME auto-date folder name concurrently
        — both search, both find nothing yet (the other hasn't created it
        yet), both create a folder, and you end up with two distinct Drive
        folders sharing the same name and only some of the files in each.
        Fixed by serializing all batch processing per chat behind a lock
        (_get_chat_lock): a new batch (whether from _finish_singles or
        _finish_group) now waits for any batch already in flight for that
        chat to fully finish (download → clean → upload → report → delete)
        before starting. This doesn't stop a burst from still being split
        into multiple batches (see SINGLES_DEBOUNCE_SECONDS below), but it
        guarantees those batches run strictly one after another, so the
        second one's ensure_folder() call will always find the folder the
        first one already created instead of racing to make a duplicate.
        Also raised SINGLES_DEBOUNCE_SECONDS 2.0 -> 4.0 to reduce how often
        a slow upload from the phone (weak signal, large files) creates a
        >2s gap between messages and triggers a premature split in the
        first place.
- v5.4: added /version — replies with the running code's version string.
        Added purely as a deployment diagnostic: several rounds of "the
        feature isn't there" turned out to be Render still running an old
        deploy, indistinguishable from an actual code bug without a way to
        ask the running instance what it actually is. Check /version after
        every deploy going forward.
- v5.3: added auto_date_folder setting (/settings, third checkbox). When on,
        the bot skips the "укажите имя папки" text prompt entirely: it reads
        each file's EXIF capture date (falling back to the Telegram message's
        send time, then to today, if no EXIF date is present), picks one date
        for the whole batch (most common; ties go to the earliest), announces
        "Папка будет создана автоматически: DD.MM.YY", and uploads right away
        — no confirmation click, matching the "remove friction" goal from
        /settings itself. GPS/date extraction reuses the same exiftool pass
        already done for the GPS warning, no extra subprocess calls added.
        Upload+report+cleanup logic extracted into _do_upload() shared by
        both this path and the manual on_folder_name handler.
- v5.2: fixed the long freeze after sending files. Root cause: exiftool
        calls (inspect_metadata / strip_exif) are blocking OS subprocess
        calls; running them directly in an async handler froze the ENTIRE
        bot (no other updates processed) for the full duration. Now offloaded
        via asyncio.to_thread, and downloads / exif calls / Drive uploads all
        run concurrently (bounded by semaphores — DOWNLOAD_CONCURRENCY,
        EXIF_CONCURRENCY, UPLOAD_CONCURRENCY) instead of strictly one-by-one.
        Also added a Telegram command menu (the blue "menu" button) via
        bot.set_my_commands() at startup, listing /start /accounts /settings
        /logout.
- v5.1: added a global IsAdmin filter (dp.message.filter / dp.callback_query.filter)
        so ONLY config.ADMIN_ID can interact with the bot at all — anyone else's
        messages are dropped before reaching any handler, silently. This closes
        the "any stranger who finds the bot can connect their own Google
        account and use our infra/quota" exposure raised earlier.
- v5.0: multiple Google accounts per Telegram user. /start now offers
        "add another account" once one is connected; /accounts lists all
        connected accounts and lets you tap one to make it active (that's
        the one uploads go to). Any Google account can connect — not just
        the one that created the OAuth client — since the app is Published
        (In Production) with a non-sensitive scope, so there's no test-user
        allow-list. Settings (clean/anonymize) are now independent of which
        account is active — one set of preferences per Telegram user.
- v4.1: moved "чистить метаданные?" / "обезличить имена?" out of the
        per-upload flow and into standing settings, toggled anytime via
        /settings (checkboxes, persisted in Supabase). The upload flow is
        now just: files -> folder name -> link. Settings are applied
        automatically and silently (the note before the folder-name prompt
        just states what will happen, doesn't ask).
- v4.0: storage backend Yandex.Disk -> Google Drive. Reason: Yandex forces
        login for public FOLDER links (only individual files are login-free
        by design), which broke the "anyone with the link, no auth" goal.
        Google Drive's "anyone with the link" folder sharing has no such
        restriction. Uses drive.file scope (non-sensitive) via plain REST
        (aiohttp), no Google client libraries needed. App is published
        "In Production" so there's no 100-test-user cap and (to be
        confirmed over the coming week) no 7-day refresh-token expiry.
- v3.5: reverted /space (v3.4) — confirmed live that GET /v1/disk returns
        403 under cloud_api:disk.app_folder scope. Showing quota would
        require broadening to a whole-disk read scope, which isn't worth
        trading away the "bot only sees its own folder" guarantee. Back to
        app_folder-only, no disk-quota command.
- v3.3: added a "❌ Отменить загрузку" button at every step (clean choice,
        rename choice, folder-name prompt). Cancelling wipes any downloaded/
        cleaned temp files and clears FSM state — nothing gets uploaded.
- v3.2: fixed batching for files sent as separate messages (no shared
        media_group_id) — some Telegram clients send multi-file "as
        document" uploads this way instead of a real album. Now buffered
        per-chat with a debounce: each new file resets a short timer, and
        the whole burst is processed together once uploads pause.
- v3.1: per-file tracking (message_id -> path -> upload name -> status).
        Upload continues past individual failures instead of aborting the
        whole batch. Final report lists successes/failures by exact
        filename. Successfully uploaded messages are deleted from the chat
        (Telegram lets bots delete their own incoming private messages);
        deletion failures are reported honestly, not hidden.
- v3.0: storage backend Google Drive -> Yandex.Disk (accessible from BY/CIS).
        Per-user Yandex OAuth tokens (access+refresh) in Supabase, auto-refresh.
- v2.1: optional "обезличить имена" -> rename to 001, 002, 003…
- v2.0: multi-user OAuth, optional cleaning, shareable link.

Flow: files -> (settings applied silently: clean? anonymize names?) ->
folder name -> upload to the active Google account's Drive (per-file,
continue on error) -> report + public link -> delete succeeded messages.

Runs an aiohttp server (OAuth callback + Render port) alongside aiogram polling.
"""
VERSION = "5.21"  # bump on every change; check via /version to confirm what's actually deployed

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatAction
from aiogram.filters import BaseFilter, Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from config import config
from db import (
    keep_alive_loop,
    get_account_by_id,
    save_upload,
    list_uploads,
    get_upload,
    update_upload,
    delete_upload,
    delete_uploads_by_folder,
    list_uploads_by_folder,
    set_folder_comment_file,
    get_settings,
    set_setting,
    add_or_update_account,
    list_accounts,
    get_active_account,
    set_active_account,
    update_account_tokens,
    remove_all_accounts,
)
from exif_utils import inspect_metadata, strip_exif
from photo_fix import fix_photo, normalize_orientation
from drive_utils import (
    ensure_folder,
    upload_file,
    find_file_in_folder,
    replace_file_content,
    publish_and_get_url,
    GoogleAuthError,
    trash_file,
    folder_is_empty,
    revoke_public_access,
)
from oauth import build_auth_url, exchange_code, refresh_access_token, get_user_email

logging.basicConfig(level=logging.INFO)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class IsAdmin(BaseFilter):
    """Global gate: only config.ADMIN_ID may use the bot. Everyone else's
    messages/callbacks are simply not handled — no reply, no acknowledgment
    that the bot exists or does anything. Applied once here to the whole
    dispatcher, so every handler below is covered automatically."""
    async def __call__(self, event) -> bool:
        user = event.from_user
        return user is not None and user.id == config.ADMIN_ID


dp.message.filter(IsAdmin())
dp.callback_query.filter(IsAdmin())

# v5.7: single per-chat buffer for an entire incoming burst — replaces two
# separate, uncoordinated mechanisms that used to exist here (one that
# waited a flat 1s for an album's remaining media_group_id messages, one
# that debounced individually-sent files with no media_group_id). Telegram
# caps any single album at 10 items, so sending 11+ photos gets split
# client-side into two media groups with two different media_group_id
# values — the old code treated those as two unrelated batches with no
# knowledge of each other, which is what produced the "Получил 2 файл(ов)"
# / "Получил 9 файл(ов)" double-prompt. Now it doesn't matter whether an
# arriving file belongs to an album, a different album, or no album at
# all: every arrival goes into the same list for its chat and resets the
# same silence timer. Processing starts once nothing new has arrived for
# BATCH_SILENCE_SECONDS, so a 10+1 split (or any burst) that lands within
# that window is naturally collected and processed as one batch with one
# folder-name question, instead of relying on the queue (folder_name_queue,
# still kept below as a fallback for genuinely separate sends) to sort out
# two competing prompts after the fact.
BATCH_SILENCE_SECONDS = 4.0
pending_batch: dict[int, list[Message]] = {}
pending_batch_tasks: dict[int, asyncio.Task] = {}

# v5.5: serializes batch processing per chat. Without this, two batches for
# the same chat (e.g. a burst split by the silence timer into two pieces,
# which can still happen if the pauses are long enough) could run
# _process()/_do_upload() concurrently. Both would then call ensure_folder()
# for the same auto-date folder name at nearly the same time — both search
# Drive, both find nothing (neither has created it yet), both create a
# folder — leaving two duplicate "31.08.26"-style folders with only some of
# the files in each, instead of one folder with all of them. Holding this
# lock for the full duration of _process() (through upload, report, and
# cleanup) means a later batch's ensure_folder() call always runs after the
# earlier batch's folder-create has already committed, so it finds and
# reuses that folder instead of racing to make a new one.
chat_locks: dict[int, asyncio.Lock] = {}


def _get_chat_lock(chat_id: int) -> asyncio.Lock:
    lock = chat_locks.get(chat_id)
    if lock is None:
        lock = asyncio.Lock()
        chat_locks[chat_id] = lock
    return lock


# v5.11: v5.9's heartbeat sent random filler text messages into the chat
# itself to prove the bot was still alive during the two long-silent
# stretches (download+exif+clean in _process, the actual Drive upload in
# _do_upload). Replaced with Telegram's native chat-action indicator — the
# same "печатает..." / "отправляет файл..." line that appears right under
# the chat title, which is also where Telegram's own service notices (e.g.
# "Connecting...") show up. It's more at home there than as extra messages
# cluttering the conversation, and needs no cleanup — nothing to delete
# once the real work is done, it just stops updating and Telegram clears
# it on its own a few seconds later.
#
# A sendChatAction call only keeps the indicator showing for ~5s, so it
# has to be re-sent periodically for the duration of a long operation —
# same repeating-background-task shape as the old heartbeat, just calling
# send_chat_action instead of send_message. Bot API only offers a fixed
# set of action types (no custom text), so exact wording isn't ours to
# pick — "upload_document" for the download/inspect/clean stretch (closest
# available match to "handling files") and "upload_document" again for the
# actual Drive upload right after.
CHAT_ACTION_INTERVAL_SECONDS = 4.0


async def _chat_action_loop(chat_id: int, action: str) -> None:
    try:
        while True:
            await bot.send_chat_action(chat_id, action)
            await asyncio.sleep(CHAT_ACTION_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        pass


@contextlib.asynccontextmanager
async def _heartbeat(chat_id: int, action: str = ChatAction.UPLOAD_DOCUMENT):
    """Wrap around any await that might run long and silent. Keeps
    Telegram's own "бот отправляет файл..." indicator alive under the chat
    title for as long as the wrapped code runs; always stopped on the way
    out, success or failure alike."""
    task = asyncio.create_task(_chat_action_loop(chat_id, action))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task



class Flow(StatesGroup):
    waiting_folder_name = State()
    waiting_comment = State()


# ---------------------------------------------------------------- commands ---
def _accounts_keyboard(accounts: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for a in accounts:
        mark = "✅ " if a["is_active"] else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{a['email']}", callback_data=f"acct:{a['id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    accounts = list_accounts(uid)
    auth_url = build_auth_url(uid)

    if accounts:
        active = next((a for a in accounts if a["is_active"]), None)
        active_email = active["email"] if active else "не выбран — см. /accounts"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Подключить ещё аккаунт", url=auth_url)]
        ])
        await message.answer(
            f"Подключено аккаунтов: {len(accounts)}. Активный: {active_email}\n\n"
            "Пришли фото или файлы (можно альбомом) — загружу на активный "
            "аккаунт (спрошу только имя папки) и после успешной загрузки "
            "удалю исходные сообщения из чата.\n\n"
            "Чистка метаданных и обезличивание имён — настраиваются заранее в "
            "/settings.\n\n"
            "⚠️ Присылай как файл (📎 → Файл), а не как обычное фото — иначе "
            "Telegram сам пережмёт изображение.\n\n"
            "/uploads — последние загрузки (ссылки, удаление)\n"
            "/accounts — посмотреть все аккаунты и переключиться\n"
            "/settings — настройки очистки/переименования\n"
            "/logout — отключить все аккаунты.",
            reply_markup=kb,
        )
        await message.answer("Меню внизу 👇", reply_markup=UPLOADS_KEYBOARD)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить Google Drive", url=auth_url)]
    ])
    await message.answer(
        "Привет! Я PixKeep — чищу метаданные (EXIF/GPS/IPTC/XMP) из фото и файлов "
        "без потери качества и складываю результат на твой Google Drive.\n\n"
        "Сначала подключи свой Диск — откроется страница Google, войди и "
        "разреши доступ. Пароль вводится только у Google, я его не вижу. "
        "Доступ ограничен: я вижу только файлы и папки, которые сам создам "
        "(scope drive.file) — остальной твой Диск мне не виден.\n\n"
        "Можно подключить сразу несколько своих Google-аккаунтов и "
        "переключаться между ними через /accounts.",
        reply_markup=kb,
    )


@dp.message(Command("accounts"))
async def accounts_cmd(message: Message):
    accounts = list_accounts(message.from_user.id)
    if not accounts:
        await message.answer("Нет подключённых аккаунтов. /start чтобы подключить.")
        return
    await message.answer(
        "Твои Google-аккаунты — нажми, чтобы сделать активным для загрузки:",
        reply_markup=_accounts_keyboard(accounts),
    )


@dp.callback_query(F.data.startswith("acct:"))
async def on_switch_account(cq: CallbackQuery):
    account_id = int(cq.data.split(":", 1)[1])
    set_active_account(cq.from_user.id, account_id)
    accounts = list_accounts(cq.from_user.id)
    await cq.message.edit_reply_markup(reply_markup=_accounts_keyboard(accounts))
    await cq.answer("Активный аккаунт изменён")


# ------------------------------------------------------------ my uploads ----
UPLOADS_BUTTON = "📂 Мои загрузки"
UPLOADS_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=UPLOADS_BUTTON)]],
    resize_keyboard=True, is_persistent=True,
)


def _real(files: list[dict]) -> list[dict]:
    """History entries without the service txt list."""
    return [f for f in files if not f.get("report")]


def _fmt_dt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(LOCAL_TZ).strftime("%d.%m %H:%M")
    except Exception:
        return "?"


async def _with_drive(account: dict, fn):
    """Runs `await fn(session, token)`; on a 401 refreshes the token once
    (and stores it) and retries."""
    async with aiohttp.ClientSession() as session:
        try:
            return await fn(session, account["access_token"])
        except GoogleAuthError:
            new = await refresh_access_token(account["refresh_token"])
            update_account_tokens(account["id"], new["access_token"], new["refresh_token"])
            account["access_token"] = new["access_token"]
            return await fn(session, new["access_token"])


def _uploads_list_view(uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    rows = list_uploads(uid, 10)
    if not rows:
        return "Загрузок пока нет.", None
    kb = [[InlineKeyboardButton(
        text=f"{_fmt_dt(r['created_at'])} · {r['folder_name']} · {len(_real(r['files']))} ф.",
        callback_data=f"up:{r['id']}",
    )] for r in rows]
    return "📂 Последние загрузки — выбери:", InlineKeyboardMarkup(inline_keyboard=kb)


def _upload_detail_view(u: dict) -> tuple[str, InlineKeyboardMarkup]:
    names = [f["name"] for f in _real(u["files"])]
    shown = ", ".join(names[:15]) + (f" … и ещё {len(names) - 15}" if len(names) > 15 else "")
    access = ("🔓 доступ по ссылке открыт" if u.get("shared", True)
              else "🔒 доступ по ссылке закрыт")
    text = (
        f"📁 {u['folder_name']}\n{_fmt_dt(u['created_at'])} · файлов: {len(names)}\n"
        f"{access}\n\nФайлы: {shown}\n\n{u['link']}"
    )
    comment = u.get("comment")
    if comment:
        text += "\n\n💬 Комментарий:\n" + (comment[:400] + ("…" if len(comment) > 400 else ""))
    uid = u["id"]
    kb = []
    if u.get("shared", True):
        kb.append([InlineKeyboardButton(text="🔗 Открыть папку", url=u["link"])])
        kb.append([InlineKeyboardButton(text="📋 Скопировать ссылку",
                                        copy_text=CopyTextButton(text=u["link"]))])
        kb.append([InlineKeyboardButton(text="🔒 Закрыть доступ по ссылке",
                                        callback_data=f"upun:{uid}")])
    else:
        kb.append([InlineKeyboardButton(text="🔓 Открыть доступ по ссылке",
                                        callback_data=f"upsh:{uid}")])
    kb.append([InlineKeyboardButton(text="💬 Добавить комментарий", callback_data=f"cmt:{uid}")])
    if comment:
        kb.append([InlineKeyboardButton(text="🧹 Удалить комментарий", callback_data=f"cmtdel:{uid}")])
    kb.append([InlineKeyboardButton(text="🗑 Удалить отдельные файлы", callback_data=f"upf:{uid}:0")])
    kb.append([InlineKeyboardButton(text="🗑 Удалить всю загрузку", callback_data=f"updel:{uid}")])
    kb.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="uplist")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _send_uploads(message: Message):
    text, kb = _uploads_list_view(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@dp.message(Command("uploads"))
async def uploads_cmd(message: Message):
    await _send_uploads(message)


@dp.message(F.text == UPLOADS_BUTTON)
async def uploads_button(message: Message):
    await _send_uploads(message)


@dp.callback_query(F.data == "uplist")
async def on_uploads_list(cq: CallbackQuery):
    text, kb = _uploads_list_view(cq.from_user.id)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@dp.callback_query(F.data.regexp(r"^up:\d+$"))
async def on_upload_open(cq: CallbackQuery):
    u = get_upload(cq.from_user.id, int(cq.data.split(":")[1]))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer()


async def _account_for(cq: CallbackQuery, u: dict) -> dict | None:
    account = get_account_by_id(cq.from_user.id, u["account_id"])
    if not account:
        await cq.answer("Аккаунт этой загрузки больше не подключён", show_alert=True)
    return account


@dp.callback_query(F.data.regexp(r"^up(un|sh):\d+$"))
async def on_upload_share_toggle(cq: CallbackQuery):
    action, raw_id = cq.data.split(":")
    u = get_upload(cq.from_user.id, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    try:
        if action == "upun":
            await _with_drive(account, lambda s, t: revoke_public_access(s, t, u["folder_id"]))
            update_upload(cq.from_user.id, u["id"], shared=False)
            u["shared"] = False
            note = "Доступ по ссылке закрыт"
        else:
            await _with_drive(account, lambda s, t: publish_and_get_url(s, t, u["folder_id"]))
            update_upload(cq.from_user.id, u["id"], shared=True)
            u["shared"] = True
            note = "Доступ по ссылке открыт"
    except Exception as e:
        logging.exception("share toggle failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer(note)


COMMENT_FILE_NAME = "!COMMENT.txt"   # "!" sorts before digits and letters in Drive
TXT_MIME = "text/plain; charset=utf-8"


async def _sync_comment_file(account: dict, tg: int, folder_id: str) -> None:
    """Rebuilds !COMMENT.txt in the folder from the comments of every
    history row of that folder (one file per folder, oldest first). Creates
    it, replaces its content, or trashes it when no comments are left."""
    rows = list_uploads_by_folder(tg, folder_id)
    comments = [r["comment"] for r in rows if r.get("comment")]
    file_id = next((r["comment_file_id"] for r in rows if r.get("comment_file_id")), None)

    if not comments:
        if file_id:
            await _with_drive(account, lambda s, t: trash_file(s, t, file_id))
            set_folder_comment_file(tg, folder_id, None)
        return

    os.makedirs(config.TEMP_DIR, exist_ok=True)
    path = os.path.join(config.TEMP_DIR, f"comment_{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:   # BOM: Windows Notepad reads Cyrillic
        f.write("\n\n".join(comments) + "\n")
    try:
        done = False
        if file_id:
            try:
                await _with_drive(account, lambda s, t: replace_file_content(
                    s, t, path, file_id, TXT_MIME))
                done = True
            except RuntimeError as e:
                if "404" not in str(e):   # file was removed by hand -> recreate below
                    raise
        if not done:
            new_id = await _with_drive(account, lambda s, t: upload_file(
                s, t, path, folder_id, COMMENT_FILE_NAME, TXT_MIME))
            set_folder_comment_file(tg, folder_id, new_id)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def _after_removal(account: dict, tg: int, folder_id: str) -> bool:
    """Call once an upload's row is deleted and its files are trashed:
    updates (or removes) the folder's comment file, then trashes the folder
    if nothing is left in it. Returns True if the folder was trashed."""
    try:
        await _sync_comment_file(account, tg, folder_id)
    except Exception:
        logging.exception("comment file sync failed")

    async def _chk(session, token):
        if await folder_is_empty(session, token, folder_id):
            await trash_file(session, token, folder_id)
            return True
        return False

    return await _with_drive(account, _chk)


async def _restore_state(state: FSMContext, prev: str | None) -> None:
    await state.set_state(prev)
    await state.update_data(comment_upload_id=None, comment_prev_state=None)


@dp.callback_query(F.data.regexp(r"^cmt:\d+$"))
async def on_comment_start(cq: CallbackQuery, state: FSMContext):
    uid = int(cq.data.split(":")[1])
    u = get_upload(cq.from_user.id, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    cur = await state.get_state()
    if cur != Flow.waiting_comment.state:   # keep e.g. a pending folder-name question
        await state.update_data(comment_prev_state=cur)
    await state.update_data(comment_upload_id=uid)
    await state.set_state(Flow.waiting_comment)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cmtx")],
    ])
    await cq.message.answer(
        f"💬 Введите комментарий к папке «{u['folder_name']}» и отправьте одним сообщением.\n"
        f"Он сохранится в файл {COMMENT_FILE_NAME} в этой папке. "
        "Папка открыта всем, у кого есть ссылка, — комментарий они тоже увидят.",
        reply_markup=kb,
    )
    await cq.answer()


@dp.callback_query(F.data == "cmtx")
async def on_comment_cancel(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if await state.get_state() == Flow.waiting_comment.state:
        await _restore_state(state, data.get("comment_prev_state"))
    await cq.message.edit_text("❌ Комментарий отменён.")
    await cq.answer()


@dp.message(Flow.waiting_comment, F.text, ~F.text.startswith("/"))
async def on_comment_text(message: Message, state: FSMContext):
    tg = message.from_user.id
    data = await state.get_data()
    prev = data.get("comment_prev_state")
    uid = data.get("comment_upload_id")
    text = message.text.strip()
    u = get_upload(tg, uid) if uid else None
    account = get_account_by_id(tg, u["account_id"]) if u else None
    if not u or not account:
        await _restore_state(state, prev)
        await message.answer("Не нашёл эту загрузку или её аккаунт — комментарий не сохранён.")
        return

    block = f"[{datetime.now(LOCAL_TZ):%Y-%m-%d %H:%M}]\n{text}"
    new_comment = f"{u['comment']}\n\n{block}" if u.get("comment") else block
    try:
        update_upload(tg, uid, comment=new_comment)
    except Exception as e:
        logging.exception("saving comment failed")
        await _restore_state(state, prev)
        await message.answer(f"Не удалось сохранить комментарий: {e}")
        return
    try:
        await _sync_comment_file(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("comment upload failed")
        await _restore_state(state, prev)
        await message.answer(
            f"Комментарий сохранён в боте, но в папку загрузить не удалось: {e}\n"
            "Попробуй добавить ещё один комментарий — файл перезапишется целиком."
        )
        return
    await _restore_state(state, prev)
    await message.answer(f"💬 Сохранено в {COMMENT_FILE_NAME} (папка «{u['folder_name']}»).")


@dp.callback_query(F.data.regexp(r"^cmtdel:\d+$"))
async def on_comment_delete(cq: CallbackQuery):
    tg = cq.from_user.id
    uid = int(cq.data.split(":")[1])
    u = get_upload(tg, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    try:
        update_upload(tg, uid, comment=None)
        u["comment"] = None
        await _sync_comment_file(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("comment delete failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer("Комментарий удалён")


FILES_PER_PAGE = 8


def _upload_files_view(u: dict, page: int) -> tuple[str, InlineKeyboardMarkup]:
    files = _real(u["files"])
    pages = max(1, (len(files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = files[page * FILES_PER_PAGE:(page + 1) * FILES_PER_PAGE]
    uid = u["id"]
    kb = [[InlineKeyboardButton(
        text=f"🗑 {f['name']}"[:60], callback_data=f"upfd:{uid}:{f['id']}"
    )] for f in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"upf:{uid}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"upf:{uid}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"up:{uid}")])
    text = (f"Нажми на файл, чтобы удалить его (уйдёт в корзину Google Диска).\n"
            f"Папка «{u['folder_name']}», файлов: {len(files)}"
            + (f" · стр. {page + 1}/{pages}" if pages > 1 else ""))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@dp.callback_query(F.data.regexp(r"^upf:\d+:\d+$"))
async def on_upload_files(cq: CallbackQuery):
    _, raw_id, raw_page = cq.data.split(":")
    u = get_upload(cq.from_user.id, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    text, kb = _upload_files_view(u, int(raw_page))
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@dp.callback_query(F.data.regexp(r"^upfd:\d+:[\w-]+$"))
async def on_upload_file_delete(cq: CallbackQuery):
    _, raw_id, file_id = cq.data.split(":")
    tg = cq.from_user.id
    u = get_upload(tg, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    target = next((f for f in _real(u["files"]) if f["id"] == file_id), None)
    if not target:
        # tapped twice / stale button — just refresh the list
        text, kb = _upload_files_view(u, 0)
        await cq.message.edit_text(text, reply_markup=kb)
        await cq.answer("Этого файла уже нет в списке")
        return
    account = await _account_for(cq, u)
    if not account:
        return

    rest = [f for f in u["files"] if f["id"] != file_id]
    rest_real = _real(rest)
    folder_gone = False

    async def _do(session, token):
        await trash_file(session, token, file_id)
        if not rest_real:
            for rf in rest:            # service txt list goes with the last file
                await trash_file(session, token, rf["id"])

    try:
        await _with_drive(account, _do)
        if not rest_real:
            delete_upload(tg, u["id"])
            folder_gone = await _after_removal(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("delete single file failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return

    if not rest_real:
        if folder_gone:
            delete_uploads_by_folder(tg, u["folder_id"])
        await cq.message.edit_text("🗑 Файл удалён — это был последний в загрузке, она убрана из списка.")
        await cq.answer()
        return

    update_upload(tg, u["id"], files=rest)
    u["files"] = rest
    text, kb = _upload_files_view(u, 0)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(f"Удалён: {target['name']}")


@dp.callback_query(F.data.regexp(r"^updel:\d+$"))
async def on_upload_delete_ask(cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    u = get_upload(cq.from_user.id, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"updelok:{uid}")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"up:{uid}")],
    ])
    await cq.message.edit_text(
        f"Удалить {len(_real(u['files']))} файл(ов) из папки «{u['folder_name']}»?\n"
        "Они уйдут в корзину Google Диска (там их можно восстановить в течение "
        "30 дней). Если папка опустеет, она тоже уйдёт в корзину.",
        reply_markup=kb,
    )
    await cq.answer()


@dp.callback_query(F.data.regexp(r"^updelok:\d+$"))
async def on_upload_delete_do(cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    tg = cq.from_user.id
    u = get_upload(tg, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    await cq.message.edit_text("Удаляю…")

    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    failed: list[dict] = []

    async def _one(session, token, f):
        async with sem:
            try:
                await trash_file(session, token, f["id"])
            except GoogleAuthError:
                raise
            except Exception as e:
                logging.error(f"trash failed for {f['name']}: {e}")
                failed.append(f)

    async def _all(session, token):
        failed.clear()
        await asyncio.gather(*[_one(session, token, f) for f in u["files"]])

    folder_gone = False
    try:
        await _with_drive(account, _all)
        if not failed:
            delete_upload(tg, uid)
            folder_gone = await _after_removal(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("delete upload failed")
        await cq.message.edit_text(f"Ошибка при удалении: {e}")
        return

    if failed:
        update_upload(tg, uid, files=failed)
        await cq.message.edit_text(
            f"Удалено {len(_real(u['files'])) - len(_real(failed))}, не удалось: "
            f"{', '.join(f['name'] for f in failed)}.\nМожно повторить из списка /uploads."
        )
        return
    if folder_gone:
        delete_uploads_by_folder(tg, u["folder_id"])
    await cq.message.edit_text(
        "🗑 Удалено (в корзине Google Диска)."
        + (" Папка тоже." if folder_gone else " Папка осталась — в ней есть другие файлы.")
    )
    await cq.answer()


@dp.message(Command("logout"))
async def logout(message: Message):
    remove_all_accounts(message.from_user.id)
    await message.answer(
        "Отключил все твои Google-аккаунты. Чтобы подключить снова — /start.\n"
        "Токены удалены из базы. Доступ приложения можно также отозвать вручную "
        "на странице myaccount.google.com/permissions."
    )


@dp.message(Command("version"))
async def version_cmd(message: Message):
    """Diagnostic: confirms which code is actually running on the server,
    since a stale deploy is indistinguishable from a code bug otherwise."""
    await message.answer(f"PixKeep v{VERSION}")


PHOTO_FIX_ORDER = ["off", "auto", "always"]
PHOTO_FIX_LABELS = {
    "off": "выкл",
    "auto": "авто (только тёмные)",
    "always": "всегда (поднять тени)",
}


def _settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    clean_mark = "✅" if s["clean_metadata"] else "⬜"
    rename_mark = "✅" if s["anonymize_names"] else "⬜"
    date_mark = "✅" if s["auto_date_folder"] else "⬜"
    replace_mark = "✅" if s["replace_duplicates"] else "⬜"
    list_mark = "✅" if s["upload_list"] else "⬜"
    fix_label = PHOTO_FIX_LABELS.get(s["photo_fix"], PHOTO_FIX_LABELS["off"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{clean_mark} Чистить метаданные (EXIF/GPS)",
            callback_data="toggle:clean_metadata",
        )],
        [InlineKeyboardButton(
            text=f"{rename_mark} Обезличивать имена (001, 002…)",
            callback_data="toggle:anonymize_names",
        )],
        [InlineKeyboardButton(
            text=f"{date_mark} Папка по дате съёмки (без вопроса)",
            callback_data="toggle:auto_date_folder",
        )],
        [InlineKeyboardButton(
            text=f"{replace_mark} Заменять файлы с таким же именем",
            callback_data="toggle:replace_duplicates",
        )],
        [InlineKeyboardButton(
            text=f"🌗 Коррекция тёмных фото: {fix_label}",
            callback_data="cycle:photo_fix",
        )],
        [InlineKeyboardButton(
            text=f"{list_mark} Список файлов (txt) в папку",
            callback_data="toggle:upload_list",
        )],
    ])


@dp.message(Command("settings"))
async def settings_cmd(message: Message):
    if not get_active_account(message.from_user.id):
        await message.answer("Сначала подключи Google Drive командой /start.")
        return
    s = get_settings(message.from_user.id)
    await message.answer(
        "⚙️ Настройки загрузки — применяются к каждой следующей загрузке "
        "автоматически, спрашивать не буду:",
        reply_markup=_settings_keyboard(s),
    )


@dp.callback_query(F.data.startswith("toggle:"))
async def on_toggle_setting(cq: CallbackQuery):
    field = cq.data.split(":", 1)[1]
    s = get_settings(cq.from_user.id)
    if field not in s:
        await cq.answer()
        return
    if not isinstance(s[field], bool):
        await cq.answer()
        return
    new_value = not s[field]
    set_setting(cq.from_user.id, field, new_value)
    s[field] = new_value
    await cq.message.edit_reply_markup(reply_markup=_settings_keyboard(s))
    await cq.answer("Обновлено")


@dp.callback_query(F.data == "cycle:photo_fix")
async def on_cycle_photo_fix(cq: CallbackQuery):
    s = get_settings(cq.from_user.id)
    cur = s["photo_fix"] if s["photo_fix"] in PHOTO_FIX_ORDER else "off"
    new_value = PHOTO_FIX_ORDER[(PHOTO_FIX_ORDER.index(cur) + 1) % len(PHOTO_FIX_ORDER)]
    set_setting(cq.from_user.id, "photo_fix", new_value)
    s["photo_fix"] = new_value
    await cq.message.edit_reply_markup(reply_markup=_settings_keyboard(s))
    await cq.answer(PHOTO_FIX_LABELS[new_value])


# ------------------------------------------------------------ media intake ---
BOT_API_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # Bot API: bots can't download files > 20 MB


def _media_info(message: Message) -> tuple[str, str, int | None] | None:
    """(file_id, file_name, file_size) of whatever the message carries."""
    u = uuid.uuid4().hex
    if message.document:
        d = message.document
        return d.file_id, d.file_name or f"{u}.jpg", d.file_size
    if message.photo:
        p = message.photo[-1]
        return p.file_id, f"{u}.jpg", p.file_size
    if message.video:
        v = message.video
        return v.file_id, v.file_name or f"{u}.mp4", v.file_size
    if message.audio:
        a = message.audio
        return a.file_id, a.file_name or f"{u}.mp3", a.file_size
    if message.voice:
        return message.voice.file_id, f"{u}.ogg", message.voice.file_size
    if message.video_note:
        return message.video_note.file_id, f"{u}.mp4", message.video_note.file_size
    if message.animation:
        an = message.animation
        return an.file_id, an.file_name or f"{u}.mp4", an.file_size
    return None


class DownloadSkipped(Exception):
    """A file that can't be fetched; str(e) is the reason shown to the user."""


def _reserve_path(dest_dir: str, file_name: str) -> str:
    """Atomically claims a unique file name inside dest_dir (name.ext,
    name_2.ext, ...) so two files with the same name in one batch can't
    overwrite each other."""
    file_name = os.path.basename(file_name.replace("\\", "/")) or uuid.uuid4().hex
    base, ext = os.path.splitext(file_name)
    n = 1
    while True:
        candidate = file_name if n == 1 else f"{base}_{n}{ext}"
        path = os.path.join(dest_dir, candidate)
        try:
            with open(path, "xb"):
                return path
        except FileExistsError:
            n += 1


async def _download(message: Message, dest_dir: str) -> str | None:
    info = _media_info(message)
    if not info:
        return None
    file_id, file_name, file_size = info
    if file_size and file_size > BOT_API_DOWNLOAD_LIMIT:
        raise DownloadSkipped(f"больше 20 МБ ({file_size // (1024 * 1024)} МБ) — лимит Telegram для ботов")

    path = _reserve_path(dest_dir, file_name)
    try:
        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, destination=path)
    except Exception as e:
        try:
            os.remove(path)
        except OSError:
            pass
        if "too big" in str(e).lower():
            raise DownloadSkipped("больше 20 МБ — лимит Telegram для ботов")
        raise DownloadSkipped(f"не скачался ({str(e)[:60]})")
    return path


@dp.message(F.photo | F.document | F.video | F.audio | F.voice | F.video_note | F.animation)
async def handle_media(message: Message, state: FSMContext):
    if not get_active_account(message.from_user.id):
        await message.answer("Сначала подключи Google Drive командой /start.")
        return

    chat_id = message.chat.id
    pending_batch.setdefault(chat_id, []).append(message)
    # Cancel any previously scheduled flush for this chat and reschedule —
    # this is what lets an entire burst (any mix of albums and loose files)
    # get collected as one batch instead of processed piecemeal.
    old_task = pending_batch_tasks.get(chat_id)
    if old_task and not old_task.done():
        old_task.cancel()
    pending_batch_tasks[chat_id] = asyncio.create_task(
        _finish_batch(chat_id, state)
    )


async def _finish_batch(chat_id: int, state: FSMContext):
    try:
        await asyncio.sleep(BATCH_SILENCE_SECONDS)
    except asyncio.CancelledError:
        return  # a newer file arrived and rescheduled us; that task will run
    messages = pending_batch.pop(chat_id, [])
    pending_batch_tasks.pop(chat_id, None)
    if messages:
        # Wait for any batch already in flight for this chat to fully finish
        # first — see chat_locks above for why this matters.
        async with _get_chat_lock(chat_id):
            await _process(messages, state, chat_id)


# Concurrency caps for the steps below — modest by design: Render's free
# tier has limited CPU, and exiftool spawns a real OS subprocess per call,
# so too much parallelism there would thrash rather than help. Tune if needed.
DOWNLOAD_CONCURRENCY = 5
EXIF_CONCURRENCY = 4

# Used only as a display/fallback timezone for the auto-date-folder feature
# (EXIF timestamps have no timezone info at all, and Telegram's message.date
# is UTC) — approximates "today" the way a person in Belarus would see it.
LOCAL_TZ = ZoneInfo("Europe/Minsk")

_EXIF_DATETIME_FORMATS = ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S")


def _parse_exif_date(raw: str | None):
    """Parse an EXIF DateTimeOriginal/CreateDate string into a plain date.
    Returns None if raw is empty or doesn't match a known format."""
    if not raw:
        return None
    for fmt in _EXIF_DATETIME_FORMATS:
        try:
            return datetime.strptime(raw[:19], fmt).date()
        except ValueError:
            continue
    return None


def _pick_folder_date(exif_dates: list, messages: list[Message]):
    """One date for the whole batch: the most common EXIF capture date
    (ties broken by the earliest), or — if no file had a usable EXIF date
    at all — the earliest message's send time, converted to LOCAL_TZ."""
    if exif_dates:
        counts = Counter(exif_dates)
        top = max(counts.values())
        return min(d for d, c in counts.items() if c == top)

    send_times = [m.date for m in messages if m.date]
    if send_times:
        dt = min(send_times)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LOCAL_TZ).date()

    return datetime.now(LOCAL_TZ).date()


def _skipped_text(skipped: list[tuple[str, str]]) -> str:
    lines = [f"⚠️ Не принял {len(skipped)} файл(ов):"]
    for name, reason in skipped[:10]:
        lines.append(f"• {name} — {reason}")
    if len(skipped) > 10:
        lines.append(f"… и ещё {len(skipped) - 10}")
    return "\n".join(lines)


async def _process(messages: list[Message], state: FSMContext, chat_id: int):
    """Download each message's media, apply the user's standing settings
    (clean metadata / anonymize names — see /settings) immediately, and go
    straight to asking for a folder name. No per-upload questions anymore.

    Downloads and exiftool calls run concurrently (bounded by semaphores)
    instead of one-by-one. Crucially, exiftool itself is a blocking OS
    subprocess call — running it directly in this coroutine would freeze
    the whole bot (no other messages could be handled) for as long as it
    takes to process every file. asyncio.to_thread() offloads each call to
    a worker thread so the event loop stays responsive throughout.

    Each item carries its own message_id so we know which chat message to
    delete later, and its own status once uploaded."""
    telegram_id = messages[0].from_user.id
    settings = get_settings(telegram_id)

    work_dir = os.path.join(config.TEMP_DIR, str(chat_id), uuid.uuid4().hex)
    os.makedirs(work_dir, exist_ok=True)

    dl_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    exif_sem = asyncio.Semaphore(EXIF_CONCURRENCY)

    skipped: list[tuple[str, str]] = []   # (file name, reason) — not downloaded

    async def _download_bounded(msg: Message) -> str | None:
        async with dl_sem:
            try:
                return await _download(msg, work_dir)
            except DownloadSkipped as e:
                info = _media_info(msg)
                skipped.append((info[1] if info else "?", str(e)))
                return None
            except Exception as e:
                logging.exception("download failed")
                info = _media_info(msg)
                skipped.append((info[1] if info else "?", f"ошибка: {str(e)[:60]}"))
                return None

    async def _inspect(it: dict) -> dict:
        async with exif_sem:
            return await asyncio.to_thread(inspect_metadata, it["path"])

    async def _clean_one(it: dict, clean_dir: str) -> None:
        out = os.path.join(clean_dir, it["orig_name"])
        async with exif_sem:
            try:
                await asyncio.to_thread(strip_exif, it["path"], out)
            except Exception as e:
                logging.error(f"strip failed {it['path']}: {e}")
                shutil.copy(it["path"], out)
                it["clean_failed"] = True
        it["upload_path"] = out

    # v5.9: this is the long silent stretch — download from Telegram, run
    # exiftool on every file, optionally re-write clean copies — with
    # nothing printed to the chat until it's all done. Heartbeat covers it.
    # v5.11: TYPING here specifically (vs. the default UPLOAD_DOCUMENT used
    # for the Drive upload below in _do_upload) purely so the indicator
    # differs between the two stretches — Bot API has no generic
    # "processing" action to reach for.
    async with _heartbeat(chat_id, action=ChatAction.TYPING):
        paths = await asyncio.gather(*[_download_bounded(msg) for msg in messages])

        items = []
        for msg, path in zip(messages, paths):
            if not path:
                continue
            items.append({
                "message_id": msg.message_id,
                "path": path,
                "orig_name": os.path.basename(path),
            })

        if not items:
            msg = "Не нашёл файлов для обработки."
            if skipped:
                msg = _skipped_text(skipped) + "\nИсходные сообщения оставил в чате."
            await bot.send_message(chat_id, msg)
            shutil.rmtree(work_dir, ignore_errors=True)
            return

        metas = await asyncio.gather(*[_inspect(it) for it in items])
        gps_count = sum(1 for m in metas if m["has_gps"])
        exif_dates = [d for d in (_parse_exif_date(m["datetime"]) for m in metas) if d]

        # v5.18: cleaning drops the EXIF Orientation tag, so photos stored
        # sideways (typical for portrait shots) must be physically rotated first.
        if settings["clean_metadata"]:
            rot_dir = os.path.join(work_dir, "rotated")
            os.makedirs(rot_dir, exist_ok=True)
            for it in items:   # one at a time: keeps memory low
                out = os.path.join(rot_dir, it["orig_name"])
                if await asyncio.to_thread(normalize_orientation, it["path"], out):
                    it["path"] = out
                    it["rotated"] = True
        rotated_count = sum(1 for it in items if it.get("rotated"))

        fix_mode = settings["photo_fix"]
        if fix_mode in ("auto", "always"):
            fix_dir = os.path.join(work_dir, "fixed")
            os.makedirs(fix_dir, exist_ok=True)
            for it in items:   # one at a time on purpose: keeps memory low
                out = os.path.join(fix_dir, it["orig_name"])
                res = await asyncio.to_thread(fix_photo, it["path"], out, fix_mode)
                if res["edited"]:
                    it["path"] = out
                    it["edited"] = True
                else:
                    logging.info(f"photo_fix skipped {it['orig_name']}: {res['note']}")
        edited_count = sum(1 for it in items if it.get("edited"))

        if settings["clean_metadata"]:
            clean_dir = os.path.join(work_dir, "clean")
            os.makedirs(clean_dir, exist_ok=True)
            await asyncio.gather(*[_clean_one(it, clean_dir) for it in items])
        else:
            for it in items:
                it["upload_path"] = it["path"]

    width = max(3, len(str(len(items))))
    for i, it in enumerate(items, start=1):
        if settings["anonymize_names"]:
            ext = os.path.splitext(it["upload_path"])[1] or ".jpg"
            it["upload_name"] = f"{i:0{width}d}{ext}"
        else:
            it["upload_name"] = it["orig_name"]

    note = f"Получил {len(items)} файл(ов)."
    if skipped:
        note += "\n" + _skipped_text(skipped) + "\n(их сообщения остаются в чате)"
    unclean = [it["orig_name"] for it in items if it.get("clean_failed")]
    if unclean:
        note += (f"\n⚠️ Метаданные не удалось почистить (загружу как есть): "
                 + ", ".join(unclean[:5]) + (f" и ещё {len(unclean) - 5}" if len(unclean) > 5 else ""))
    if gps_count:
        note += f"\n⚠️ В {gps_count} из них есть GPS-координаты съёмки."
    note += (
        f"\nМетаданные: {'чищу' if settings['clean_metadata'] else 'оставляю как есть'}. "
        f"Имена: {'обезличиваю (001, 002…)' if settings['anonymize_names'] else 'оставляю оригинальные'}. "
        "(меняется в /settings)"
    )
    if settings["replace_duplicates"]:
        note += "\n♻️ Файлы с таким же именем в папке будут заменены."
    if rotated_count:
        note += f"\n↻ Повернул {rotated_count} фото в нормальное положение (иначе после чистки метаданных они лежали бы на боку)."
    if settings["photo_fix"] in ("auto", "always"):
        note += (f"\n🌗 Подправил яркость: {edited_count} из {len(items)}"
                 if edited_count else "\n🌗 Коррекция включена, но кадры не показались тёмными — не трогал (режим «всегда» подсветит любые).")

    if settings["auto_date_folder"]:
        folder_name = _pick_folder_date(exif_dates, messages).strftime("%d.%m.%y")
        note += f"\n\n📁 Папка будет создана автоматически: {folder_name}"
        await bot.send_message(chat_id, note)
        await _do_upload(chat_id, telegram_id, items, work_dir, folder_name)
        return

    # v5.6: FSM state (waiting_folder_name + its items/work_dir) is per chat,
    # not per batch. If a previous batch is already waiting on a folder-name
    # answer (e.g. Telegram split one 11-file send into a 10-file and a
    # 1-file media group — see the 10+1 report — and this is the second one
    # arriving right behind the first), setting state here would silently
    # overwrite the first batch's items/work_dir: whatever name gets typed
    # would only apply to the last batch, and the earlier one's files would
    # never upload and never get cleaned up. Queue it instead — it gets its
    # own prompt once the batch ahead of it is answered.
    current_fsm_state = await state.get_state()
    if current_fsm_state == Flow.waiting_folder_name.state:
        folder_name_queue.setdefault(chat_id, []).append({"items": items, "work_dir": work_dir, "note": note})
        await bot.send_message(
            chat_id,
            note + "\n\n⏳ Уже жду имя папки для предыдущей пачки — сначала "
                   "ответь на тот вопрос, потом спрошу про эту.",
        )
        return

    await _ask_folder_name(chat_id, state, items, work_dir, note)


# v5.6: queue of batches waiting for their turn to ask "укажите имя папки",
# used when more than one batch is in flight for the same chat at once (see
# the comment in _process above). Keyed by chat_id; each entry is the same
# {"items", "work_dir", "note"} shape _process would otherwise put straight
# into FSM state.
folder_name_queue: dict[int, list[dict]] = {}


async def _ask_folder_name(chat_id: int, state: FSMContext, items: list[dict],
                            work_dir: str, note: str) -> None:
    """Puts one batch into Flow.waiting_folder_name and sends its prompt.
    Shared by _process (first batch for a chat) and _ask_next_queued (any
    batch that had to wait behind it)."""
    await state.update_data(items=items, work_dir=work_dir)
    await state.set_state(Flow.waiting_folder_name)

    note += "\n\nУкажите имя папки на Google Drive. Если такой папки нет, она будет создана."
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отменить загрузку", callback_data="cancel")],
    ])
    await bot.send_message(chat_id, note, reply_markup=cancel_kb)


async def _ask_next_queued(chat_id: int, state: FSMContext) -> None:
    """Called once a batch's folder-name question has been resolved (answered
    or cancelled) — pops the next queued batch for this chat, if any, and
    asks its folder-name question."""
    queue = folder_name_queue.get(chat_id)
    if not queue:
        return
    nxt = queue.pop(0)
    if not queue:
        folder_name_queue.pop(chat_id, None)
    await _ask_folder_name(chat_id, state, nxt["items"], nxt["work_dir"], nxt["note"])


# ------------------------------------------------------------------ cancel ---
@dp.callback_query(F.data == "cancel")
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
    # v5.6: if another batch was queued behind this one (see folder_name_queue),
    # its turn to ask for a folder name is now — cancelling shouldn't strand it.
    async with _get_chat_lock(chat_id):
        await _ask_next_queued(chat_id, state)


# ------------------------------------------------------------ folder + up ----
UPLOAD_CONCURRENCY = 4


async def _upload_all(account: dict, items: list[dict], folder_name: str,
                      replace_duplicates: bool = False) -> tuple[list[dict], str, str]:
    """Upload every item to the user's Google Drive folder, concurrently
    (bounded by UPLOAD_CONCURRENCY). Continues past individual file
    failures (records them, doesn't abort).

    Unlike Yandex.Disk (which uploads with overwrite=true, making retries
    harmless), Drive's file-create call always makes a NEW file — so a
    naive whole-batch retry on token expiry would leave duplicates behind.
    Instead we refresh the access token at most once — an asyncio.Lock
    makes that safe even if several concurrent uploads hit 401 at the same
    time (only the first actually calls Google; the rest just wait for it
    and reuse the refreshed token) — and only the items that actually hit
    the 401 get retried; everything already uploaded stays untouched.

    replace_duplicates (settings, v5.10): when True, a file whose name
    already exists in this folder gets its content overwritten in place
    (same file id, same share link) instead of a second copy being
    created alongside it — see find_file_in_folder/replace_file_content.
    """
    folder_name = folder_name.strip()  # Drive folder names are plain metadata,
                                        # not a path, so no slash-stripping needed
    access_token = account["access_token"]
    account_id = account["id"]
    refreshed = False
    refresh_lock = asyncio.Lock()

    async def refresh_once() -> str:
        nonlocal access_token, refreshed
        async with refresh_lock:
            if not refreshed:
                new = await refresh_access_token(account["refresh_token"])
                access_token = new["access_token"]
                update_account_tokens(account_id, new["access_token"], new["refresh_token"])
                refreshed = True
        return access_token

    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    results: list[dict | None] = [None] * len(items)

    async with aiohttp.ClientSession() as session:
        try:
            folder_id = await ensure_folder(session, access_token, folder_name)
        except GoogleAuthError:
            access_token = await refresh_once()
            folder_id = await ensure_folder(session, access_token, folder_name)

        async def _put_one(token: str, it: dict) -> str:
            """Does the actual create-or-replace for one item, given a
            known-good token. Raises on failure so both call sites below
            (first try / after-refresh retry) can handle it the same way."""
            existing_id = None
            if replace_duplicates:
                existing_id = await find_file_in_folder(
                    session, token, folder_id, it["upload_name"]
                )
            if existing_id:
                await replace_file_content(session, token, it["upload_path"], existing_id)
                return existing_id
            return await upload_file(
                session, token, it["upload_path"], folder_id, it["upload_name"],
            )

        async def _upload_one(index: int, it: dict) -> None:
            async with sem:
                try:
                    fid = await _put_one(access_token, it)
                    results[index] = {**it, "success": True, "error": None, "file_id": fid}
                    return
                except GoogleAuthError:
                    pass  # fall through to a single refresh-and-retry below
                except Exception as e:
                    logging.error(f"upload failed for {it['upload_name']}: {e}")
                    results[index] = {**it, "success": False, "error": str(e)}
                    return

                try:
                    token = await refresh_once()
                    fid = await _put_one(token, it)
                    results[index] = {**it, "success": True, "error": None, "file_id": fid}
                except Exception as e:
                    logging.error(f"upload failed for {it['upload_name']} after refresh: {e}")
                    results[index] = {**it, "success": False, "error": str(e)}

        await asyncio.gather(*[_upload_one(i, it) for i, it in enumerate(items)])

        url = await publish_and_get_url(session, access_token, folder_id)
    return results, url, folder_id


def _build_list_text(folder_name: str, results: list[dict], s: dict) -> str:
    now = datetime.now(LOCAL_TZ)
    ok = [r for r in results if r["success"]]
    bad = [r for r in results if not r["success"]]
    lines = [
        "PixKeep upload list",
        f"Date: {now:%Y-%m-%d %H:%M} (Europe/Minsk)",
        f"Folder: {folder_name.strip()}",
        f"Metadata: {'cleaned' if s['clean_metadata'] else 'kept'}",
        f"Photo correction: {s['photo_fix']}",
        f"Files: {len(ok)} uploaded, {len(bad)} failed",
        "",
    ]
    for r in results:
        if r["success"]:
            line = f"{r['upload_name']} - {'edited' if r.get('edited') else 'original'}"
            if r.get("rotated"):
                line += ", rotated upright"
            if r.get("clean_failed"):
                line += " [metadata NOT cleaned]"
            if r["upload_name"] != r["orig_name"]:
                line += f" [was: {r['orig_name']}]"
        else:
            line = f"{r['orig_name']} - FAILED"
        lines.append(line)
    return "\n".join(lines) + "\n"


async def _upload_list_file(account: dict, folder_id: str, folder_name: str,
                            results: list[dict], s: dict, tmp_dir: str) -> dict:
    """Writes the txt list (UTF-8 with BOM so Windows Notepad reads it) and
    uploads it into the batch's folder. Returns a history entry flagged
    report=True so «Мои загрузки» can treat it as a service file."""
    name = f"upload_list_{datetime.now(LOCAL_TZ):%Y-%m-%d_%H-%M-%S}.txt"
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, name)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(_build_list_text(folder_name, results, s))
    try:
        file_id = await _with_drive(
            account, lambda sess, tok: upload_file(sess, tok, path, folder_id, name, TXT_MIME)
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"id": file_id, "name": name, "report": True}


async def _do_upload(chat_id: int, telegram_id: int, items: list[dict],
                     work_dir: str | None, folder_name: str) -> None:
    """Shared by both the manual folder-name prompt and the auto-date-folder
    path: upload, report per-file success/failure, delete succeeded chat
    messages, clean up temp files."""
    account = get_active_account(telegram_id)
    if not account:
        await bot.send_message(chat_id, "Диск не подключён. /start чтобы подключить.")
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        return

    await bot.send_message(chat_id, f"Загружаю в папку «{folder_name}»...")

    user_settings = get_settings(telegram_id)
    replace_duplicates = user_settings["replace_duplicates"]
    try:
        async with _heartbeat(chat_id):
            results, link, folder_id = await _upload_all(account, items, folder_name, replace_duplicates)
    except Exception as e:
        logging.exception("upload failed")
        await bot.send_message(chat_id, f"Ошибка при загрузке: {e}")
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        return

    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    # v5.17: optional txt list of the batch, uploaded next to the files.
    report_entry = None
    list_failed = False
    if user_settings["upload_list"] and succeeded:
        try:
            report_entry = await _upload_list_file(
                account, folder_id, folder_name, results, user_settings,
                work_dir or config.TEMP_DIR,
            )
        except Exception:
            logging.exception("couldn't upload txt list")
            list_failed = True

    lines = []
    if succeeded:
        names = ", ".join(r["upload_name"] for r in succeeded)
        lines.append(f"✅ Загружено ({len(succeeded)}): {names}")
    if failed:
        names = ", ".join(r["orig_name"] for r in failed)
        lines.append(f"❌ Не удалось загрузить ({len(failed)}): {names}")
    if report_entry:
        lines.append(f"📝 Список файлов: {report_entry['name']}")
    elif list_failed:
        lines.append("⚠️ txt-список загрузить не удалось (файлы при этом загружены).")
    if succeeded:
        lines.append(f"\nПапка (доступ на чтение всем, у кого есть ссылка):\n{link}")

    # v5.8: the link in the text above is already tappable (opens the
    # folder), but Telegram's default tap gesture for a plain URL is
    # "open", not "copy" — there's no way to long-press-copy it without
    # first opening the browser. copy_text is a separate inline-button type
    # (Bot API 7.x+) whose only job is "put this exact string on the
    # clipboard when tapped", so we add it alongside the text link rather
    # than instead of it — one tap to open, or one tap on the button to copy.
    upload_row_id = None
    if succeeded:
        try:
            upload_row_id = save_upload(
                telegram_id, account["id"], folder_id, folder_name.strip(), link,
                [{"id": r["file_id"], "name": r["upload_name"]} for r in succeeded]
                + ([report_entry] if report_entry else []),
            )
        except Exception:
            logging.exception("couldn't record upload in history")

    reply_markup = None
    if succeeded:
        rows = [[InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=link))]]
        if upload_row_id:
            rows.append([InlineKeyboardButton(text="💬 Добавить комментарий",
                                              callback_data=f"cmt:{upload_row_id}")])
        reply_markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=reply_markup)

    # Delete original chat messages only for files that actually uploaded.
    # v5.13: v5.12 wrapped this in the TYPING chat-action to give some sign
    # it was happening. But there's no "done" chat-action to follow it with
    # (Bot API's action vocabulary is a fixed set of in-progress states,
    # nothing means "finished") — so all that flash of "печатает..." right
    # at the end actually accomplished was raising "why did it say typing
    # and then nothing?". Deletion is quick regardless; doing it plainly
    # here means the upload report above is simply the last thing in the
    # chat, full stop, with nothing flickering after it.
    not_deleted = 0
    for r in succeeded:
        try:
            await bot.delete_message(chat_id, r["message_id"])
        except Exception as e:
            logging.warning(f"couldn't delete message {r['message_id']}: {e}")
            not_deleted += 1

    # Only speak up here if something actually needs attention (a handful
    # of messages Telegram wouldn't let us delete, e.g. too old) — that's
    # real information the person has to act on, not a routine confirmation.
    # When everything deleted cleanly, the report above stays the last word.
    if not_deleted:
        await bot.send_message(
            chat_id,
            f"🗑 Не смог удалить {not_deleted} исходных сообщени(й) из чата — "
            "обычно если прошло слишком много времени, удали вручную при желании.",
        )

    if work_dir and os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)


@dp.message(Flow.waiting_folder_name, F.text)
async def on_folder_name(message: Message, state: FSMContext):
    chat_id = message.chat.id
    data = await state.get_data()
    items = data.get("items", [])
    work_dir = data.get("work_dir")
    folder_name = message.text.strip()

    if not items:
        await message.answer("Нет файлов для загрузки, начни заново.")
        await state.clear()
        return

    await state.clear()
    # v5.6: hold the same per-chat lock _process uses, so a batch that's
    # still queued (waiting for THIS answer to free things up) can't get
    # its own prompt asked out of turn, and so this upload's ensure_folder()
    # call can't race a concurrently-arriving new batch's.
    async with _get_chat_lock(chat_id):
        await _do_upload(chat_id, message.from_user.id, items, work_dir, folder_name)
        await _ask_next_queued(chat_id, state)


# ------------------------------------------------------- oauth web server ----
async def oauth_callback(request: web.Request) -> web.Response:
    state = request.query.get("state", "")
    code = request.query.get("code", "")
    error = request.query.get("error")
    if error:
        return web.Response(text=f"Отказано в доступе: {error}", content_type="text/plain")
    try:
        telegram_id, access_token, refresh_token = await exchange_code(state, code)
        email = await get_user_email(access_token)
        add_or_update_account(telegram_id, email, access_token, refresh_token)
        await bot.send_message(
            telegram_id,
            f"Google Drive подключён ✅ ({email})\nТеперь пришли фото или файлы."
        )
        return web.Response(
            text="Готово! Диск подключён. Можешь вернуться в Telegram.",
            content_type="text/plain",
        )
    except Exception as e:
        logging.exception("oauth callback failed")
        return web.Response(text=f"Ошибка авторизации: {e}", content_type="text/plain")


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def run_web():
    app = web.Application()
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logging.info(f"web server on :{config.PORT}")


BOT_COMMANDS = [
    BotCommand(command="start", description="Подключить Диск / статус"),
    BotCommand(command="uploads", description="Мои загрузки: ссылки и удаление"),
    BotCommand(command="accounts", description="Аккаунты: список и переключение"),
    BotCommand(command="settings", description="Настройки очистки и переименования"),
    BotCommand(command="logout", description="Отключить все аккаунты"),
    BotCommand(command="version", description="Какая версия кода сейчас работает"),
]


async def main():
    os.makedirs(config.TEMP_DIR, exist_ok=True)
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
