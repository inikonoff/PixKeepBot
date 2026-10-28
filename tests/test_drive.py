"""Drive code against a fake Drive server (aiohttp test server, no Google)."""
import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pixkeep import db, delivery, drive, drive_utils


class FakeDrive:
    def __init__(self, token="good", fail_first=()):
        self.token = token
        self.fail_first = set(fail_first)   # paths that answer 503 once
        self.files = {}                      # id -> {name, parents, md5, mime}
        self.public = set()
        self.calls = []
        self._n = 0

    def _new_id(self):
        self._n += 1
        return f"id{self._n}"

    @web.middleware
    async def mw(self, request, handler):
        self.calls.append((request.method, request.path))
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return web.Response(status=401)
        if request.path in self.fail_first:
            self.fail_first.discard(request.path)
            return web.Response(status=503)
        return await handler(request)

    async def list_files(self, request):
        q = request.query["q"]
        out = []
        for fid, f in self.files.items():
            if "name='" in q and f"name='{f['name']}'" not in q:
                continue
            if "in parents" in q and q.split("'")[1] not in f["parents"]:
                continue
            if "mimeType='application/vnd.google-apps.folder'" in q and f["mime"] != "folder":
                continue
            out.append({"id": fid, "name": f["name"], "md5Checksum": f.get("md5")})
        return web.json_response({"files": out})

    async def create(self, request):
        meta = await request.json()
        fid = self._new_id()
        self.files[fid] = {"name": meta["name"], "parents": [], "mime": "folder"}
        return web.json_response({"id": fid})

    async def upload(self, request):
        import hashlib
        reader = await request.multipart()
        meta = json.loads(await (await reader.next()).text())
        body = await (await reader.next()).read()
        fid = self._new_id()
        self.files[fid] = {"name": meta["name"], "parents": meta["parents"], "mime": "file",
                           "md5": hashlib.md5(body).hexdigest()}
        return web.json_response({"id": fid})

    async def permissions(self, request):
        self.public.add(request.match_info["fid"])
        return web.json_response({"id": "p1"})

    def app(self):
        app = web.Application(middlewares=[self.mw])
        app.router.add_get("/drive/v3/files", self.list_files)
        app.router.add_post("/drive/v3/files", self.create)
        app.router.add_post("/upload/drive/v3/files", self.upload)
        app.router.add_post("/drive/v3/files/{fid}/permissions", self.permissions)
        return app


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(drive_utils, "_delay", lambda attempt, retry_after: 0)


async def _with_server(fake, monkeypatch, coro_fn):
    server = TestServer(fake.app())
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    monkeypatch.setattr(drive_utils, "API", base + "/drive/v3")
    monkeypatch.setattr(drive_utils, "UPLOAD_API", base + "/upload/drive/v3")
    try:
        return await coro_fn()
    finally:
        await server.close()


def _account(token):
    return {"id": 1, "email": "a@b.c", "access_token": token, "refresh_token": "r"}


def _items(tmp_path, *contents):
    items = []
    for i, data in enumerate(contents):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(data)
        items.append({"message_id": i, "path": str(p), "orig_name": p.name,
                      "upload_path": str(p), "upload_name": p.name})
    return items


def test_upload_all_refreshes_expired_token_once(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive(token="good")
    refreshes, stored = [], []

    async def fake_refresh(refresh_token):
        refreshes.append(refresh_token)
        return {"access_token": "good", "refresh_token": refresh_token}

    async def fake_store(account_id, access, refresh):
        stored.append((account_id, access))

    monkeypatch.setattr(drive, "refresh_access_token", fake_refresh)
    monkeypatch.setattr(db, "update_account_tokens", fake_store)
    items = _items(tmp_path, b"one", b"two", b"one")   # third is a duplicate of the first
    account = _account("expired")

    results, link, folder_id = asyncio.run(_with_server(
        fake, monkeypatch, lambda: delivery.upload_all(account, items, " Trip ")))

    assert refreshes == ["r"] and stored == [(1, "good")] and account["access_token"] == "good"
    assert [r["success"] for r in results] == [True, True, True]
    assert results[2]["duplicate_of"] == "f0.jpg"
    uploaded = sorted(f["name"] for f in fake.files.values() if f["mime"] == "file")
    assert uploaded == ["f0.jpg", "f1.jpg"]
    assert fake.files[folder_id]["name"] == "Trip"
    assert folder_id in fake.public and link.endswith(folder_id)


def test_existing_folder_and_content_are_reused(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive()
    items = _items(tmp_path, b"same")

    async def twice():
        first = await delivery.upload_all(_account("good"), items, "Trip")
        second = await delivery.upload_all(_account("good"), items, "Trip")
        return first, second

    (r1, _, f1), (r2, _, f2) = asyncio.run(_with_server(fake, monkeypatch, twice))
    assert f1 == f2
    assert r1[0].get("duplicate_of") is None and r2[0]["duplicate_of"] == "f0.jpg"


def test_transient_503_is_retried(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive(fail_first={"/drive/v3/files", "/upload/drive/v3/files"})
    items = _items(tmp_path, b"x")
    results, _, _ = asyncio.run(_with_server(
        fake, monkeypatch, lambda: delivery.upload_all(_account("good"), items, "T")))
    assert results[0]["success"]


def test_upload_failure_is_reported_not_raised(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive()
    items = _items(tmp_path, b"x")

    async def broken_upload(*a, **kw):
        raise RuntimeError("Google API 500: boom")

    monkeypatch.setattr(delivery, "upload_file", broken_upload)
    results, _, _ = asyncio.run(_with_server(
        fake, monkeypatch, lambda: delivery.upload_all(_account("good"), items, "T")))
    assert not results[0]["success"] and "boom" in results[0]["error"]
