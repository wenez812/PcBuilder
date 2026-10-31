"""Вспомогательные функции для работы с Telegram API."""
from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

logger = logging.getLogger(__name__)


MAX_TEXT = 4000


def fit_text(text: str, limit: int = MAX_TEXT) -> str:
    """Обрезает по границе строки, чтобы не разорвать HTML-тег и не превысить лимит Telegram (4096)."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    return cut + "\n…"


async def show(
    event: Message | CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Для нажатия кнопки редактирует сообщение, для команды отправляет новое."""
    text = fit_text(text)
    if isinstance(event, CallbackQuery):
        message = event.message
        if message is None or not hasattr(message, "edit_text"):
            # Сообщение слишком старое или недоступно — отправляем новое
            await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)
            return
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.warning("Не удалось отредактировать сообщение (%s), отправляю новое", exc)
            await event.bot.send_message(event.from_user.id, text, reply_markup=reply_markup)
    else:
        await event.answer(text, reply_markup=reply_markup)
