"""
Delivery: upload prepared items to the active Google account's Drive, report
to the chat, delete the source messages of what reached Drive, keep failed
files for a retry. Used by the immediate flow (and later by scheduled uploads).
"""
import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
import time
import uuid
from datetime import datetime

from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from pixkeep import db, texts
from pixkeep.config import config
from pixkeep.drive import TXT_MIME, DriveClient, account_quota, with_drive
from pixkeep.drive_utils import (
    ensure_folder,
    find_file_in_folder,
    list_folder_md5,
    publish_and_get_url,
    replace_file_content,
    upload_file,
)
from pixkeep.oauth import TokenRevokedError, build_auth_url
from pixkeep.runtime import LOCAL_TZ, bot, heartbeat

UPLOAD_CONCURRENCY = 4


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def plan_uploads(names: list[str], hashes: list[str], known: dict[str, str],
                 names_in_folder: dict[str, set[str]], replace_duplicates: bool) -> list[str | None]:
    """For each item: None = upload it, or the name of the file it duplicates
    (same md5 already in the folder or earlier in this batch) = skip it.

    "Заменять файлы с таким же именем" wins over content-skipping: if a file
    with this very name already exists with DIFFERENT content, the new content
    must replace it, even when the same bytes also sit in the folder under
    another name."""
    seen = dict(known)
    plan: list[str | None] = []
    for name, h in zip(names, hashes):
        wants_replace = (replace_duplicates and name in names_in_folder
                         and h not in names_in_folder[name])
        if h in seen and not wants_replace:
            plan.append(seen[h])
        else:
            seen[h] = name
            plan.append(None)
    return plan


async def upload_all(account: dict, items: list[dict], folder_name: str,
                     replace_duplicates: bool = False,
                     on_progress=None) -> tuple[list[dict], str, str]:
    """Upload every item into the folder, concurrently (UPLOAD_CONCURRENCY),
    continuing past individual failures. Returns (results, link, folder_id).

    Drive's file-create always makes a NEW file, so a whole-batch retry would
    leave duplicates: DriveClient refreshes the token once and only the calls
    that actually hit the 401 are retried.

    replace_duplicates: a file whose name already exists in the folder gets
    its content overwritten in place (same id, same link) instead of a second
    copy being created alongside it."""
    folder_name = folder_name.strip()
    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    results: list[dict | None] = [None] * len(items)

    async with DriveClient(account) as drive:
        folder_id = await drive.call(lambda s, t: ensure_folder(s, t, folder_name))

        try:
            known, names_in_folder = await drive.call(lambda s, t: list_folder_md5(s, t, folder_id))
        except TokenRevokedError:
            raise
        except Exception as e:
            logging.warning(f"duplicate check against folder skipped: {e}")
            known, names_in_folder = {}, {}
        hashes = await asyncio.gather(*[asyncio.to_thread(md5_file, it["upload_path"]) for it in items])
        plan = plan_uploads([it["upload_name"] for it in items], hashes, known,
                            names_in_folder, replace_duplicates)
        to_upload = []
        for i, (it, dup_of) in enumerate(zip(items, plan)):
            if dup_of is not None:
                results[i] = {**it, "success": True, "error": None,
                              "file_id": None, "duplicate_of": dup_of}
            else:
                to_upload.append(i)

        done = len(items) - len(to_upload)
        total = len(items)

        async def _put_one(session, token, it: dict) -> str:
            existing_id = None
            if replace_duplicates:
                existing_id = await find_file_in_folder(session, token, folder_id, it["upload_name"])
            if existing_id:
                await replace_file_content(session, token, it["upload_path"], existing_id)
                return existing_id
            return await upload_file(session, token, it["upload_path"], folder_id, it["upload_name"])

        async def _upload_one(i: int) -> None:
            nonlocal done
            it = items[i]
            async with sem:
                try:
                    fid = await drive.call(lambda s, t: _put_one(s, t, it))
                    results[i] = {**it, "success": True, "error": None, "file_id": fid}
                except Exception as e:
                    logging.error(f"upload failed for {it['upload_name']}: {e}")
                    results[i] = {**it, "success": False, "error": str(e)}
            done += 1
            if on_progress:
                with contextlib.suppress(Exception):
                    await on_progress(done, total)

        await asyncio.gather(*[_upload_one(i) for i in to_upload])
        link = await drive.call(lambda s, t: publish_and_get_url(s, t, folder_id))
    return results, link, folder_id


async def upload_list_file(account: dict, folder_id: str, folder_name: str,
                           results: list[dict], s: dict, tmp_dir: str) -> dict:
    """Writes the txt list (UTF-8 with BOM so Windows Notepad reads it) and
    uploads it into the batch's folder. Returns a history entry flagged
    report=True so «Мои загрузки» can treat it as a service file."""
    name = f"upload_list_{datetime.now(LOCAL_TZ):%Y-%m-%d_%H-%M-%S}.txt"
    os.makedirs(tmp_dir, exist_ok=True)
    path = os.path.join(tmp_dir, name)
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(texts.build_list_text(folder_name, results, s))
    try:
        file_id = await with_drive(
            account, lambda sess, tok: upload_file(sess, tok, path, folder_id, name, TXT_MIME))
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return {"id": file_id, "name": name, "report": True}


