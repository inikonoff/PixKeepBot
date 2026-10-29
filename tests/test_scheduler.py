"""Scheduled upload: planning math and the whole queue lifecycle with an
in-memory database, a fake bot and a fake pipeline/upload (no network)."""
import asyncio
import random
from datetime import datetime, timedelta, timezone

import pytest

from pixkeep import db, scheduler
from pixkeep.delivery import Outcome
from pixkeep.media import MediaRef
from pixkeep.oauth import TokenRevokedError
from pixkeep.pipeline import PreparedBatch
from pixkeep.runtime import LOCAL_TZ

# ---------------------------------------------------------------- plan_next --


def _local(h, m):
    return datetime(2026, 10, 29, h, m, tzinfo=LOCAL_TZ).astimezone(timezone.utc)


def test_pause_is_random_within_range():
    now = _local(9, 0)
    rnd = random.Random(1)
    pauses = [(scheduler.plan_next(now, 10, 15, None, rnd)[0] - now).total_seconds() for _ in range(200)]
    assert all(600 <= p <= 900 for p in pauses)
    assert len({round(p) for p in pauses}) > 100       # not a fixed step


def test_deadline_caps_the_last_tick():
    when, flush = scheduler.plan_next(_local(13, 55), 10, 15, "14:00", random.Random(1))
    assert (when, flush) == (_local(14, 0), True)
    when, flush = scheduler.plan_next(_local(13, 0), 10, 15, "14:00", random.Random(1))
    assert not flush and when < _local(14, 0)


def test_deadline_already_passed_does_not_apply():
    now = _local(15, 0)
    when, flush = scheduler.plan_next(now, 10, 15, "14:00", random.Random(1))
    assert not flush and when > now


def test_deadline_ahead():
    assert scheduler._deadline_ahead("14:00", _local(13, 0))
    assert not scheduler._deadline_ahead("14:00", _local(14, 30))
    assert not scheduler._deadline_ahead("", _local(9, 0))


def test_parse_hhmm():
    assert scheduler.parse_hhmm("13:30") == (13, 30)
    assert scheduler.parse_hhmm("9.05") == (9, 5)
    assert scheduler.parse_hhmm("25:00") is None and scheduler.parse_hhmm("x") is None


# ----------------------------------------------------------- fake world -----
class Store:
    def __init__(self):
        self.queue, self.state, self.n = [], {}, 0

    async def queue_add(self, tid, chat_id, folder, files, attempts=0):
        self.n += 1
        self.queue.append({"id": self.n, "telegram_id": tid, "chat_id": chat_id,
                           "folder_name": folder, "files": files, "attempts": attempts})
        return self.n

    async def queue_list(self, tid):
        rows = [dict(e) for e in self.queue if e["telegram_id"] == tid]
        return sorted(rows, key=lambda e: (-e["attempts"], e["id"]))

    async def queue_update(self, eid, **f):
        next(e for e in self.queue if e["id"] == eid).update(f)

    async def queue_delete(self, eid):
        self.queue = [e for e in self.queue if e["id"] != eid]

    async def queue_users(self):
        return sorted({e["telegram_id"] for e in self.queue})

    async def get_queue_state(self, tid):
        return dict(self.state[tid]) if tid in self.state else None

    async def set_queue_state(self, tid, chat_id, **f):
        row = self.state.setdefault(tid, {"telegram_id": tid, "chat_id": chat_id, "next_tick_at": None,
                                          "flush": False, "paused": False, "status_message_id": None,
                                          "summary": []})
        row.update(f)

    async def clear_queue_state(self, tid):
        self.state.pop(tid, None)

    async def get_settings(self, tid):
        return {**db.DEFAULT_SETTINGS, "deferred": True}

    async def get_active_account(self, tid):
        return {"id": 1, "email": "me@x.y", "access_token": "t", "refresh_token": "r"}


class Bot:
    def __init__(self):
        self.sent, self.deleted, self.edits, self.n = [], [], [], 100

    async def send_message(self, chat_id, text, **kw):
        self.n += 1
        self.sent.append(text)
        return type("M", (), {"message_id": self.n})()

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)

    async def delete_message(self, chat_id, mid):
        self.deleted.append(mid)


