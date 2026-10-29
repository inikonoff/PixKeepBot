"""
Supabase storage: per-user settings, connected Google accounts (exactly one
active), upload history ("Мои загрузки") and the keep-alive ping.
Full schema and migrations: README.md ("Supabase").
"""
import asyncio
import functools
import logging

from supabase import Client, create_client

from pixkeep import crypto
from pixkeep.config import config

_client: Client | None = None


def _sb() -> Client:
    """The Supabase client, created on first use (so importing this module
    needs no network and no credentials, e.g. in tests)."""
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


def _in_thread(fn):
    """supabase-py is synchronous: every query is a blocking HTTP call. Run it
    in a worker thread so the bot's event loop never stalls on the database.
    Every public function below is therefore awaited by callers."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
    return wrapper

DEFAULT_SETTINGS = {
    "clean_metadata": True,
    "anonymize_names": False,
    "auto_date_folder": False,
    "replace_duplicates": False,
    "photo_fix": "off",        # off | auto | always   (v5.17)
    "upload_list": False,      # put a txt list of files into the folder (v5.17)
    # v5.30, migrations/002_deferred_upload.sql
    "deferred": False,         # upload on a random schedule instead of right away
    "deferred_min": 10,        # minutes between scheduled uploads: from..
    "deferred_max": 15,        # ..to
    "deferred_deadline": "14:00",   # "HH:MM" by which everything is uploaded; "" = none
}
DEFERRED_KEYS = ("deferred", "deferred_min", "deferred_max", "deferred_deadline")

# False until check_schema() confirms migration 002 was run. Without it the
# deferred columns are not selected (defaults apply) and the mode is hidden,
# so deploying before the migration doesn't break ordinary uploads.
deferred_available = False
# Same for migrations/003_parent_folder.sql (parent folder columns).
parent_available = False

PARENT_COLS = "parent_folder_id, parent_folder_name"


def _account_cols() -> str:
    return "id, email, access_token, refresh_token" + (", " + PARENT_COLS if parent_available else "")


def _settings_keys() -> list[str]:
    return [k for k in DEFAULT_SETTINGS if deferred_available or k not in DEFERRED_KEYS]


@_in_thread
def check_schema() -> list[str]:
    """Startup check; returns the problems found (empty = all good)."""
    global deferred_available, parent_available

    def probe(table: str, columns: str) -> str | None:
        try:
            _sb().table(table).select(columns).limit(1).execute()
        except Exception as e:
            return f"{table}: {str(e)[:150]}"
        return None

    deferred = [probe("user_settings", ", ".join(DEFERRED_KEYS)),
                probe("upload_queue", "id, telegram_id, chat_id, folder_name, files, attempts"),
                probe("upload_queue_state",
                      "telegram_id, chat_id, next_tick_at, flush, paused, status_message_id, summary")]
    parent = [probe("google_accounts", PARENT_COLS)]
    deferred_available = not any(deferred)
    parent_available = not any(parent)
    return [p for p in deferred + parent if p]


# --------------------------------------------------------------- settings ---
@_in_thread
def get_settings(telegram_id: int) -> dict:
    # Columns come from DEFAULT_SETTINGS on purpose — a new setting added
    # there is selected automatically and can't silently go stale.
    keys = _settings_keys()
    resp = (
        _sb().table("user_settings")
        .select(", ".join(keys))
        .eq("telegram_id", telegram_id)
        .limit(1)
        .execute()
    )
    row = resp.data[0] if resp.data else {}
    return {
        key: (row.get(key) if row.get(key) is not None else default)
        for key, default in DEFAULT_SETTINGS.items()
    }


@_in_thread
def set_setting(telegram_id: int, field: str, value) -> None:
    if field not in _settings_keys():
        raise ValueError(f"Unknown setting: {field}")
    _sb().table("user_settings").upsert({
        "telegram_id": telegram_id,
        field: value,
    }).execute()


# ------------------------------------------------------------ upload queue --
@_in_thread
def queue_add(telegram_id: int, chat_id: int, folder_name: str | None,
              files: list[dict], attempts: int = 0) -> int:
    resp = _sb().table("upload_queue").insert({
        "telegram_id": telegram_id, "chat_id": chat_id, "folder_name": folder_name,
        "files": files, "attempts": attempts,
    }).execute()
    return resp.data[0]["id"]


@_in_thread
def queue_list(telegram_id: int) -> list[dict]:
    """Waiting batches in upload order: retried ones first, then oldest first."""
    resp = (
        _sb().table("upload_queue").select("*")
        .eq("telegram_id", telegram_id)
        .order("attempts", desc=True).order("id")
        .execute()
    )
    return resp.data or []


@_in_thread
def queue_update(entry_id: int, **fields) -> None:
    _sb().table("upload_queue").update(fields).eq("id", entry_id).execute()


@_in_thread
def queue_delete(entry_id: int) -> None:
    _sb().table("upload_queue").delete().eq("id", entry_id).execute()


@_in_thread
def queue_users() -> list[int]:
    resp = _sb().table("upload_queue").select("telegram_id").execute()
    return sorted({r["telegram_id"] for r in (resp.data or [])})


@_in_thread
def get_queue_state(telegram_id: int) -> dict | None:
    resp = (
        _sb().table("upload_queue_state").select("*")
        .eq("telegram_id", telegram_id).limit(1).execute()
    )
    return resp.data[0] if resp.data else None


@_in_thread
def set_queue_state(telegram_id: int, chat_id: int, **fields) -> None:
    _sb().table("upload_queue_state").upsert(
        {"telegram_id": telegram_id, "chat_id": chat_id, **fields}).execute()


@_in_thread
def clear_queue_state(telegram_id: int) -> None:
    _sb().table("upload_queue_state").delete().eq("telegram_id", telegram_id).execute()


# ---------------------------------------------------------- Google accounts --
@_in_thread
def add_or_update_account(telegram_id: int, email: str,
                          access_token: str, refresh_token: str) -> None:
    """Connect a new Google account, or refresh tokens if this email is
    already connected for this Telegram user. Either way, this account
    becomes the active one (deactivating any others)."""
    _sb().table("google_accounts").update({"is_active": False}).eq(
        "telegram_id", telegram_id
    ).execute()
    _sb().table("google_accounts").upsert({
        "telegram_id": telegram_id,
        "email": email,
        "access_token": crypto.encrypt(access_token),
        "refresh_token": crypto.encrypt(refresh_token),
        "is_active": True,
    }, on_conflict="telegram_id,email").execute()


@_in_thread
def list_accounts(telegram_id: int) -> list[dict]:
    resp = (
        _sb().table("google_accounts")
        .select("id, email, is_active")
        .eq("telegram_id", telegram_id)
        .order("created_at")
        .execute()
    )
    return resp.data or []


@_in_thread
def get_active_account(telegram_id: int) -> dict | None:
    resp = (
        _sb().table("google_accounts")
        .select(_account_cols())
        .eq("telegram_id", telegram_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    return _decrypted(resp.data[0]) if resp.data else None


@_in_thread
def set_active_account(telegram_id: int, account_id: int) -> None:
    _sb().table("google_accounts").update({"is_active": False}).eq(
        "telegram_id", telegram_id
    ).execute()
    _sb().table("google_accounts").update({"is_active": True}).eq(
        "id", account_id
    ).eq("telegram_id", telegram_id).execute()


@_in_thread
def update_account_tokens(account_id: int, access_token: str, refresh_token: str) -> None:
    _sb().table("google_accounts").update({
        "access_token": crypto.encrypt(access_token),
        "refresh_token": crypto.encrypt(refresh_token),
    }).eq("id", account_id).execute()


@_in_thread
def set_parent_folder(telegram_id: int, account_id: int, folder_id: str | None, name: str | None) -> None:
    """Parent folder of one account (None = no parent: folders go to the Drive root)."""
    _sb().table("google_accounts").update({
        "parent_folder_id": folder_id, "parent_folder_name": name,
    }).eq("id", account_id).eq("telegram_id", telegram_id).execute()


@_in_thread
def remove_all_accounts(telegram_id: int) -> None:
    _sb().table("google_accounts").delete().eq("telegram_id", telegram_id).execute()
    _sb().table("uploads").delete().eq("telegram_id", telegram_id).execute()


@_in_thread
def get_account_by_id(telegram_id: int, account_id: int) -> dict | None:
    resp = (
        _sb().table("google_accounts")
        .select(_account_cols())
        .eq("telegram_id", telegram_id)
        .eq("id", account_id)
        .limit(1)
        .execute()
    )
    return _decrypted(resp.data[0]) if resp.data else None


def _decrypted(row: dict) -> dict:
    return {**row, "access_token": crypto.decrypt(row["access_token"]),
            "refresh_token": crypto.decrypt(row["refresh_token"])}


@_in_thread
def encrypt_existing_tokens() -> int:
    """Startup migration: encrypts tokens still stored as plaintext (rows
    written before TOKEN_KEY was set). Returns how many rows were updated."""
    if not config.TOKEN_KEY:
        return 0
    rows = _sb().table("google_accounts").select("id, access_token, refresh_token").execute().data or []
    n = 0
    for r in rows:
        if crypto.is_encrypted(r["access_token"]) and crypto.is_encrypted(r["refresh_token"]):
            continue
        _sb().table("google_accounts").update({
            "access_token": crypto.encrypt(r["access_token"]),
            "refresh_token": crypto.encrypt(r["refresh_token"]),
        }).eq("id", r["id"]).execute()
        n += 1
    return n


# ------------------------------------------------------------------ uploads --
UPLOADS_KEEP = 20  # rows kept per user; older ones are pruned on insert


@_in_thread
def save_upload(telegram_id: int, account_id: int, folder_id: str,
                folder_name: str, link: str, files: list[dict]) -> int | None:
    """files: [{"id": drive_file_id, "name": uploaded_name}, ...]
    Returns the new row's id."""
    resp = _sb().table("uploads").insert({
        "telegram_id": telegram_id,
        "account_id": account_id,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "link": link,
        "files": files,
    }).execute()
    new_id = resp.data[0]["id"] if resp.data else None
    old = (
        _sb().table("uploads").select("id")
        .eq("telegram_id", telegram_id)
        .is_("comment", "null")
        .order("created_at", desc=True)
        .range(UPLOADS_KEEP, UPLOADS_KEEP + 200)
        .execute()
    )
    ids = [r["id"] for r in (old.data or [])]
    if ids:
        _sb().table("uploads").delete().in_("id", ids).execute()
    return new_id


