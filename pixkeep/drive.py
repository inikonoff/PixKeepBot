"""
DriveClient: one Google account's Drive session with token refresh in one
place. drive_utils.py has the raw REST calls; everything else goes through
this module.
"""
import asyncio
import logging
import time

import aiohttp

from pixkeep import db
from pixkeep.drive_utils import GoogleAuthError, get_quota, new_session
from pixkeep.oauth import refresh_access_token

TXT_MIME = "text/plain; charset=utf-8"


class DriveClient:
    """`async with DriveClient(account) as drive: await drive.call(fn)` runs
    `await fn(session, token)`. On a 401 the access token is refreshed (and
    stored) and the call is retried once. Concurrent calls that hit the same
    expired token share a single refresh: the lock plus the "is the token I
    used still current?" check mean only the first one actually asks Google.

    `account` is the dict from db (id, access_token, refresh_token); it is
    updated in place with the new token so callers keep a fresh copy.
    Raises oauth.TokenRevokedError if Google revoked access."""

    def __init__(self, account: dict):
        self.account = account
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "DriveClient":
        self._session = new_session()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._session.close()

    async def _refresh(self, stale_token: str) -> None:
        async with self._lock:
            if self.account["access_token"] != stale_token:
                return  # another call already refreshed it
            new = await refresh_access_token(self.account["refresh_token"])
            await db.update_account_tokens(
                self.account["id"], new["access_token"], new["refresh_token"])
            self.account["access_token"] = new["access_token"]
            self.account["refresh_token"] = new["refresh_token"]

    async def call(self, fn):
        token = self.account["access_token"]
        try:
            return await fn(self._session, token)
        except GoogleAuthError:
            await self._refresh(token)
            return await fn(self._session, self.account["access_token"])


async def with_drive(account: dict, fn):
    """One-off call: opens a session, runs fn(session, token), closes it."""
    async with DriveClient(account) as drive:
        return await drive.call(fn)


# --------------------------------------------------------------- quota ------
QUOTA_TTL = 60.0
_quota_cache: dict[int, tuple[float, int, int | None]] = {}


async def account_quota(account: dict, fresh: bool = False) -> tuple[int, int | None] | None:
    """(used, limit) of an account's Google storage, or None if it couldn't be
    fetched (the feature is informational — never blocks anything)."""
    now = time.monotonic()
    hit = _quota_cache.get(account["id"])
    if hit and not fresh and now - hit[0] < QUOTA_TTL:
        return hit[1], hit[2]
    try:
        used, limit = await asyncio.wait_for(with_drive(account, get_quota), 8)
    except Exception as e:
        logging.warning(f"quota fetch failed for account {account['id']}: {e}")
        return None
    _quota_cache[account["id"]] = (now, used, limit)
    return used, limit
