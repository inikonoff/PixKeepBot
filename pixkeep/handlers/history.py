"""
«📂 Мои загрузки»: the last uploads with link, access toggle, folder comment
(!COMMENT.txt), rename, "send file to chat", delete single files or the whole
upload (to the Drive trash).
"""
import asyncio
import logging
import os
import uuid
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from pixkeep import db, texts
from pixkeep.config import config
from pixkeep.delivery import UPLOAD_CONCURRENCY
from pixkeep.drive import TXT_MIME, with_drive
from pixkeep.drive_utils import (
    GoogleAuthError,
    download_file,
    folder_is_empty,
    get_file_meta,
    publish_and_get_url,
    rename_file,
    replace_file_content,
    revoke_public_access,
    trash_file,
    upload_file,
)
from pixkeep.runtime import LOCAL_TZ, Flow, bot

router = Router(name="history")

UPLOADS_BUTTON = "📂 Мои загрузки"
UPLOADS_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=UPLOADS_BUTTON)]],
    resize_keyboard=True, is_persistent=True,
)


def _real(files: list[dict]) -> list[dict]:
    """History entries without the service txt list."""
    return [f for f in files if not f.get("report")]


async def _uploads_list_view(uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    rows = await db.list_uploads(uid, 10)
    if not rows:
        return "Загрузок пока нет.", None
    kb = [[InlineKeyboardButton(
        text=f"{texts.fmt_dt(r['created_at'])} · {r['folder_name']} · {len(_real(r['files']))} ф.",
        callback_data=f"up:{r['id']}",
    )] for r in rows]
    return "📂 Последние загрузки — выбери:", InlineKeyboardMarkup(inline_keyboard=kb)


def _upload_detail_view(u: dict) -> tuple[str, InlineKeyboardMarkup]:
    names = [f["name"] for f in _real(u["files"])]
    shown = ", ".join(names[:15]) + (f" … и ещё {len(names) - 15}" if len(names) > 15 else "")
    access = ("🔓 доступ по ссылке открыт" if u.get("shared", True)
              else "🔒 доступ по ссылке закрыт")
    text = (
        f"📁 {u['folder_name']}\n{texts.fmt_dt(u['created_at'])} · файлов: {len(names)}\n"
        f"{access}\n\nФайлы: {shown}\n\n{u['link']}"
    )
    comment = u.get("comment")
    if comment:
        text += "\n\n💬 Комментарий:\n" + (comment[:400] + ("…" if len(comment) > 400 else ""))
    uid = u["id"]
    kb = []
    if u.get("shared", True):
        kb.append([InlineKeyboardButton(text="🔗 Открыть папку", url=u["link"])])
        kb.append([InlineKeyboardButton(text="📋 Скопировать ссылку",
                                        copy_text=CopyTextButton(text=u["link"]))])
        kb.append([InlineKeyboardButton(text="🔒 Закрыть доступ по ссылке",
                                        callback_data=f"upun:{uid}")])
    else:
        kb.append([InlineKeyboardButton(text="🔓 Открыть доступ по ссылке",
                                        callback_data=f"upsh:{uid}")])
    kb.append([InlineKeyboardButton(text="💬 Добавить комментарий", callback_data=f"cmt:{uid}")])
    if comment:
        kb.append([InlineKeyboardButton(text="🧹 Удалить комментарий", callback_data=f"cmtdel:{uid}")])
    kb.append([InlineKeyboardButton(text="📥 Прислать файл в чат", callback_data=f"upg:{uid}:0")])
    kb.append([InlineKeyboardButton(text="✏️ Переименовать папку", callback_data=f"upren:{uid}")])
    kb.append([InlineKeyboardButton(text="🗑 Удалить отдельные файлы", callback_data=f"upf:{uid}:0")])
    kb.append([InlineKeyboardButton(text="🗑 Удалить всю загрузку", callback_data=f"updel:{uid}")])
    kb.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="uplist")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _send_uploads(message: Message):
    text, kb = await _uploads_list_view(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("uploads"))
async def uploads_cmd(message: Message):
    await _send_uploads(message)


@router.message(F.text == UPLOADS_BUTTON)
async def uploads_button(message: Message):
    await _send_uploads(message)


