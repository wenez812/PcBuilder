"""Последний роутер: фото, неизвестные сообщения и устаревшие кнопки."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import CallbackQuery, Message

from bot.keyboards import main_menu_kb
from bot.utils import texts
from config import Settings


def build_router() -> Router:
    router = Router(name="misc")

    @router.message(StateFilter(None), F.photo | F.document.mime_type.startswith("image/"))
    async def msg_photo(message: Message, settings: Settings) -> None:
        await message.answer(texts.PHOTO_NOT_AVAILABLE, reply_markup=main_menu_kb(settings.is_admin(message.from_user.id)))

    @router.message(StateFilter(None))
    async def msg_unknown(message: Message, settings: Settings) -> None:
        await message.answer("🤔 Не понял сообщение. Выберите действие в меню:",
                             reply_markup=main_menu_kb(settings.is_admin(message.from_user.id)))

    @router.callback_query()
    async def cb_stale(callback: CallbackQuery) -> None:
        await callback.answer("Эта кнопка устарела. Откройте /menu.", show_alert=True)

    return router
