"""Scheduled mode in intake: batches go to the queue without downloading."""
import asyncio
from datetime import datetime, timezone

import pytest

from pixkeep import db
from pixkeep.handlers import intake
from pixkeep.media import MediaRef
from pixkeep.runtime import Flow


def _ref(i, size=1000):
    return MediaRef(kind="photo", file_id=f"f{i}", file_unique_id=f"u{i}", name=f"{i}.jpg", size=size,
                    message_id=i, date=datetime(2026, 10, 29, tzinfo=timezone.utc))


class State:
    def __init__(self):
        self.state, self.data = None, {}

    async def get_state(self):
        return self.state

    async def set_state(self, s):
        self.state = s.state if hasattr(s, "state") else s

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.state, self.data = None, {}


@pytest.fixture
def calls(monkeypatch):
    log = {"enqueued": [], "sent": [], "prepared": 0}

    async def enqueue(tid, chat_id, refs, folder):
        log["enqueued"].append(([r.name for r in refs], folder))

    async def send_message(chat_id, text, **kw):
        log["sent"].append(text)

    async def prepare(*a, **k):
        log["prepared"] += 1

    async def no_account(tid):
        return None

    monkeypatch.setattr(intake.scheduler, "enqueue", enqueue)
    monkeypatch.setattr(intake.bot, "send_message", send_message, raising=False)
    monkeypatch.setattr(intake, "prepare_batch", prepare)
    monkeypatch.setattr(db, "get_active_account", no_account)
    return log


SETTINGS = {**db.DEFAULT_SETTINGS, "deferred": True}


def test_auto_date_batch_is_queued_without_download(calls):
    big = _ref(2, size=30 * 1024 * 1024)
    asyncio.run(intake._process_deferred([_ref(1), big], {**SETTINGS, "auto_date_folder": True},
                                         State(), 1, 1))
    assert calls["enqueued"] == [(["1.jpg"], None)] and calls["prepared"] == 0
    assert any("2.jpg" in t and "20 МБ" in t for t in calls["sent"])


def test_folder_question_then_enqueue(calls):
    state = State()

    async def go():
        await intake._process_deferred([_ref(1), _ref(2)], SETTINGS, state, 1, 1)
        assert state.state == Flow.waiting_folder_name.state
        assert "В очередь: 2 файла" in calls["sent"][-1]
        await intake._begin_upload(1, 1, state, await state.get_data(), "Trip")

    asyncio.run(go())
    assert calls["enqueued"] == [(["1.jpg", "2.jpg"], "Trip")] and state.state is None


def test_answer_after_temp_files_were_swept(calls, tmp_path):
    state = State()
    data = {"items": [{"message_id": 1}], "work_dir": str(tmp_path / "gone")}
    asyncio.run(intake._begin_upload(1, 1, state, data, "Trip"))
    assert "слишком долго" in calls["sent"][-1] and state.state is None
