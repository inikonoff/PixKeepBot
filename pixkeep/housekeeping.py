"""
Background housekeeping: sweeps forgotten temp files and warns about a
nearly full disk. Normally every batch removes its own work dir; what can
linger is a batch whose folder question was never answered (the work dir
stays until a restart) or a file left by a crash.
"""
import asyncio
import contextlib
import logging
import os
import shutil
import time

from pixkeep.config import config
from pixkeep.runtime import bot

SWEEP_INTERVAL = 30 * 60        # seconds between sweeps
MAX_AGE = 6 * 3600              # temp entries older than this are removed
LOW_DISK_BYTES = 500 * 1024 ** 2
LOW_DISK_ALERT_EVERY = 24 * 3600

_last_alert = [0.0]


def _newest_mtime(path: str) -> float:
    newest = os.path.getmtime(path)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            with contextlib.suppress(OSError):
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
    return newest


def sweep(temp_dir: str, max_age: float = MAX_AGE, keep: set[str] = frozenset(),
          now: float | None = None) -> int:
    """Removes batch work dirs (TEMP_DIR/<chat>/<id>) and loose temp files
    untouched for max_age seconds, except paths in `keep` (retry store).
    Returns how many entries were removed."""
    now = now or time.time()
    removed = 0
    if not os.path.isdir(temp_dir):
        return 0
    for name in os.listdir(temp_dir):
        path = os.path.join(temp_dir, name)
        candidates = ([os.path.join(path, sub) for sub in os.listdir(path)]
                      if os.path.isdir(path) else [path])
        for c in candidates:
            if c in keep:
                continue
            try:
                if now - _newest_mtime(c) < max_age:
                    continue
                if os.path.isdir(c):
                    shutil.rmtree(c, ignore_errors=True)
                else:
                    os.remove(c)
                removed += 1
            except OSError:
                continue
        with contextlib.suppress(OSError):
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
    return removed


async def _check_disk() -> None:
    free = shutil.disk_usage(config.TEMP_DIR).free
    if free >= LOW_DISK_BYTES:
        return
    logging.warning(f"low disk space: {free // (1024 ** 2)} MB free in {config.TEMP_DIR}")
    if time.monotonic() - _last_alert[0] < LOW_DISK_ALERT_EVERY and _last_alert[0]:
        return
    _last_alert[0] = time.monotonic()
    for admin in config.ADMIN_IDS:
        with contextlib.suppress(Exception):
            await bot.send_message(admin, f"⚠️ На сервере бота мало места: свободно {free // (1024 ** 2)} МБ. "
                                          "Большие пачки могут не скачаться.")


async def housekeeping_loop() -> None:
    from pixkeep.delivery import retry_store  # late import: delivery imports a lot
    while True:
        await asyncio.sleep(SWEEP_INTERVAL)
        try:
            keep = {e["work_dir"] for e in retry_store.values() if e.get("work_dir")}
            n = await asyncio.to_thread(sweep, config.TEMP_DIR, MAX_AGE, keep)
            if n:
                logging.info(f"housekeeping: removed {n} stale temp entries")
            await _check_disk()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("housekeeping failed")
