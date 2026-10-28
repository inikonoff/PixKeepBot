"""
Central configuration from environment variables (see README.md, "Render").
"""
import os


class Config:
    BOT_TOKEN = os.environ["BOT_TOKEN"]

    # Only this Telegram user ID may use the bot at all — everyone else is
    # silently ignored (no reply, no acknowledgment the bot even exists).
    # v5.26: ADMIN_IDS may hold several comma-separated IDs; the old single
    # ADMIN_ID variable keeps working.
    ADMIN_IDS = {
        int(x) for x in
        (os.environ.get("ADMIN_IDS") or os.environ["ADMIN_ID"]).replace(" ", "").split(",")
        if x
    }

    # v5.26: per-batch cap on downloaded bytes (protects the small Render disk).
    MAX_BATCH_MB = int(os.environ.get("MAX_BATCH_MB", "300"))

    # One Google Cloud OAuth "Web application" client, shared by all users.
    # Each user authorizes their OWN Google Drive via the consent screen.
    GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
    GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
    # Must exactly match a redirect URI registered in Google Cloud Console, e.g.
    # https://your-app.onrender.com/oauth/callback
    OAUTH_REDIRECT_URI = os.environ["OAUTH_REDIRECT_URI"]

    SUPABASE_URL = os.environ["SUPABASE_URL"]
    SUPABASE_KEY = os.environ["SUPABASE_KEY"]

    PORT = int(os.environ.get("PORT", "10000"))

    # drive.file: app can only see/edit files IT created or that the user
    # explicitly opened with it — not the whole Drive.
    # userinfo.email: lets us fetch the account's email so multiple
    # connected Google accounts can be told apart in /accounts.
    GOOGLE_SCOPE = (
        "https://www.googleapis.com/auth/drive.file "
        "https://www.googleapis.com/auth/userinfo.email"
    )

    TEMP_DIR = "/tmp/cleandrive_bot"


config = Config()
