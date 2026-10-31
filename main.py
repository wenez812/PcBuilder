"""Точка входа: python main.py"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.app import build_dispatcher
from bot.database import Database
from bot.services import catalog_service, price_updater
from bot.utils.logging_setup import setup_logging
from config import ConfigError, load_settings

logger = logging.getLogger("pc_builder")


async def _refresh_job(db: Database, sources) -> None:
    report = await price_updater.refresh_prices(db, sources)
    logger.info("Обновление цен: %s", report.summary().replace("\n", "; "))


async def run() -> None:
    settings = load_settings()
    setup_logging(settings.log_level, settings.log_dir)

    db = Database(settings.database_url)
    await db.create_all()
    async with db.session_factory() as session:
        await catalog_service.seed_catalog(session)
        await session.commit()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = build_dispatcher(settings, db)

    scheduler: AsyncIOScheduler | None = None
    if settings.price_feed_url and settings.price_refresh_hours > 0:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            _refresh_job, "interval", hours=settings.price_refresh_hours, args=[db, dp["price_sources"]],
            id="refresh_prices", max_instances=1, coalesce=True,
        )
        scheduler.start()
        logger.info("Автообновление цен из фида каждые %s ч.", settings.price_refresh_hours)
    elif not settings.price_feed_url:
        logger.info("PRICE_FEED_URL не задан: цены загружаются только вручную (CSV в админ-панели)")

    try:
        me = await bot.get_me()
        logger.info("Бот запущен: @%s (админов: %d)", me.username, len(settings.admin_ids))
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Запустить бота"),
                BotCommand(command="menu", description="Главное меню"),
                BotCommand(command="help", description="Помощь"),
            ]
        )
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        await db.dispose()
        logger.info("Бот остановлен")


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"❌ Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)
    except TelegramUnauthorizedError:
        print("❌ Telegram отклонил токен (Unauthorized). Проверьте BOT_TOKEN в .env или получите новый у @BotFather.",
              file=sys.stderr)
        sys.exit(1)
    except TelegramNetworkError as exc:
        print(f"❌ Нет связи с Telegram: {exc}\nПроверьте интернет, VPN/прокси и доступность api.telegram.org.",
              file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