# Failed files keep their temp dir for RETRY_TTL seconds so the
# "🔁 Повторить неудачные" button can re-send them without asking again.
RETRY_TTL = 30 * 60
retry_store: dict[str, dict] = {}


async def _expire_retry(token: str) -> None:
    try:
        await asyncio.sleep(RETRY_TTL)
    except asyncio.CancelledError:
        return
    entry = retry_store.pop(token, None)
    if entry and entry["work_dir"] and os.path.exists(entry["work_dir"]):
        shutil.rmtree(entry["work_dir"], ignore_errors=True)


async def deliver(chat_id: int, telegram_id: int, items: list[dict],
                  work_dir: str | None, folder_name: str) -> None:
    """Upload (with live progress), report per-file success/failure, delete
    source messages of what got to Drive, keep failed files for retry, clean
    up temp files."""
    def _cleanup() -> None:
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

    account = await db.get_active_account(telegram_id)
    if not account:
        await bot.send_message(chat_id, "Диск не подключён. /start чтобы подключить.")
        _cleanup()
        return

    # Free-space heads-up (informational: the upload still goes ahead).
    try:
        total_size = sum(os.path.getsize(it["upload_path"]) for it in items
                         if os.path.exists(it["upload_path"]))
        q = await account_quota(account)
        if q and q[1] and q[0] + total_size > q[1]:
            await bot.send_message(chat_id, texts.quota_warning(
                account["email"], max(q[1] - q[0], 0), total_size))
    except Exception:
        logging.exception("quota pre-check failed")

    status = await bot.send_message(chat_id, texts.uploading(folder_name, 0, len(items)))
    last_edit = [0.0]

    async def on_progress(done: int, total: int) -> None:
        now = time.monotonic()
        if done < total and now - last_edit[0] < 2.0:
            return
        last_edit[0] = now
        with contextlib.suppress(Exception):
            await status.edit_text(texts.uploading(folder_name, done, total))

    async def _drop_status() -> None:
        with contextlib.suppress(Exception):
            await status.delete()

    user_settings = await db.get_settings(telegram_id)
    try:
        async with heartbeat(chat_id):
            results, link, folder_id = await upload_all(
                account, items, folder_name, user_settings["replace_duplicates"], on_progress)
    except TokenRevokedError:
        await _drop_status()
        await bot.send_message(
            chat_id,
            "Google отозвал доступ к этому аккаунту. Подключи его заново — "
            "файлы из этой пачки придётся прислать ещё раз.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                text="🔗 Подключить заново", url=build_auth_url(telegram_id))]]),
        )
        _cleanup()
        return
    except Exception as e:
        logging.exception("upload failed")
        await _drop_status()
        await bot.send_message(chat_id, f"Ошибка при загрузке: {e}")
        _cleanup()
        return
    await _drop_status()

    succeeded = [r for r in results if r["success"]]            # on Drive (new or already there)
    uploaded = [r for r in succeeded if not r.get("duplicate_of")]
    failed = [r for r in results if not r["success"]]

    # Optional txt list of the batch, uploaded next to the files.
    report_entry = None
    list_failed = False
    if user_settings["upload_list"] and succeeded:
        try:
            report_entry = await upload_list_file(
                account, folder_id, folder_name, results, user_settings,
                work_dir or config.TEMP_DIR)
        except Exception:
            logging.exception("couldn't upload txt list")
            list_failed = True

    quota = await account_quota(account, fresh=True) if succeeded else None
    report = texts.upload_report(results, folder_name, link, report_entry, list_failed,
                                 account["email"], quota)

    upload_row_id = None
    if uploaded:
        try:
            upload_row_id = await db.save_upload(
                telegram_id, account["id"], folder_id, folder_name.strip(), link,
                [{"id": r["file_id"], "name": r["upload_name"]} for r in uploaded]
                + ([report_entry] if report_entry else []),
            )
        except Exception:
            logging.exception("couldn't record upload in history")

    rows = []
    if succeeded:
        rows.append([InlineKeyboardButton(text="📋 Скопировать ссылку", copy_text=CopyTextButton(text=link))])
        if upload_row_id:
            rows.append([InlineKeyboardButton(text="💬 Добавить комментарий",
                                              callback_data=f"cmt:{upload_row_id}")])
    keep_dir = False
    if failed:
        token = uuid.uuid4().hex[:10]
        retry_store[token] = {
            "items": [items[i] for i, r in enumerate(results) if not r["success"]],
            "work_dir": work_dir, "folder_name": folder_name, "telegram_id": telegram_id,
            "task": asyncio.create_task(_expire_retry(token)),
        }
        keep_dir = True
        rows.append([InlineKeyboardButton(
            text=f"🔁 Повторить неудачные ({len(failed)})", callback_data=f"retry:{token}")])
    await bot.send_message(chat_id, report,
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    # Delete source messages only for files that are on Drive, plainly, so the
    # report stays the last thing in chat.
    not_deleted = 0
    for r in succeeded:
        try:
            await bot.delete_message(chat_id, r["message_id"])
        except Exception as e:
            logging.warning(f"couldn't delete message {r['message_id']}: {e}")
            not_deleted += 1
    if not_deleted:
        await bot.send_message(
            chat_id,
            f"🗑 Не смог удалить {not_deleted} исходных сообщени(й) из чата — "
            "обычно если прошло слишком много времени, удали вручную при желании.",
        )

    if not keep_dir:
        _cleanup()
