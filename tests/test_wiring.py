import ast
import asyncio
import pathlib
from datetime import datetime, timezone

import pytest
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.methods import TelegramMethod
from aiogram.types import Chat, Message, Update, User

import main
from pixkeep import db

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_FUNCS = {name for name in dir(db) if asyncio.iscoroutinefunction(getattr(db, name))
            and not name.startswith("_") and name != "keep_alive_loop"}


def test_every_db_call_is_awaited():
    """db functions are async; a forgotten `await` would silently pass a
    coroutine object around instead of data."""
    problems = []
    for path in [ROOT / "main.py", *ROOT.joinpath("pixkeep").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "db"
                    and node.func.attr in DB_FUNCS
                    and not isinstance(parents.get(node), ast.Await)):
                problems.append(f"{path.name}:{node.lineno} db.{node.func.attr}")
    assert not problems, problems


@pytest.fixture(scope="module")
def dp():
    return main.build_dispatcher()   # routers can be attached only once per process


def test_dispatcher_has_all_routers(dp):
    assert [r.name for r in dp.sub_routers] == ["start", "settings", "history", "queue", "intake"]
    assert sum(len(r.message.handlers) + len(r.callback_query.handlers) for r in dp.sub_routers) == 36


class _NoNetworkBot:
    """Stands in for Bot: any API call made while handling fails the test."""
    id = 123456

    async def __call__(self, method: TelegramMethod, request_timeout=None):
        raise AssertionError(f"unexpected Telegram call: {type(method).__name__}")


def _update(user_id: int, text: str) -> Update:
    user = User(id=user_id, is_bot=False, first_name="X")
    msg = Message(message_id=1, date=datetime.now(timezone.utc),
                  chat=Chat(id=user_id, type="private"), from_user=user, text=text)
    return Update(update_id=1, message=msg)


def test_non_admin_is_ignored_silently(dp):
    result = asyncio.run(dp.feed_update(_NoNetworkBot(), _update(999, "/version")))
    assert result is UNHANDLED


def test_admin_reaches_handlers(dp):
    with pytest.raises(AssertionError, match="SendMessage"):
        asyncio.run(dp.feed_update(_NoNetworkBot(), _update(1, "/version")))
