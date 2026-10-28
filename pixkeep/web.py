"""
Small aiohttp server next to polling: Google OAuth callback and /health
(the external pinger keeps the Render instance awake through it).
"""
import html
import logging

from aiohttp import web

from pixkeep import db
from pixkeep.config import config
from pixkeep.oauth import exchange_code, get_user_email
from pixkeep.runtime import bot


def _oauth_page(ok: bool, text: str) -> web.Response:
    color = "#1a7f37" if ok else "#c62828"
    icon = "✅" if ok else "⚠️"
    body = (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>PixKeep</title><style>"
        "body{font-family:system-ui,sans-serif;display:flex;min-height:100vh;"
        "align-items:center;justify-content:center;margin:0;padding:16px;"
        "background:#f6f7f9;color:#222}"
        "@media(prefers-color-scheme:dark){body{background:#16181c;color:#eee}}"
        ".c{max-width:420px;text-align:center}.i{font-size:56px}"
        f"h1{{font-size:22px;color:{color}}}</style></head><body><div class='c'>"
        f"<div class='i'>{icon}</div><h1>{'Готово' if ok else 'Не получилось'}</h1>"
        f"<p>{html.escape(text)}</p><p>Можно вернуться в Telegram.</p></div></body></html>"
    )
    return web.Response(text=body, content_type="text/html", status=200 if ok else 400)


async def oauth_callback(request: web.Request) -> web.Response:
    state = request.query.get("state", "")
    code = request.query.get("code", "")
    error = request.query.get("error")
    if error:
        return _oauth_page(False, f"Доступ не предоставлен ({error}).")
    try:
        telegram_id, access_token, refresh_token = await exchange_code(state, code)
        email = await get_user_email(access_token)
        await db.add_or_update_account(telegram_id, email, access_token, refresh_token)
        await bot.send_message(
            telegram_id,
            f"Google Drive подключён ✅ ({email})\nТеперь пришли фото или файлы."
        )
        return _oauth_page(True, f"Диск {email} подключён.")
    except Exception as e:
        logging.exception("oauth callback failed")
        return _oauth_page(False, str(e)[:200])


async def health(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def run_web():
    app = web.Application()
    app.router.add_get("/oauth/callback", oauth_callback)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.PORT)
    await site.start()
    logging.info(f"web server on :{config.PORT}")
