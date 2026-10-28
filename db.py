"""
Supabase storage: per-user settings, connected Google accounts (exactly one
active), upload history ("Мои загрузки") and the keep-alive ping.
Full schema and migrations: README.md ("Supabase").
"""
import asyncio
import logging

from supabase import create_client

from config import config

_supabase = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

DEFAULT_SETTINGS = {
    "clean_metadata": True,
    "anonymize_names": False,
    "auto_date_folder": False,
    "replace_duplicates": False,
    "photo_fix": "off",        # off | auto | always   (v5.17)
    "upload_list": False,      # put a txt list of files into the folder (v5.17)
}


# --------------------------------------------------------------- settings ---
def get_settings(telegram_id: int) -> dict:
    # Select columns built from DEFAULT_SETTINGS.keys() on purpose — adding a
    # new setting there automatically includes it here too, so this can't
    # silently go stale again the way it did with auto_date_folder.
    columns = ", ".join(DEFAULT_SETTINGS.keys())
    resp = (
        _supabase.table("user_settings")
        .select(columns)
        .eq("telegram_id", telegram_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return dict(DEFAULT_SETTINGS)
    row = resp.data[0]
    return {
        key: (row.get(key) if row.get(key) is not None else default)
        for key, default in DEFAULT_SETTINGS.items()
    }


def set_setting(telegram_id: int, field: str, value) -> None:
    if field not in DEFAULT_SETTINGS:
        raise ValueError(f"Unknown setting: {field}")
    _supabase.table("user_settings").upsert({
        "telegram_id": telegram_id,
        field: value,
    }).execute()


# ---------------------------------------------------------- Google accounts --
def add_or_update_account(telegram_id: int, email: str,
                          access_token: str, refresh_token: str) -> None:
    """Connect a new Google account, or refresh tokens if this email is
    already connected for this Telegram user. Either way, this account
    becomes the active one (deactivating any others)."""
    _supabase.table("google_accounts").update({"is_active": False}).eq(
        "telegram_id", telegram_id
    ).execute()
    _supabase.table("google_accounts").upsert({
        "telegram_id": telegram_id,
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "is_active": True,
    }, on_conflict="telegram_id,email").execute()


def list_accounts(telegram_id: int) -> list[dict]:
    resp = (
        _supabase.table("google_accounts")
        .select("id, email, is_active")
        .eq("telegram_id", telegram_id)
        .order("created_at")
        .execute()
    )
    return resp.data or []


def get_active_account(telegram_id: int) -> dict | None:
    resp = (
        _supabase.table("google_accounts")
        .select("id, email, access_token, refresh_token")
        .eq("telegram_id", telegram_id)
        .eq("is_active", True)
        .limit(1)
        .execute()
    )
    if resp.data:
        return resp.data[0]
    return None


def set_active_account(telegram_id: int, account_id: int) -> None:
    _supabase.table("google_accounts").update({"is_active": False}).eq(
        "telegram_id", telegram_id
    ).execute()
    _supabase.table("google_accounts").update({"is_active": True}).eq(
        "id", account_id
    ).eq("telegram_id", telegram_id).execute()


def update_account_tokens(account_id: int, access_token: str, refresh_token: str) -> None:
    _supabase.table("google_accounts").update({
        "access_token": access_token,
        "refresh_token": refresh_token,
    }).eq("id", account_id).execute()


def remove_all_accounts(telegram_id: int) -> None:
    _supabase.table("google_accounts").delete().eq("telegram_id", telegram_id).execute()
    _supabase.table("uploads").delete().eq("telegram_id", telegram_id).execute()


def get_account_by_id(telegram_id: int, account_id: int) -> dict | None:
    resp = (
        _supabase.table("google_accounts")
        .select("id, email, access_token, refresh_token")
        .eq("telegram_id", telegram_id)
        .eq("id", account_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


# ------------------------------------------------------------------ uploads --
UPLOADS_KEEP = 20  # rows kept per user; older ones are pruned on insert


def save_upload(telegram_id: int, account_id: int, folder_id: str,
                folder_name: str, link: str, files: list[dict]) -> int | None:
    """files: [{"id": drive_file_id, "name": uploaded_name}, ...]
    Returns the new row's id."""
    resp = _supabase.table("uploads").insert({
        "telegram_id": telegram_id,
        "account_id": account_id,
        "folder_id": folder_id,
        "folder_name": folder_name,
        "link": link,
        "files": files,
    }).execute()
    new_id = resp.data[0]["id"] if resp.data else None
    old = (
        _supabase.table("uploads").select("id")
        .eq("telegram_id", telegram_id)
        .is_("comment", "null")
        .order("created_at", desc=True)
        .range(UPLOADS_KEEP, UPLOADS_KEEP + 200)
        .execute()
    )
    ids = [r["id"] for r in (old.data or [])]
    if ids:
        _supabase.table("uploads").delete().in_("id", ids).execute()
    return new_id


def list_uploads(telegram_id: int, limit: int = 10) -> list[dict]:
    resp = (
        _supabase.table("uploads").select("*")
        .eq("telegram_id", telegram_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def get_upload(telegram_id: int, upload_id: int) -> dict | None:
    resp = (
        _supabase.table("uploads").select("*")
        .eq("telegram_id", telegram_id).eq("id", upload_id)
        .limit(1).execute()
    )
    return resp.data[0] if resp.data else None


def update_upload(telegram_id: int, upload_id: int, **fields) -> None:
    _supabase.table("uploads").update(fields).eq(
        "telegram_id", telegram_id).eq("id", upload_id).execute()


def delete_upload(telegram_id: int, upload_id: int) -> None:
    _supabase.table("uploads").delete().eq(
        "telegram_id", telegram_id).eq("id", upload_id).execute()


def delete_uploads_by_folder(telegram_id: int, folder_id: str) -> None:
    _supabase.table("uploads").delete().eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()


# -------------------------------------------------------------- keep-alive ---
# Free Supabase projects are paused after ~7 days without activity. A cheap
# request every 10 minutes keeps the project "active".
KEEPALIVE_INTERVAL_SECONDS = 10 * 60


def _ping_sync() -> None:
    _supabase.table("user_settings").select("telegram_id").limit(1).execute()


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


def list_uploads_by_folder(telegram_id: int, folder_id: str) -> list[dict]:
    """All history rows for one Drive folder, oldest first."""
    resp = (
        _supabase.table("uploads").select("*")
        .eq("telegram_id", telegram_id).eq("folder_id", folder_id)
        .order("created_at")
        .execute()
    )
    return resp.data or []


def set_folder_comment_file(telegram_id: int, folder_id: str, file_id: str | None) -> None:
    _supabase.table("uploads").update({"comment_file_id": file_id}).eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()


def rename_folder_rows(telegram_id: int, folder_id: str, new_name: str) -> None:
    _supabase.table("uploads").update({"folder_name": new_name}).eq(
        "telegram_id", telegram_id).eq("folder_id", folder_id).execute()
