"""
/settings: standing upload settings, applied silently to every next batch.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from pixkeep import db
from pixkeep.runtime import Flow
from pixkeep.scheduler import DEADLINE_PRESETS, INTERVAL_PRESETS, parse_hhmm

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
        [InlineKeyboardButton(text=_deferred_label(s), callback_data="dfr")],
    ])


SETTINGS_TEXT = ("⚙️ Настройки загрузки — применяются к каждой следующей загрузке "
                 "автоматически, спрашивать не буду:")


def _deadline_label(s: dict) -> str:
    return f"до {s['deferred_deadline']}" if s["deferred_deadline"] else "без срока"


def _deferred_label(s: dict) -> str:
    if not db.deferred_available:
        return "🕒 Загрузка по расписанию — нужна миграция БД"
    if not s["deferred"]:
        return "🕒 Загрузка: сразу"
    return f"🕒 По расписанию: {s['deferred_min']}–{s['deferred_max']} мин, {_deadline_label(s)}"


DEFERRED_TEXT = (
    "🕒 <b>Загрузка по расписанию</b>\n\n"
    "Присланные пачки ждут в чате и уходят на Диск по одной, целиком, через "
    "случайные промежутки в выбранном диапазоне. Пока пачка ждёт, бот её не "
    "скачивает — ресурсов почти не тратится.\n\n"
    "К крайнему сроку загружу всё, что останется. Под статусом очереди есть "
    "кнопка «⏩ Загрузить всё сейчас»."
)


def _deferred_keyboard(s: dict) -> InlineKeyboardMarkup:
    mark = "✅" if s["deferred"] else "⬜"
    cur_int = (s["deferred_min"], s["deferred_max"])
    ints = [InlineKeyboardButton(text=("✓ " if (lo, hi) == cur_int else "") + f"{lo}–{hi} мин",
                                 callback_data=f"dfr:i:{lo}:{hi}") for lo, hi in INTERVAL_PRESETS]
    cur_dl = s["deferred_deadline"]
    dls = [InlineKeyboardButton(text=("✓ " if not cur_dl else "") + "без срока", callback_data="dfr:d:none")]
    dls += [InlineKeyboardButton(text=("✓ " if d == cur_dl else "") + d, callback_data=f"dfr:d:{d.replace(':', '')}")
            for d in DEADLINE_PRESETS]
    custom = cur_dl and cur_dl not in DEADLINE_PRESETS
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark} Загружать по расписанию", callback_data="dfr:t")],
        ints,
        dls,
        [InlineKeyboardButton(text=(f"✓ свой срок: {cur_dl}" if custom else "✏️ Свой срок…"),
                              callback_data="dfr:dc")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="dfr:back")],
    ])


@router.message(Command("settings"))
async def settings_cmd(message: Message):
    if not await db.get_active_account(message.from_user.id):
        await message.answer("Сначала подключи Google Drive командой /start.")
        return
    s = await db.get_settings(message.from_user.id)
    await message.answer(SETTINGS_TEXT, reply_markup=_settings_keyboard(s))


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



# ------------------------------------------------------- scheduled upload ---
@router.callback_query(F.data.startswith("dfr"))
async def on_deferred(cq: CallbackQuery, state: FSMContext):
    if not db.deferred_available:
        await cq.answer("Сначала выполни в Supabase SQL из migrations/002_deferred_upload.sql "
                        "и перезапусти бота.", show_alert=True)
        return
    uid = cq.from_user.id
    parts = cq.data.split(":")
    if parts == ["dfr", "back"]:
        s = await db.get_settings(uid)
        await cq.message.edit_text(SETTINGS_TEXT, reply_markup=_settings_keyboard(s))
        await cq.answer()
        return
    if parts == ["dfr", "dc"]:
        cur = await state.get_state()
        if cur != Flow.waiting_deadline.state:
            await state.update_data(deadline_prev_state=cur)
        await state.set_state(Flow.waiting_deadline)
        await cq.message.answer("✏️ Пришли крайний срок в формате ЧЧ:ММ, например 13:30.")
        await cq.answer()
        return
    if parts == ["dfr", "t"]:
        s = await db.get_settings(uid)
        await db.set_setting(uid, "deferred", not s["deferred"])
    elif len(parts) == 4 and parts[1] == "i":
        lo, hi = int(parts[2]), int(parts[3])
        await db.set_setting(uid, "deferred_min", lo)
        await db.set_setting(uid, "deferred_max", hi)
    elif len(parts) == 3 and parts[1] == "d":
        await db.set_setting(uid, "deferred_deadline", "" if parts[2] == "none" else f"{parts[2][:2]}:{parts[2][2:]}")
    s = await db.get_settings(uid)
    await cq.message.edit_text(DEFERRED_TEXT, parse_mode="HTML", reply_markup=_deferred_keyboard(s))
    await cq.answer("Обновлено" if len(parts) > 1 else None)


@router.message(Flow.waiting_deadline, F.text, ~F.text.startswith("/"))
async def on_deadline_text(message: Message, state: FSMContext):
    hm = parse_hhmm(message.text)
    if not hm:
        await message.answer("Не понял время. Пришли в формате ЧЧ:ММ, например 13:30.")
        return
    data = await state.get_data()
    await state.set_state(data.get("deadline_prev_state"))
    value = f"{hm[0]:02d}:{hm[1]:02d}"
    await db.set_setting(message.from_user.id, "deferred_deadline", value)
    s = await db.get_settings(message.from_user.id)
    await message.answer(f"🕒 Крайний срок: {value}.\n\n" + DEFERRED_TEXT, parse_mode="HTML",
                         reply_markup=_deferred_keyboard(s))
