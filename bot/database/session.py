"""Подключение к БД. Для перехода на PostgreSQL достаточно сменить DATABASE_URL."""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

import bot.models  # noqa: F401  (регистрация всех моделей в metadata)
from bot.models.base import Base

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, echo=echo)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

        if self.engine.dialect.name == "sqlite":
            # В SQLite внешние ключи (и каскадное удаление) выключены по умолчанию
            @event.listens_for(self.engine.sync_engine, "connect")
            def _enable_fk(dbapi_connection, _record):  # pragma: no cover - вызывается драйвером
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    async def create_all(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("База данных готова: %s", self.engine.url.render_as_string(hide_password=True))

    async def dispose(self) -> None:
        await self.engine.dispose()