@_in_thread
def list_uploads(telegram_id: int, limit: int = 10) -> list[dict]:
    resp = (
        _sb().table("uploads").select("*")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


@_in_thread
def get_upload(telegram_id: int, upload_id: int) -> dict | None:
    resp = (
        _sb().table("uploads").select("*")
        .eq("telegram_id", telegram_id).eq("id", upload_id)
        .limit(1).execute()
    )
    return resp.data[0] if resp.data else None


@_in_thread
def update_upload(telegram_id: int, upload_id: int, **fields) -> None:
    _sb().table("uploads").update(fields).eq(
        "telegram_id", telegram_id).eq("id", upload_id).execute()


@_in_thread
def delete_upload(telegram_id: int, upload_id: int) -> None:
    _sb().table("uploads").delete().eq(
        "telegram_id", telegram_id).eq("id", upload_id).execute()


@_in_thread
def delete_uploads_by_folder(telegram_id: int, folder_id: str) -> None:
    _sb().table("uploads").delete().eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()


# -------------------------------------------------------------- keep-alive ---
# Free Supabase projects are paused after ~7 days without activity. A cheap
# request every 10 minutes keeps the project "active".
KEEPALIVE_INTERVAL_SECONDS = 10 * 60


def _ping_sync() -> None:
    _sb().table("user_settings").select("telegram_id").limit(1).execute()


async def keep_alive_loop(interval: int = KEEPALIVE_INTERVAL_SECONDS) -> None:
    """Background task: ping Supabase right away, then every `interval` seconds.
    Errors are logged and never stop the loop; cancellation exits cleanly."""
    while True:
        try:
            await asyncio.to_thread(_ping_sync)
            logging.debug("Supabase keep-alive: ok")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.warning(f"Supabase keep-alive failed: {e}")
        await asyncio.sleep(interval)


@_in_thread
def list_uploads_by_folder(telegram_id: int, folder_id: str) -> list[dict]:
    """All history rows for one Drive folder, oldest first."""
    resp = (
        _sb().table("uploads").select("*")
        .eq("telegram_id", telegram_id).eq("folder_id", folder_id)
        .order("created_at")
        .execute()
    )
    return resp.data or []


@_in_thread
def set_folder_comment_file(telegram_id: int, folder_id: str, file_id: str | None) -> None:
    _sb().table("uploads").update({"comment_file_id": file_id}).eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()


@_in_thread
def rename_folder_rows(telegram_id: int, folder_id: str, new_name: str) -> None:
    _sb().table("uploads").update({"folder_name": new_name}).eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()
