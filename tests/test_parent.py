"""Parent folder menu in /settings (Drive and DB calls replaced by fakes)."""
import asyncio
from types import SimpleNamespace as NS

import pytest

from pixkeep import db, texts
from pixkeep.handlers import settings
from pixkeep.runtime import Flow


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


class CQ:
    def __init__(self, data):
        self.data, self.from_user = data, NS(id=1)
        self.calls = []
        self.message = NS(edit_text=self._rec("edit_text"), answer=self._rec("msg_answer"),
                          edit_reply_markup=self._rec("edit_markup"))

    def _rec(self, name):
        async def f(*a, **k):
            self.calls.append((name, a, k))
        return f

    async def answer(self, *a, **k):
        self.calls.append(("answer", a, k))

    def called(self, name):
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def world(monkeypatch):
    w = NS(account={"id": 7, "email": "me@x.y", "parent_folder_id": None, "parent_folder_name": None},
           saved=[], folders=[{"id": "F1", "name": "Photos"}, {"id": "F2", "name": "Docs"}])

    async def get_active_account(uid):
        return dict(w.account)

    async def set_parent_folder(uid, account_id, folder_id, name):
        w.saved.append((uid, account_id, folder_id, name))
        w.account.update(parent_folder_id=folder_id, parent_folder_name=name)

    async def get_settings(uid):
        return dict(db.DEFAULT_SETTINGS)

    async def with_drive(account, fn):
        return await fn(None, None)

    async def list_folders(s, t, limit, parent_id=None, root_only=False):
        assert root_only
        return w.folders

    async def get_file_meta(s, t, fid):
        return {"name": next(f["name"] for f in w.folders if f["id"] == fid), "size": None}

    async def ensure_folder(s, t, name, parent_id=None):
        assert parent_id == "root"
        return "NEW1"

    monkeypatch.setattr(db, "parent_available", True)
    for name, fn in dict(get_active_account=get_active_account, set_parent_folder=set_parent_folder,
                         get_settings=get_settings).items():
        monkeypatch.setattr(db, name, fn)
    for name, fn in dict(with_drive=with_drive, list_folders=list_folders, get_file_meta=get_file_meta,
                         ensure_folder=ensure_folder).items():
        monkeypatch.setattr(settings, name, fn)
    return w


def _buttons(kb):
    return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]


def test_menu_lists_bot_folders_and_actions(world):
    cq = CQ("par")
    asyncio.run(settings.on_parent(cq, State()))
    _, (text,), kw = cq.called("edit_text")[0]
    assert "Родительская папка" in text and "корень Диска" in text
    buttons = _buttons(kw["reply_markup"])
    assert ("📁 Photos", "par:s:F1") in buttons and ("➕ Создать новую папку…", "par:new") in buttons
    assert not any(cb == "par:none" for _, cb in buttons)          # nothing to clear yet


def test_select_then_clear(world):
    cq = CQ("par:s:F1")
    asyncio.run(settings.on_parent(cq, State()))
    assert world.saved == [(1, 7, "F1", "Photos")]
    _, (text,), kw = cq.called("edit_text")[0]
    assert "<b>Photos</b>" in text
    buttons = _buttons(kw["reply_markup"])
    assert ("✓ Photos", "par:s:F1") in buttons and any(cb == "par:none" for _, cb in buttons)

    cq = CQ("par:none")
    asyncio.run(settings.on_parent(cq, State()))
    assert world.saved[-1] == (1, 7, None, None)


def test_create_new_parent_by_name(world):
    state = State()
    asyncio.run(settings.on_parent(CQ("par:new"), state))
    assert state.state == Flow.waiting_parent_name.state

    answers = []

    async def answer(text, **kw):
        answers.append(text)

    msg = NS(from_user=NS(id=1), text="  Family  ", answer=answer)
    asyncio.run(settings.on_parent_name(msg, state))
    assert world.saved == [(1, 7, "NEW1", "Family")] and state.state is None
    assert "Family" in answers[-1] and "внутри" in answers[-1]


def test_bad_name_is_refused(world):
    answers = []

    async def answer(text, **kw):
        answers.append(text)

    asyncio.run(settings.on_parent_name(NS(from_user=NS(id=1), text="x" * 101, answer=answer), State()))
    assert world.saved == [] and "от 1 до 100" in answers[0]


def test_hidden_without_migration(world, monkeypatch):
    monkeypatch.setattr(db, "parent_available", False)
    cq = CQ("par")
    asyncio.run(settings.on_parent(cq, State()))
    assert cq.called("answer")[0][2]["show_alert"] and not cq.called("edit_text")
    kb = settings._settings_keyboard(db.DEFAULT_SETTINGS, world.account)
    assert not any(cb == "par" for _, cb in _buttons(kb))


def test_settings_button_shows_current_parent(world):
    kb = settings._settings_keyboard(db.DEFAULT_SETTINGS, {**world.account, "parent_folder_name": "Photos"})
    assert ("📂 Родительская папка: Photos", "par") in _buttons(kb)
    kb = settings._settings_keyboard(db.DEFAULT_SETTINGS, world.account)
    assert ("📂 Родительская папка: корень Диска", "par") in _buttons(kb)


def test_report_and_question_mention_parent():
    results = [{"upload_name": "a.jpg", "orig_name": "a.jpg", "success": True}]
    text = texts.upload_report(results, "28.10.26", "https://d/f/A", None, False, "e", None, "Photos")
    assert "Photos / <b>28.10.26</b>" in text
    assert "внутри «Photos»" in texts.ask_folder_line("Photos")
    assert "внутри" not in texts.ask_folder_line(None)
