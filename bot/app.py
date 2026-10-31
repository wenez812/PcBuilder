"""Сборка Dispatcher: middleware, роутеры, обработчик ошибок."""
from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.database import Database
from bot.handlers import build_main_router
from bot.integrations import build_sources
from bot.handlers.errors import build_errors_router
from bot.middlewares import DbSessionMiddleware, RateLimiter, ThrottlingMiddleware, UserMiddleware
from config import Settings


def build_dispatcher(settings: Settings, db: Database) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp["settings"] = settings
    dp["db"] = db
    dp["price_sources"] = build_sources(settings.price_feed_url, settings.price_feed_token, settings.price_feed_shop)

    limiter = RateLimiter(
        max_actions=settings.throttle_max_actions,
        window=settings.throttle_window,
        cooldown=settings.throttle_cooldown,
    )
    throttling = ThrottlingMiddleware(limiter)
    # Порядок: сессия БД (update) -> антиспам -> регистрация пользователя (message/callback)
    dp.message.outer_middleware(throttling)
    dp.callback_query.outer_middleware(throttling)

    dp.update.outer_middleware(DbSessionMiddleware(db.session_factory))
    dp.message.outer_middleware(UserMiddleware())
    dp.callback_query.outer_middleware(UserMiddleware())

    dp.include_router(build_errors_router(db))
    dp.include_router(build_main_router())
    return dp
