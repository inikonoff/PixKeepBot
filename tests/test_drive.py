"""Drive code against a fake Drive server (aiohttp test server, no Google)."""
import asyncio
import json
import re

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from pixkeep import db, delivery, drive, drive_utils
from pixkeep.drive_utils import ParentFolderMissing


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
        parent = re.search(r"'([^']+)' in parents", q)
        out = []
        for fid, f in self.files.items():
            if "name='" in q and f"name='{f['name']}'" not in q:
                continue
            if parent:
                # "root" = the top level: folders created without a parent
                if (f["parents"] != [] if parent.group(1) == "root" else parent.group(1) not in f["parents"]):
                    continue
            if "mimeType='application/vnd.google-apps.folder'" in q and f["mime"] != "folder":
                continue
            out.append({"id": fid, "name": f["name"], "md5Checksum": f.get("md5")})
        return web.json_response({"files": out})

    async def create(self, request):
        meta = await request.json()
        fid = self._new_id()
        parents = [p for p in meta.get("parents", []) if p != "root"]
        self.files[fid] = {"name": meta["name"], "parents": parents, "mime": "folder"}
        return web.json_response({"id": fid})

    async def get_file(self, request):
        f = self.files.get(request.match_info["fid"])
        if f is None:
            return web.Response(status=404)
        return web.json_response({"id": request.match_info["fid"], "trashed": f.get("trashed", False)})

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
        app.router.add_get("/drive/v3/files/{fid}", self.get_file)
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


def test_folders_go_inside_the_parent_folder(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive()
    items = _items(tmp_path, b"x")

    async def go():
        async with drive.DriveClient(_account("good")) as d:
            parent_id = await d.call(lambda s, t: drive_utils.ensure_folder(s, t, "Photos", "root"))
            decoy = await d.call(lambda s, t: drive_utils.ensure_folder(s, t, "28.10.26", "root"))
        account = {**_account("good"), "parent_folder_id": parent_id, "parent_folder_name": "Photos"}
        first = await delivery.upload_all(account, items, "28.10.26")
        second = await delivery.upload_all(account, items, "28.10.26")
        async with drive.DriveClient(_account("good")) as d:
            tops = await d.call(lambda s, t: drive_utils.list_folders(s, t, 10, root_only=True))
            inside = await d.call(lambda s, t: drive_utils.list_recent_folders(s, t, 6, parent_id))
        return parent_id, decoy, first, second, tops, inside

    parent_id, decoy, (_, _, f1), (_, _, f2), tops, inside = asyncio.run(_with_server(fake, monkeypatch, go))
    assert f1 == f2 and f1 != decoy                      # same folder reused, root decoy ignored
    assert fake.files[f1]["parents"] == [parent_id]
    assert sorted(f["name"] for f in tops) == ["28.10.26", "Photos"]   # nested folder isn't a candidate
    assert inside == ["28.10.26"]


def test_missing_parent_folder_is_reported(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive()
    items = _items(tmp_path, b"x")
    account = {**_account("good"), "parent_folder_id": "gone", "parent_folder_name": "Old"}
    with pytest.raises(ParentFolderMissing, match="Old"):
        asyncio.run(_with_server(fake, monkeypatch, lambda: delivery.upload_all(account, items, "T")))
    assert not [f for f in fake.files.values() if f["mime"] == "file"]


def test_trashed_parent_folder_is_reported(tmp_path, monkeypatch, no_backoff):
    fake = FakeDrive()
    fake.files["p1"] = {"name": "P", "parents": [], "mime": "folder", "trashed": True}
    account = {**_account("good"), "parent_folder_id": "p1", "parent_folder_name": "P"}
    with pytest.raises(ParentFolderMissing):
        asyncio.run(_with_server(fake, monkeypatch, lambda: delivery.upload_all(
            account, _items(tmp_path, b"x"), "T")))
