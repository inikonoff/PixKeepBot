"""
drive_utils.py
v4.1 - Google Drive v3 REST API helpers (async, aiohttp, no Google client libs)

Mirrors the shape of the previous Yandex disk_utils.py so main.py's overall
flow barely has to change: ensure_folder / upload_file / publish_and_get_url.

API docs: https://developers.google.com/workspace/drive/api/reference/rest/v3
Auth header format: `Authorization: Bearer <token>`.

Changelog:
- v4.3: optional content_type for upload_file / replace_file_content (txt files).
- v4.2: upload_file now returns the new file's id; added trash_file,
        folder_is_empty, revoke_public_access (for "Мои загрузки").
- v4.1: added find_file_in_folder + replace_file_content, for the
        replace_duplicates setting — lets main.py overwrite an existing
        same-named file in place instead of always creating a new one via
        upload_file (Drive's default: filenames aren't unique, so uploading
        the same file twice used to just leave two copies behind).
"""
import json

import aiohttp

API = "https://www.googleapis.com/drive/v3"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"


class GoogleAuthError(Exception):
    """Raised on HTTP 401 so the caller can refresh the token and retry."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _check(resp: aiohttp.ClientResponse, ok=(200, 201)):
    if resp.status == 401:
        raise GoogleAuthError("Google token unauthorized")
    if resp.status in ok:
        return
    text = await resp.text()
    raise RuntimeError(f"Google API {resp.status}: {text}")


async def ensure_folder(session: aiohttp.ClientSession, token: str,
                        folder_name: str) -> str:
    """Find or create a folder by name in the app's visible root, return its id.

    Note: with drive.file scope, the app can only see files/folders IT
    created (or that the user explicitly opened with it) — so this search
    will only find a folder of this name if OUR bot made it before.
    """
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"mimeType='application/vnd.google-apps.folder' and name='{safe_name}' "
        "and trashed=false"
    )
    async with session.get(
        f"{API}/files", params={"q": query, "fields": "files(id,name)"},
        headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200,))
        data = await resp.json()

    files = data.get("files", [])
    if files:
        return files[0]["id"]

    metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    async with session.post(
        f"{API}/files", json=metadata, headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200, 201))
        created = await resp.json()
        return created["id"]


async def upload_file(session: aiohttp.ClientSession, token: str,
                      local_path: str, folder_id: str, name: str,
                      content_type: str = "application/octet-stream") -> str:
    """Multipart upload of a single (modest-size) file into folder_id.
    Always creates a NEW file, even if one with this name already exists
    in folder_id — see find_file_in_folder/replace_file_content below for
    the "don't duplicate" alternative (the replace_duplicates setting)."""
    metadata = {"name": name, "parents": [folder_id]}
    with open(local_path, "rb") as f:
        file_bytes = f.read()

    form = aiohttp.FormData()
    form.add_field(
        "metadata", json.dumps(metadata),
        content_type="application/json; charset=UTF-8",
    )
    form.add_field("file", file_bytes, content_type=content_type)

    async with session.post(
        f"{UPLOAD_API}/files", params={"uploadType": "multipart", "fields": "id"},
        data=form, headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200, 201))
        return (await resp.json())["id"]


async def find_file_in_folder(session: aiohttp.ClientSession, token: str,
                              folder_id: str, name: str) -> str | None:
    """Looks for a non-trashed file named exactly `name` directly inside
    folder_id. Returns its file id, or None if there isn't one.

    Used by the replace_duplicates setting. Same drive.file-scope caveat as
    ensure_folder: this can only see files the app itself created — which is
    exactly the set we'd want to compare against anyway, since "the same
    file sent again" only makes sense relative to what this bot put there.
    """
    safe_name = name.replace("'", "\\'")
    query = f"'{folder_id}' in parents and name='{safe_name}' and trashed=false"
    async with session.get(
        f"{API}/files", params={"q": query, "fields": "files(id)"},
        headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200,))
        data = await resp.json()
    files = data.get("files", [])
    return files[0]["id"] if files else None


async def replace_file_content(session: aiohttp.ClientSession, token: str,
                               local_path: str, file_id: str,
                               content_type: str = "application/octet-stream") -> None:
    """Overwrites an existing file's bytes in place — same file id, same
    share link, no duplicate. Used instead of upload_file when
    find_file_in_folder found a same-named file already there."""
    with open(local_path, "rb") as f:
        file_bytes = f.read()
    async with session.patch(
        f"{UPLOAD_API}/files/{file_id}", params={"uploadType": "media"},
        data=file_bytes, headers={**_headers(token), "Content-Type": content_type},
    ) as resp:
        await _check(resp, ok=(200,))


async def publish_and_get_url(session: aiohttp.ClientSession, token: str,
                              folder_id: str) -> str:
    """Grant 'anyone with the link' reader access to the folder, return its URL."""
    async with session.post(
        f"{API}/files/{folder_id}/permissions",
        json={"type": "anyone", "role": "reader"},
        headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200, 201))

    return f"https://drive.google.com/drive/folders/{folder_id}"


async def trash_file(session: aiohttp.ClientSession, token: str,
                     file_id: str) -> None:
    """Moves a file/folder to the Drive trash (recoverable for 30 days).
    Already gone (404) counts as success."""
    async with session.patch(
        f"{API}/files/{file_id}", json={"trashed": True}, headers=_headers(token),
    ) as resp:
        if resp.status == 404:
            return
        await _check(resp, ok=(200,))


async def folder_is_empty(session: aiohttp.ClientSession, token: str,
                          folder_id: str) -> bool:
    query = f"'{folder_id}' in parents and trashed=false"
    async with session.get(
        f"{API}/files", params={"q": query, "fields": "files(id)", "pageSize": 1},
        headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200,))
        data = await resp.json()
    return not data.get("files")


async def revoke_public_access(session: aiohttp.ClientSession, token: str,
                               folder_id: str) -> None:
    """Removes the 'anyone with the link' permission from the folder."""
    async with session.get(
        f"{API}/files/{folder_id}/permissions",
        params={"fields": "permissions(id,type)"}, headers=_headers(token),
    ) as resp:
        await _check(resp, ok=(200,))
        data = await resp.json()
    for p in data.get("permissions", []):
        if p.get("type") == "anyone":
            async with session.delete(
                f"{API}/files/{folder_id}/permissions/{p['id']}",
                headers=_headers(token),
            ) as resp:
                if resp.status != 404:
                    await _check(resp, ok=(200, 204))
