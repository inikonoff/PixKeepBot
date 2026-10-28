"""
/settings: standing upload settings, applied silently to every next batch.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pixkeep import db

router = Router(name="settings")


PHOTO_FIX_ORDER = ["off", "auto", "always"]
PHOTO_FIX_LABELS = {
    "off": "выкл",
    "auto": "авто (только тёмные)",
    "always": "всегда (поднять тени)",
}


def _settings_keyboard(s: dict) -> InlineKeyboardMarkup:
    clean_mark = "✅" if s["clean_metadata"] else "⬜"
    rename_mark = "✅" if s["anonymize_names"] else "⬜"
    date_mark = "✅" if s["auto_date_folder"] else "⬜"
    replace_mark = "✅" if s["replace_duplicates"] else "⬜"
    list_mark = "✅" if s["upload_list"] else "⬜"
    fix_label = PHOTO_FIX_LABELS.get(s["photo_fix"], PHOTO_FIX_LABELS["off"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{clean_mark} Чистить метаданные (EXIF/GPS)",
            callback_data="toggle:clean_metadata",
        )],
        [InlineKeyboardButton(
            text=f"{rename_mark} Обезличивать имена (001, 002…)",
            callback_data="toggle:anonymize_names",
        )],
        [InlineKeyboardButton(
            text=f"{date_mark} Папка по дате съёмки (без вопроса)",
            callback_data="toggle:auto_date_folder",
        )],
        [InlineKeyboardButton(
            text=f"{replace_mark} Заменять файлы с таким же именем",
            callback_data="toggle:replace_duplicates",
        )],
        [InlineKeyboardButton(
            text=f"🌗 Коррекция тёмных фото: {fix_label}",
            callback_data="cycle:photo_fix",
        )],
        [InlineKeyboardButton(
            text=f"{list_mark} Список файлов (txt) в папку",
            callback_data="toggle:upload_list",
        )],
    ])


@router.message(Command("settings"))
async def settings_cmd(message: Message):
    if not await db.get_active_account(message.from_user.id):
        await message.answer("Сначала подключи Google Drive командой /start.")
        return
    s = await db.get_settings(message.from_user.id)
    await message.answer(
        "⚙️ Настройки загрузки — применяются к каждой следующей загрузке "
        "автоматически, спрашивать не буду:",
        reply_markup=_settings_keyboard(s),
    )


@router.callback_query(F.data.startswith("toggle:"))
async def on_toggle_setting(cq: CallbackQuery):
    field = cq.data.split(":", 1)[1]
    s = await db.get_settings(cq.from_user.id)
    if field not in s:
        await cq.answer()
        return
    if not isinstance(s[field], bool):
        await cq.answer()
        return
    new_value = not s[field]
    await db.set_setting(cq.from_user.id, field, new_value)
    s[field] = new_value
    await cq.message.edit_reply_markup(reply_markup=_settings_keyboard(s))
    await cq.answer("Обновлено")


@router.callback_query(F.data == "cycle:photo_fix")
async def on_cycle_photo_fix(cq: CallbackQuery):
    s = await db.get_settings(cq.from_user.id)
    cur = s["photo_fix"] if s["photo_fix"] in PHOTO_FIX_ORDER else "off"
    new_value = PHOTO_FIX_ORDER[(PHOTO_FIX_ORDER.index(cur) + 1) % len(PHOTO_FIX_ORDER)]
    await db.set_setting(cq.from_user.id, "photo_fix", new_value)
    s["photo_fix"] = new_value
    await cq.message.edit_reply_markup(reply_markup=_settings_keyboard(s))
    await cq.answer(PHOTO_FIX_LABELS[new_value])

