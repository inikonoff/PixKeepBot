"""Buttons of the scheduled-upload queue (status message and pause notice)."""
from aiogram import F, Router
from aiogram.types import CallbackQuery

from pixkeep import db, scheduler

router = Router(name="queue")


@router.callback_query(F.data == "q:now")
async def on_flush_now(cq: CallbackQuery):
    if not await db.queue_list(cq.from_user.id):
        await cq.answer("Очередь уже пуста", show_alert=True)
        return
    await cq.answer("Загружаю всё, что в очереди…")
    await scheduler.flush_now(cq.from_user.id, cq.message.chat.id)


@router.callback_query(F.data == "q:go")
async def on_resume(cq: CallbackQuery):
    await cq.answer("Продолжаю очередь")
    await cq.message.edit_reply_markup(reply_markup=None)
    await scheduler.resume(cq.from_user.id, cq.message.chat.id)
