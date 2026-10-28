"""
/start, /accounts (switch / add Google accounts), /logout, /help, /version.
"""
import asyncio

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pixkeep import VERSION, db, texts
from pixkeep.config import config
from pixkeep.drive import account_quota
from pixkeep.handlers.history import UPLOADS_KEYBOARD
from pixkeep.oauth import build_auth_url

router = Router(name="start")


def _accounts_keyboard(accounts: list[dict], telegram_id: int,
                       quotas: dict[int, str] | None = None) -> InlineKeyboardMarkup:
    rows = []
    for a in accounts:
        mark = "✅ " if a["is_active"] else ""
        q = f" · {quotas[a['id']]}" if quotas and a["id"] in quotas else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{a['email']}{q}", callback_data=f"acct:{a['id']}"
        )])
    rows.append([InlineKeyboardButton(
        text="➕ Добавить аккаунт", url=build_auth_url(telegram_id)
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _accounts_view(uid: int) -> tuple[str, InlineKeyboardMarkup] | None:
    accounts = await db.list_accounts(uid)
    if not accounts:
        return None
    full = {a["id"]: await db.get_account_by_id(uid, a["id"]) for a in accounts}
    qs = await asyncio.gather(*[
        account_quota(full[a["id"]]) if full[a["id"]] else asyncio.sleep(0, result=None)
        for a in accounts
    ])
    quotas = {a["id"]: texts.quota_label(q) for a, q in zip(accounts, qs)}
    return (
        "Твои Google-аккаунты (занято места) — нажми, чтобы сделать активным для загрузки:",
        _accounts_keyboard(accounts, uid, quotas),
    )


@router.message(CommandStart())
async def start(message: Message):
    uid = message.from_user.id
    accounts = await db.list_accounts(uid)
    auth_url = build_auth_url(uid)

    if accounts:
        active = next((a for a in accounts if a["is_active"]), None)
        active_email = active["email"] if active else "не выбран — см. /accounts"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Подключить ещё аккаунт", url=auth_url)]
        ])
        await message.answer(
            f"Подключено аккаунтов: {len(accounts)}. Активный: {active_email}\n\n"
            "Пришли фото или файлы (можно альбомом) — загружу на активный "
            "аккаунт (спрошу только имя папки) и после успешной загрузки "
            "удалю исходные сообщения из чата.\n\n"
            "Чистка метаданных и обезличивание имён — настраиваются заранее в "
            "/settings.\n\n"
            "⚠️ Присылай как файл (📎 → Файл), а не как обычное фото — иначе "
            "Telegram сам пережмёт изображение.\n\n"
            "/uploads — последние загрузки (ссылки, удаление)\n"
            "/accounts — посмотреть все аккаунты и переключиться\n"
            "/settings — настройки очистки/переименования\n"
            "/logout — отключить все аккаунты.",
            reply_markup=kb,
        )
        await message.answer("Меню внизу 👇", reply_markup=UPLOADS_KEYBOARD)
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Подключить Google Drive", url=auth_url)]
    ])
    await message.answer(
        "Привет! Я PixKeep — чищу метаданные (EXIF/GPS/IPTC/XMP) из фото и файлов "
        "без потери качества и складываю результат на твой Google Drive.\n\n"
        "Сначала подключи свой Диск — откроется страница Google, войди и "
        "разреши доступ. Пароль вводится только у Google, я его не вижу. "
        "Доступ ограничен: я вижу только файлы и папки, которые сам создам "
        "(scope drive.file) — остальной твой Диск мне не виден.\n\n"
        "Можно подключить сразу несколько своих Google-аккаунтов и "
        "переключаться между ними через /accounts.",
        reply_markup=kb,
    )


@router.message(Command("accounts"))
async def accounts_cmd(message: Message):
    view = await _accounts_view(message.from_user.id)
    if not view:
        await message.answer("Нет подключённых аккаунтов. /start чтобы подключить.")
        return
    text, kb = view
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("acct:"))
async def on_switch_account(cq: CallbackQuery):
    account_id = int(cq.data.split(":", 1)[1])
    await db.set_active_account(cq.from_user.id, account_id)
    view = await _accounts_view(cq.from_user.id)
    if view:
        await cq.message.edit_reply_markup(reply_markup=view[1])
    await cq.answer("Активный аккаунт изменён")


@router.message(Command("logout"))
async def logout(message: Message):
    await db.remove_all_accounts(message.from_user.id)
    await message.answer(
        "Отключил все твои Google-аккаунты. Чтобы подключить снова — /start.\n"
        "Токены удалены из базы. Доступ приложения можно также отозвать вручную "
        "на странице myaccount.google.com/permissions."
    )


HELP_TEXT = (
    "PixKeep — загружаю твои фото и файлы на Google Диск.\n\n"
    "Как пользоваться: пришли файлы (можно альбомом или документами), "
    "напиши имя папки — и я загружу их, а исходные сообщения удалю из чата.\n\n"
    "/start — подключить Диск, статус\n"
    "/uploads — мои загрузки: ссылки, комментарии, удаление\n"
    "/accounts — аккаунты и свободное место\n"
    "/settings — чистка метаданных, имена, коррекция тёмных фото, дубликаты\n"
    "/logout — отключить все аккаунты\n"
    "/version — версия кода\n\n"
    "Лимиты: Telegram даёт ботам скачивать файлы до 20 МБ; за одну пачку — "
    f"не больше {config.MAX_BATCH_MB} МБ."
)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(HELP_TEXT)


@router.message(Command("version"))
async def version_cmd(message: Message):
    """Diagnostic: confirms which code is actually running on the server,
    since a stale deploy is indistinguishable from a code bug otherwise."""
    await message.answer(f"PixKeep v{VERSION}")

