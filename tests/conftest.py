"""Dummy environment so pixkeep.config can be imported without real secrets.
Nothing here talks to Telegram, Google or Supabase."""
import os
import sys

os.environ.setdefault("BOT_TOKEN", "123456:TEST-token")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-secret")
os.environ.setdefault("OAUTH_REDIRECT_URI", "https://example.test/oauth/callback")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
