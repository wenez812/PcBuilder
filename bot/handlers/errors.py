"""Глобальный обработчик ошибок: лог, запись в БД, понятное сообщение пользователю."""
from __future__ import annotations

import logging
import traceback

from aiogram import Router
from aiogram.types import ErrorEvent

from bot.database import Database
from bot.keyboards import back_to_menu_kb
from bot.services import user_service
from bot.utils import texts

logger = logging.getLogger(__name__)


def build_errors_router(db: Database) -> Router:
    router = Router(name="errors")

    @router.errors()
    async def on_error(event: ErrorEvent) -> bool:
        exc = event.exception
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        logger.error("Необработанная ошибка: %s", exc, exc_info=exc)

        update = event.update
        telegram_id: int | None = None
        target = None
        if update.message is not None:
            telegram_id = update.message.from_user.id if update.message.from_user else None
            target = update.message
        elif update.callback_query is not None:
            telegram_id = update.callback_query.from_user.id
            target = update.callback_query

        try:
            async with db.session_factory() as session:
                await user_service.save_error(
                    session, type(exc).__name__, str(exc), tb, telegram_id=telegram_id
                )
                await session.commit()
        except Exception:  # ошибка логирования не должна ронять бота
            logger.exception("Не удалось сохранить ошибку в БД")

        try:
            if update.callback_query is not None and target is not None:
                await target.answer()
                if target.message is not None:
                    await target.message.answer(texts.GENERIC_ERROR, reply_markup=back_to_menu_kb())
            elif update.message is not None:
                await update.message.answer(texts.GENERIC_ERROR, reply_markup=back_to_menu_kb())
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке пользователю")
        return True

    return router
