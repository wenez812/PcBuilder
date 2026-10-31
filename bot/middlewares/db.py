"""Middleware: сессия БД на каждый апдейт и регистрация пользователя."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bot.services import user_service


class DbSessionMiddleware(BaseMiddleware):
    """Открывает сессию; коммит при успехе, откат при исключении."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except BaseException:
                await session.rollback()
                raise
            await session.commit()
            return result


class UserMiddleware(BaseMiddleware):
    """Регистрирует пользователя при первом обращении и обновляет время активности."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        session: AsyncSession | None = data.get("session")
        if tg_user is not None and not tg_user.is_bot and session is not None:
            user, _ = await user_service.get_or_create_user(
                session,
                telegram_id=tg_user.id,
                username=tg_user.username,
                first_name=tg_user.first_name,
            )
            data["db_user"] = user
        return await handler(event, data)
