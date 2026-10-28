"""
Batch preparation: download -> inspect metadata -> rotate upright -> fix dark
photos -> clean metadata -> final names. Works on MediaRef receipts and
writes only to work_dir; it never talks to the chat, so the same code serves
an immediate upload and a scheduled one.

exiftool and Pillow are blocking, so every call runs in a worker thread
(asyncio.to_thread) and the bot stays responsive. Concurrency is modest on
purpose: the host has little CPU, and exiftool spawns a process per call.
"""
import asyncio
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import date

from pixkeep.config import config
from pixkeep.exif_utils import inspect_metadata, strip_exif
from pixkeep.media import (
    BOT_API_DOWNLOAD_LIMIT,
    MediaRef,
    parse_exif_date,
    unique_names,
)
from pixkeep.photo_fix import fix_photo, normalize_orientation
from pixkeep.runtime import bot

DOWNLOAD_CONCURRENCY = 5
EXIF_CONCURRENCY = 4


class DownloadSkipped(Exception):
    """A file that can't be fetched; str(e) is the reason shown to the user."""


@dataclass
class PreparedBatch:
    # one dict per downloaded file: message_id, path, orig_name, upload_path,
    # upload_name, and flags rotated / edited / clean_failed
    items: list[dict] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (name, reason)
    gps_count: int = 0
    exif_dates: list[date] = field(default_factory=list)
    rotated_count: int = 0
    edited_count: int = 0


async def _download(ref: MediaRef, path: str) -> None:
    if ref.size and ref.size > BOT_API_DOWNLOAD_LIMIT:
        raise DownloadSkipped(f"больше 20 МБ ({ref.size // (1024 * 1024)} МБ) — лимит Telegram для ботов")
    try:
        file = await bot.get_file(ref.file_id)
        await bot.download_file(file.file_path, destination=path)
    except Exception as e:
        try:
            os.remove(path)
        except OSError:
            pass
        if "too big" in str(e).lower():
            raise DownloadSkipped("больше 20 МБ — лимит Telegram для ботов")
        raise DownloadSkipped(f"не скачался ({str(e)[:60]})")


async def prepare_batch(refs: list[MediaRef], settings: dict, work_dir: str) -> PreparedBatch:
    os.makedirs(work_dir, exist_ok=True)
    batch = PreparedBatch()
    dl_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    exif_sem = asyncio.Semaphore(EXIF_CONCURRENCY)
    budget = [config.MAX_BATCH_MB * 1024 * 1024]   # bytes still allowed in this batch
    local_names = unique_names([r.name for r in refs])

    async def _download_bounded(ref: MediaRef, name: str) -> str | None:
        async with dl_sem:
            size = ref.size or 0
            if size > budget[0]:
                batch.skipped.append((ref.name, f"пачка больше {config.MAX_BATCH_MB} МБ — пришли отдельно"))
                return None
            budget[0] -= size
            path = os.path.join(work_dir, name)
            try:
                await _download(ref, path)
                return path
            except DownloadSkipped as e:
                batch.skipped.append((ref.name, str(e)))
            except Exception as e:
                logging.exception("download failed")
                batch.skipped.append((ref.name, f"ошибка: {str(e)[:60]}"))
            return None

    paths = await asyncio.gather(*[_download_bounded(r, n) for r, n in zip(refs, local_names)])
    for ref, path in zip(refs, paths):
        if path:
            batch.items.append({
                "message_id": ref.message_id,
                "path": path,
                "orig_name": os.path.basename(path),
            })
    items = batch.items
    if not items:
        return batch

    async def _inspect(it: dict) -> dict:
        async with exif_sem:
            return await asyncio.to_thread(inspect_metadata, it["path"])

    metas = await asyncio.gather(*[_inspect(it) for it in items])
    batch.gps_count = sum(1 for m in metas if m["has_gps"])
    batch.exif_dates = [d for d in (parse_exif_date(m["datetime"]) for m in metas) if d]

    # Cleaning drops the EXIF Orientation tag, so photos stored sideways
    # (typical for portrait shots) must be physically rotated first.
    if settings["clean_metadata"]:
        rot_dir = os.path.join(work_dir, "rotated")
        os.makedirs(rot_dir, exist_ok=True)
        for it in items:   # one at a time: keeps memory low
            out = os.path.join(rot_dir, it["orig_name"])
            if await asyncio.to_thread(normalize_orientation, it["path"], out):
                it["path"] = out
                it["rotated"] = True
    batch.rotated_count = sum(1 for it in items if it.get("rotated"))

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
    batch.edited_count = sum(1 for it in items if it.get("edited"))

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
    return batch
