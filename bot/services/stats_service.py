"""Статистика и рассылка для админ-панели."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Build, ErrorLog, UsageEvent, User
from bot.models.base import utcnow

FEATURE_TITLES = {
    "start": "Запуск /start", "menu": "Меню", "help": "Помощь", "open:profile": "Профиль",
    "build": "Подбор ПК", "compat": "Проверка совместимости", "upgrade": "Улучшение ПК",
    "fps": "FPS", "prices": "Сравнение цен", "builds": "Мои сборки", "admin": "Админ-панель",
}


@dataclass(slots=True)
class Stats:
    users_total: int
    active_1d: int
    active_7d: int
    active_30d: int
    builds_total: int
    errors_total: int
    errors_24h: int
    features: list[tuple[str, int]]


async def collect_stats(session: AsyncSession) -> Stats:
    now = utcnow()

    async def active(days: int) -> int:
        return int(await session.scalar(
            select(func.count()).select_from(User).where(User.last_active_at >= now - timedelta(days=days))) or 0)

    async def count(model, *where) -> int:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    rows = await session.execute(
        select(UsageEvent.feature, func.count().label("n")).group_by(UsageEvent.feature)
        .order_by(func.count().desc()).limit(12))
    return Stats(
        users_total=await count(User), active_1d=await active(1), active_7d=await active(7),
        active_30d=await active(30), builds_total=await count(Build),
        errors_total=await count(ErrorLog), errors_24h=await count(ErrorLog, ErrorLog.created_at >= now - timedelta(days=1)),
        features=[(f, n) for f, n in rows.all()],
    )


async def recent_errors(session: AsyncSession, limit: int = 10) -> list[ErrorLog]:
    result = await session.execute(select(ErrorLog).order_by(ErrorLog.id.desc()).limit(limit))
    return list(result.scalars())


async def broadcast_recipients(session: AsyncSession) -> list[int]:
    result = await session.execute(select(User.telegram_id).where(User.notifications_enabled.is_(True)))
    return [r[0] for r in result.all()]


def feature_title(feature: str) -> str:
    if feature in FEATURE_TITLES:
        return FEATURE_TITLES[feature]
    if feature.startswith("open:"):
        return FEATURE_TITLES.get(feature.split(":", 1)[1], feature)
    return feature
