"""Telegram handlers, one Router per area."""
from aiogram import Router

from pixkeep.handlers import history, intake, settings, start


def routers() -> list[Router]:
    """In registration order — it matters: commands, the «Мои загрузки»
    button and comment/rename input must win over "any text is a folder
    name" in intake."""
    return [start.router, settings.router, history.router, intake.router]
