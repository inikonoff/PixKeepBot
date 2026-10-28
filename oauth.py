"""
oauth.py
v4.2 - per-user Google OAuth (authorization-code flow, plain REST via aiohttp)

Changelog:
- v4.2: pending_states entries expire after 10 minutes (and are purged on
        every new auth link); refresh failure with invalid_grant raises
        TokenRevokedError so the bot can ask the user to reconnect.
- v4.1: added get_user_email() — needed to label multiple connected Google
        accounts distinctly in /accounts.

Each user authorizes their own Google Drive on Google's own consent page.
The bot only receives an authorization code -> tokens; it never sees the
user's Google password.
"""
import secrets
import time
from urllib.parse import urlencode

import aiohttp

from config import config

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

STATE_TTL = 600  # seconds the user has to finish the Google consent screen

# short-lived map: state nonce -> (telegram_id, created_at)
pending_states: dict[str, tuple[int, float]] = {}


class TokenRevokedError(Exception):
    """Refresh token is no longer valid (access revoked / expired)."""


def _purge_states() -> None:
    now = time.monotonic()
    for k in [k for k, (_, t) in pending_states.items() if now - t > STATE_TTL]:
        pending_states.pop(k, None)


def build_auth_url(telegram_id: int) -> str:
    _purge_states()
    state = secrets.token_urlsafe(24)
    pending_states[state] = (telegram_id, time.monotonic())
    params = {
        "client_id": config.GOOGLE_CLIENT_ID,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": config.GOOGLE_SCOPE,
        "access_type": "offline",   # required to receive a refresh token
        "prompt": "consent",        # force refresh token even on re-auth
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code(state: str, code: str) -> tuple[int, str, str]:
    """Return (telegram_id, access_token, refresh_token) for a completed consent."""
    _purge_states()
    entry = pending_states.pop(state, None)
    if entry is None:
        raise ValueError("Ссылка устарела или уже использована — запроси новую через /start")
    telegram_id = entry[0]

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "redirect_uri": config.OAUTH_REDIRECT_URI,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data) as resp:
            payload = await resp.json()
            if resp.status != 200 or "access_token" not in payload:
                raise ValueError(f"Token exchange failed: {payload}")

    if "refresh_token" not in payload:
        raise ValueError(
            "No refresh token returned by Google (usually means the user "
            "already authorized before without revoking access — ask them "
            "to revoke access at myaccount.google.com/permissions and retry)"
        )
    return telegram_id, payload["access_token"], payload["refresh_token"]


async def get_user_email(access_token: str) -> str:
    """Used to label multiple connected accounts distinctly in /accounts."""
    async with aiohttp.ClientSession() as session:
        async with session.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"userinfo failed: {data}")
            return data.get("email", "unknown@account")


async def refresh_access_token(refresh_token: str) -> dict:
    """Return {access_token, refresh_token} using a stored refresh token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(TOKEN_URL, data=data) as resp:
            payload = await resp.json()
            if resp.status != 200 or "access_token" not in payload:
                if payload.get("error") == "invalid_grant":
                    raise TokenRevokedError("Google access was revoked or expired")
                raise ValueError(f"Token refresh failed: {payload}")
    return {
        "access_token": payload["access_token"],
        # Google normally does NOT return a new refresh_token on refresh —
        # keep reusing the one we already have unless a new one is given.
        "refresh_token": payload.get("refresh_token", refresh_token),
    }