@router.callback_query(F.data == "uplist")
async def on_uploads_list(cq: CallbackQuery):
    text, kb = await _uploads_list_view(cq.from_user.id)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.regexp(r"^up:\d+$"))
async def on_upload_open(cq: CallbackQuery):
    u = await db.get_upload(cq.from_user.id, int(cq.data.split(":")[1]))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer()


async def _account_for(cq: CallbackQuery, u: dict) -> dict | None:
    account = await db.get_account_by_id(cq.from_user.id, u["account_id"])
    if not account:
        await cq.answer("Аккаунт этой загрузки больше не подключён", show_alert=True)
    return account


@router.callback_query(F.data.regexp(r"^up(un|sh):\d+$"))
async def on_upload_share_toggle(cq: CallbackQuery):
    action, raw_id = cq.data.split(":")
    u = await db.get_upload(cq.from_user.id, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    try:
        if action == "upun":
            await with_drive(account, lambda s, t: revoke_public_access(s, t, u["folder_id"]))
            await db.update_upload(cq.from_user.id, u["id"], shared=False)
            u["shared"] = False
            note = "Доступ по ссылке закрыт"
        else:
            await with_drive(account, lambda s, t: publish_and_get_url(s, t, u["folder_id"]))
            await db.update_upload(cq.from_user.id, u["id"], shared=True)
            u["shared"] = True
            note = "Доступ по ссылке открыт"
    except Exception as e:
        logging.exception("share toggle failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer(note)


COMMENT_FILE_NAME = "!COMMENT.txt"   # "!" sorts before digits and letters in Drive


async def _sync_comment_file(account: dict, tg: int, folder_id: str) -> None:
    """Rebuilds !COMMENT.txt in the folder from the comments of every
    history row of that folder (one file per folder, oldest first). Creates
    it, replaces its content, or trashes it when no comments are left."""
    rows = await db.list_uploads_by_folder(tg, folder_id)
    comments = [r["comment"] for r in rows if r.get("comment")]
    file_id = next((r["comment_file_id"] for r in rows if r.get("comment_file_id")), None)

    if not comments:
        if file_id:
            await with_drive(account, lambda s, t: trash_file(s, t, file_id))
            await db.set_folder_comment_file(tg, folder_id, None)
        return

    os.makedirs(config.TEMP_DIR, exist_ok=True)
    path = os.path.join(config.TEMP_DIR, f"comment_{uuid.uuid4().hex}.txt")
    with open(path, "w", encoding="utf-8-sig") as f:   # BOM: Windows Notepad reads Cyrillic
        f.write("\n\n".join(comments) + "\n")
    try:
        done = False
        if file_id:
            try:
                await with_drive(account, lambda s, t: replace_file_content(
                    s, t, path, file_id, TXT_MIME))
                done = True
            except RuntimeError as e:
                if "404" not in str(e):   # file was removed by hand -> recreate below
                    raise
        if not done:
            new_id = await with_drive(account, lambda s, t: upload_file(
                s, t, path, folder_id, COMMENT_FILE_NAME, TXT_MIME))
            await db.set_folder_comment_file(tg, folder_id, new_id)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


async def _after_removal(account: dict, tg: int, folder_id: str) -> bool:
    """Call once an upload's row is deleted and its files are trashed:
    updates (or removes) the folder's comment file, then trashes the folder
    if nothing is left in it. Returns True if the folder was trashed."""
    try:
        await _sync_comment_file(account, tg, folder_id)
    except Exception:
        logging.exception("comment file sync failed")

    async def _chk(session, token):
        if await folder_is_empty(session, token, folder_id):
            await trash_file(session, token, folder_id)
            return True
        return False

    return await with_drive(account, _chk)


async def _restore_state(state: FSMContext, prev: str | None) -> None:
    await state.set_state(prev)
    await state.update_data(comment_upload_id=None, comment_prev_state=None)


@router.callback_query(F.data.regexp(r"^cmt:\d+$"))
async def on_comment_start(cq: CallbackQuery, state: FSMContext):
    uid = int(cq.data.split(":")[1])
    u = await db.get_upload(cq.from_user.id, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    cur = await state.get_state()
    if cur != Flow.waiting_comment.state:   # keep e.g. a pending folder-name question
        await state.update_data(comment_prev_state=cur)
    await state.update_data(comment_upload_id=uid)
    await state.set_state(Flow.waiting_comment)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cmtx")],
    ])
    await cq.message.answer(
        f"💬 Введите комментарий к папке «{u['folder_name']}» и отправьте одним сообщением.\n"
        f"Он сохранится в файл {COMMENT_FILE_NAME} в этой папке. "
        "Папка открыта всем, у кого есть ссылка, — комментарий они тоже увидят.",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data == "cmtx")
async def on_comment_cancel(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if await state.get_state() == Flow.waiting_comment.state:
        await _restore_state(state, data.get("comment_prev_state"))
    await cq.message.edit_text("❌ Комментарий отменён.")
    await cq.answer()


@router.message(Flow.waiting_comment, F.text, ~F.text.startswith("/"))
async def on_comment_text(message: Message, state: FSMContext):
    tg = message.from_user.id
    data = await state.get_data()
    prev = data.get("comment_prev_state")
    uid = data.get("comment_upload_id")
    text = message.text.strip()
    u = await db.get_upload(tg, uid) if uid else None
    account = await db.get_account_by_id(tg, u["account_id"]) if u else None
    if not u or not account:
        await _restore_state(state, prev)
        await message.answer("Не нашёл эту загрузку или её аккаунт — комментарий не сохранён.")
        return

    block = f"[{datetime.now(LOCAL_TZ):%Y-%m-%d %H:%M}]\n{text}"
    new_comment = f"{u['comment']}\n\n{block}" if u.get("comment") else block
    try:
        await db.update_upload(tg, uid, comment=new_comment)
    except Exception as e:
        logging.exception("saving comment failed")
        await _restore_state(state, prev)
        await message.answer(f"Не удалось сохранить комментарий: {e}")
        return
    try:
        await _sync_comment_file(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("comment upload failed")
        await _restore_state(state, prev)
        await message.answer(
            f"Комментарий сохранён в боте, но в папку загрузить не удалось: {e}\n"
            "Попробуй добавить ещё один комментарий — файл перезапишется целиком."
        )
        return
    await _restore_state(state, prev)
    await message.answer(f"💬 Сохранено в {COMMENT_FILE_NAME} (папка «{u['folder_name']}»).")


@router.callback_query(F.data.regexp(r"^cmtdel:\d+$"))
async def on_comment_delete(cq: CallbackQuery):
    tg = cq.from_user.id
    uid = int(cq.data.split(":")[1])
    u = await db.get_upload(tg, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    try:
        await db.update_upload(tg, uid, comment=None)
        u["comment"] = None
        await _sync_comment_file(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("comment delete failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return
    text, kb = _upload_detail_view(u)
    await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cq.answer("Комментарий удалён")


FILES_PER_PAGE = 8


def _upload_files_view(u: dict, page: int, mode: str = "del") -> tuple[str, InlineKeyboardMarkup]:
    """mode "del": tap a file to trash it; mode "get": tap a file to have it
    sent to the chat."""
    files = _real(u["files"])
    pages = max(1, (len(files) + FILES_PER_PAGE - 1) // FILES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = files[page * FILES_PER_PAGE:(page + 1) * FILES_PER_PAGE]
    uid = u["id"]
    icon, act, nav_cb = ("🗑", "upfd", "upf") if mode == "del" else ("📥", "upgd", "upg")
    kb = [[InlineKeyboardButton(
        text=f"{icon} {f['name']}"[:60], callback_data=f"{act}:{uid}:{f['id']}"
    )] for f in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{nav_cb}:{uid}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{nav_cb}:{uid}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"up:{uid}")])
    head = ("Нажми на файл, чтобы удалить его (уйдёт в корзину Google Диска)."
            if mode == "del" else
            "Нажми на файл, чтобы я прислал его сюда (до 50 МБ — лимит Telegram для ботов).")
    text = (f"{head}\nПапка «{u['folder_name']}», файлов: {len(files)}"
            + (f" · стр. {page + 1}/{pages}" if pages > 1 else ""))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


@router.callback_query(F.data.regexp(r"^upf:\d+:\d+$"))
async def on_upload_files(cq: CallbackQuery):
    _, raw_id, raw_page = cq.data.split(":")
    u = await db.get_upload(cq.from_user.id, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    text, kb = _upload_files_view(u, int(raw_page))
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.regexp(r"^upfd:\d+:[\w-]+$"))
async def on_upload_file_delete(cq: CallbackQuery):
    _, raw_id, file_id = cq.data.split(":")
    tg = cq.from_user.id
    u = await db.get_upload(tg, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    target = next((f for f in _real(u["files"]) if f["id"] == file_id), None)
    if not target:
        # tapped twice / stale button — just refresh the list
        text, kb = _upload_files_view(u, 0)
        await cq.message.edit_text(text, reply_markup=kb)
        await cq.answer("Этого файла уже нет в списке")
        return
    account = await _account_for(cq, u)
    if not account:
        return

    rest = [f for f in u["files"] if f["id"] != file_id]
    rest_real = _real(rest)
    folder_gone = False

    async def _do(session, token):
        await trash_file(session, token, file_id)
        if not rest_real:
            for rf in rest:            # service txt list goes with the last file
                await trash_file(session, token, rf["id"])

    try:
        await with_drive(account, _do)
        if not rest_real:
            await db.delete_upload(tg, u["id"])
            folder_gone = await _after_removal(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("delete single file failed")
        await cq.answer(f"Ошибка: {e}"[:190], show_alert=True)
        return

    if not rest_real:
        if folder_gone:
            await db.delete_uploads_by_folder(tg, u["folder_id"])
        await cq.message.edit_text("🗑 Файл удалён — это был последний в загрузке, она убрана из списка.")
        await cq.answer()
        return

    await db.update_upload(tg, u["id"], files=rest)
    u["files"] = rest
    text, kb = _upload_files_view(u, 0)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer(f"Удалён: {target['name']}")


TG_SEND_LIMIT = 50 * 1024 * 1024   # Bot API: bots can send files up to 50 MB


@router.callback_query(F.data.regexp(r"^upg:\d+:\d+$"))
async def on_upload_get_list(cq: CallbackQuery):
    _, raw_id, raw_page = cq.data.split(":")
    u = await db.get_upload(cq.from_user.id, int(raw_id))
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    text, kb = _upload_files_view(u, int(raw_page), mode="get")
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.regexp(r"^upgd:\d+:[\w-]+$"))
async def on_upload_get_file(cq: CallbackQuery):
    _, raw_id, file_id = cq.data.split(":")
    u = await db.get_upload(cq.from_user.id, int(raw_id))
    target = next((f for f in _real(u["files"]) if f["id"] == file_id), None) if u else None
    if not target:
        await cq.answer("Файл не найден в этой загрузке", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    await cq.answer("Скачиваю с Диска…")
    path = None
    try:
        meta = await with_drive(account, lambda s, t: get_file_meta(s, t, file_id))
        if meta["size"] and meta["size"] > TG_SEND_LIMIT:
            await cq.message.answer(
                f"«{meta['name']}» весит {texts.fmt_bytes(meta['size'])} — больше 50 МБ, "
                "прислать в чат не получится. Открой его по ссылке на папку.")
            return
        os.makedirs(config.TEMP_DIR, exist_ok=True)
        path = os.path.join(config.TEMP_DIR, f"send_{uuid.uuid4().hex}")
        await with_drive(account, lambda s, t: download_file(s, t, file_id, path))
        await bot.send_document(
            cq.message.chat.id,
            FSInputFile(path, filename=meta["name"] or target["name"]),
            caption=f"📁 {u['folder_name']}",
        )
    except Exception as e:
        logging.exception("send file to chat failed")
        await cq.message.answer(f"Не удалось прислать файл: {e}")
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@router.callback_query(F.data.regexp(r"^upren:\d+$"))
async def on_rename_start(cq: CallbackQuery, state: FSMContext):
    uid = int(cq.data.split(":")[1])
    u = await db.get_upload(cq.from_user.id, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    cur = await state.get_state()
    if cur != Flow.waiting_rename.state:
        await state.update_data(rename_prev_state=cur)
    await state.update_data(rename_upload_id=uid)
    await state.set_state(Flow.waiting_rename)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="renx")],
    ])
    await cq.message.answer(
        f"✏️ Введите новое имя для папки «{u['folder_name']}» одним сообщением.",
        reply_markup=kb,
    )
    await cq.answer()


async def _restore_rename_state(state: FSMContext, prev: str | None) -> None:
    await state.set_state(prev)
    await state.update_data(rename_upload_id=None, rename_prev_state=None)


@router.callback_query(F.data == "renx")
async def on_rename_cancel(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if await state.get_state() == Flow.waiting_rename.state:
        await _restore_rename_state(state, data.get("rename_prev_state"))
    await cq.message.edit_text("❌ Переименование отменено.")
    await cq.answer()


@router.message(Flow.waiting_rename, F.text, ~F.text.startswith("/"))
async def on_rename_text(message: Message, state: FSMContext):
    tg = message.from_user.id
    data = await state.get_data()
    prev = data.get("rename_prev_state")
    uid = data.get("rename_upload_id")
    new_name = message.text.strip()
    u = await db.get_upload(tg, uid) if uid else None
    account = await db.get_account_by_id(tg, u["account_id"]) if u else None
    if not u or not account:
        await _restore_rename_state(state, prev)
        await message.answer("Не нашёл эту загрузку или её аккаунт — не переименовал.")
        return
    if not new_name or len(new_name) > 100:
        await message.answer("Имя должно быть от 1 до 100 символов. Введи ещё раз или нажми «Отмена».")
        return
    try:
        await with_drive(account, lambda s, t: rename_file(s, t, u["folder_id"], new_name))
        await db.rename_folder_rows(tg, u["folder_id"], new_name)
    except Exception as e:
        logging.exception("rename failed")
        await _restore_rename_state(state, prev)
        await message.answer(f"Не удалось переименовать: {e}")
        return
    await _restore_rename_state(state, prev)
    await message.answer(f"✏️ Папка «{u['folder_name']}» теперь называется «{new_name}».")


@router.callback_query(F.data.regexp(r"^updel:\d+$"))
async def on_upload_delete_ask(cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    u = await db.get_upload(cq.from_user.id, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"updelok:{uid}")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"up:{uid}")],
    ])
    await cq.message.edit_text(
        f"Удалить {len(_real(u['files']))} файл(ов) из папки «{u['folder_name']}»?\n"
        "Они уйдут в корзину Google Диска (там их можно восстановить в течение "
        "30 дней). Если папка опустеет, она тоже уйдёт в корзину.",
        reply_markup=kb,
    )
    await cq.answer()


@router.callback_query(F.data.regexp(r"^updelok:\d+$"))
async def on_upload_delete_do(cq: CallbackQuery):
    uid = int(cq.data.split(":")[1])
    tg = cq.from_user.id
    u = await db.get_upload(tg, uid)
    if not u:
        await cq.answer("Загрузка не найдена", show_alert=True)
        return
    account = await _account_for(cq, u)
    if not account:
        return
    await cq.message.edit_text("Удаляю…")

    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)
    failed: list[dict] = []

    async def _one(session, token, f):
        async with sem:
            try:
                await trash_file(session, token, f["id"])
            except GoogleAuthError:
                raise
            except Exception as e:
                logging.error(f"trash failed for {f['name']}: {e}")
                failed.append(f)

    async def _all(session, token):
        failed.clear()
        await asyncio.gather(*[_one(session, token, f) for f in u["files"]])

    folder_gone = False
    try:
        await with_drive(account, _all)
        if not failed:
            await db.delete_upload(tg, uid)
            folder_gone = await _after_removal(account, tg, u["folder_id"])
    except Exception as e:
        logging.exception("delete upload failed")
        await cq.message.edit_text(f"Ошибка при удалении: {e}")
        return

    if failed:
        await db.update_upload(tg, uid, files=failed)
        await cq.message.edit_text(
            f"Удалено {len(_real(u['files'])) - len(_real(failed))}, не удалось: "
            f"{', '.join(f['name'] for f in failed)}.\nМожно повторить из списка /uploads."
        )
        return
    if folder_gone:
        await db.delete_uploads_by_folder(tg, u["folder_id"])
    await cq.message.edit_text(
        "🗑 Удалено (в корзине Google Диска)."
        + (" Папка тоже." if folder_gone else " Папка осталась — в ней есть другие файлы.")
    )
    await cq.answer()