@pytest.fixture
def world(monkeypatch):
    store, bot = Store(), Bot()
    for name in ("queue_add", "queue_list", "queue_update", "queue_delete", "queue_users",
                 "get_queue_state", "set_queue_state", "clear_queue_state", "get_settings",
                 "get_active_account"):
        monkeypatch.setattr(db, name, getattr(store, name))
    monkeypatch.setattr(scheduler, "bot", bot)
    monkeypatch.setattr(scheduler, "plan_next",
                        lambda now, lo, hi, dl, rnd=None: (now + timedelta(seconds=0.05), False))
    uploads = []
    fail = {"names": set(), "revoked": False}

    async def prepare(refs, settings, work_dir):
        items = [{"message_id": r.message_id, "orig_name": r.name, "upload_name": r.name,
                  "upload_path": "/dev/null", "path": "/dev/null"} for r in refs]
        return PreparedBatch(items=items)

    async def upload(account, tid, items, folder, work_dir, on_progress=None):
        if fail["revoked"]:
            raise TokenRevokedError("revoked")
        uploads.append((folder, [it["upload_name"] for it in items]))
        results = [{**it, "success": it["upload_name"] not in fail["names"]} for it in items]
        return Outcome(results=results, link=f"https://drive.google.com/drive/folders/F{folder}",
                       folder_id=f"F{folder}", folder_name=folder, email="me@x.y")

    async def delete_sources(chat_id, results):
        return 0

    monkeypatch.setattr(scheduler, "prepare_batch", prepare)
    monkeypatch.setattr(scheduler, "upload_and_record", upload)
    monkeypatch.setattr(scheduler, "delete_sources", delete_sources)
    scheduler._tasks.clear()
    return store, bot, uploads, fail


def _refs(start, n):
    return [MediaRef(kind="document", file_id=f"f{i}", file_unique_id=f"u{i}", name=f"{i}.jpg", size=1,
                     message_id=i, date=datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc))
            for i in range(start, start + n)]


async def _drain(tid=1, timeout=3.0):
    task = scheduler._tasks.get(tid)
    if task:
        await asyncio.wait_for(task, timeout)


def test_one_whole_batch_per_tick_then_summary(world):
    store, bot, uploads, _ = world

    async def go():
        await scheduler.enqueue(1, 1, _refs(1, 3), "Trip")
        await scheduler.enqueue(1, 1, _refs(10, 2), None)     # auto date folder
        await _drain()

    asyncio.run(go())
    assert uploads == [("Trip", ["1.jpg", "2.jpg", "3.jpg"]), ("29.10.26", ["10.jpg", "11.jpg"])]
    assert store.queue == [] and 1 not in store.state
    summary = bot.sent[-1]
    assert "Очередь загружена" in summary and "2 пачки, 5 файлов" in summary
    assert "Trip" in summary and "29.10.26" in summary and "drive.google.com/…/" in summary
    assert any("Очередь загрузки" in t for t in bot.sent[:-1])     # status message


def test_failed_files_retry_first_then_are_dropped(world):
    store, bot, uploads, fail = world
    fail["names"] = {"2.jpg"}

    async def go():
        await scheduler.enqueue(1, 1, _refs(1, 2), "A")
        await scheduler.enqueue(1, 1, _refs(5, 1), "B")
        await _drain()

    asyncio.run(go())
    # 2.jpg is retried before batch B, three attempts in total, then dropped
    assert uploads == [("A", ["1.jpg", "2.jpg"]), ("A", ["2.jpg"]), ("A", ["2.jpg"]), ("B", ["5.jpg"])]
    assert any("Не загрузилось после трёх попыток" in t and "2.jpg" in t for t in bot.sent)
    assert store.queue == []


def test_flush_now_uploads_everything_in_one_go(world, monkeypatch):
    store, bot, uploads, _ = world
    monkeypatch.setattr(scheduler, "plan_next",
                        lambda now, lo, hi, dl, rnd=None: (now + timedelta(hours=1), False))

    async def go():
        for i in range(3):
            await scheduler.enqueue(1, 1, _refs(10 * i + 1, 1), "T")
        await asyncio.sleep(0.05)
        assert uploads == []                         # still waiting for the first tick
        await scheduler.flush_now(1, 1)
        await _drain()

    asyncio.run(go())
    assert [u[1] for u in uploads] == [["1.jpg"], ["11.jpg"], ["21.jpg"]]
    assert store.queue == []


def test_revoked_access_pauses_and_resume_continues(world):
    store, bot, uploads, fail = world
    fail["revoked"] = True

    async def go():
        await scheduler.enqueue(1, 1, _refs(1, 1), "T")
        await _drain()
        assert store.state[1]["paused"] and len(store.queue) == 1
        assert any("Google отозвал доступ" in t for t in bot.sent)
        fail["revoked"] = False
        await scheduler.resume(1, 1)
        await _drain()

    asyncio.run(go())
    assert uploads == [("T", ["1.jpg"])] and store.queue == []


def test_restart_resumes_stored_queue(world):
    store, bot, uploads, _ = world

    async def go():
        await store.queue_add(1, 1, "Old", [r.to_dict() for r in _refs(1, 2)])
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        await store.set_queue_state(1, 1, next_tick_at=past)
        await scheduler.resume_all()
        await _drain()

    asyncio.run(go())
    assert uploads == [("Old", ["1.jpg", "2.jpg"])]
