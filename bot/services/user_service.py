"""Работа с пользователями: регистрация, активность, настройки, профиль, удаление данных."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Build, BuildItem, ErrorLog, SelectionHistory, UsageEvent, User
from bot.models.base import utcnow


@dataclass(frozen=True, slots=True)
class ProfileData:
    telegram_id: int
    registered_at: datetime
    notifications_enabled: bool
    actions_count: int
    builds_count: int = 0


async def get_user(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession,
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> tuple[User, bool]:
    """Возвращает (пользователь, создан_ли_сейчас). Обновляет имя и время активности."""
    user = await get_user(session, telegram_id)
    now = utcnow()
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            created_at=now,
            last_active_at=now,
        )
        session.add(user)
        await session.flush()
        return user, True

    user.username = username
    user.first_name = first_name
    user.last_active_at = now
    return user, False


async def log_usage(session: AsyncSession, user: User, feature: str) -> None:
    session.add(UsageEvent(user_id=user.id, feature=feature))
    await session.flush()


async def toggle_notifications(session: AsyncSession, user: User) -> bool:
    user.notifications_enabled = not user.notifications_enabled
    await session.flush()
    return user.notifications_enabled


async def get_profile(session: AsyncSession, user: User) -> ProfileData:
    count = await session.scalar(
        select(func.count()).select_from(UsageEvent).where(UsageEvent.user_id == user.id)
    )
    return ProfileData(
        telegram_id=user.telegram_id,
        registered_at=user.created_at,
        notifications_enabled=user.notifications_enabled,
        actions_count=int(count or 0),
        builds_count=int(await session.scalar(
            select(func.count()).select_from(Build).where(Build.user_id == user.id)) or 0),
    )


async def delete_user_data(session: AsyncSession, telegram_id: int) -> bool:
    """Полностью удаляет пользователя и связанные с ним данные. True — если было что удалять."""
    user = await get_user(session, telegram_id)
    if user is None:
        return False
    # Явное удаление, чтобы не зависеть от каскадов конкретной СУБД
    build_ids = select(Build.id).where(Build.user_id == user.id)
    await session.execute(delete(BuildItem).where(BuildItem.build_id.in_(build_ids)))
    await session.execute(delete(Build).where(Build.user_id == user.id))
    await session.execute(delete(SelectionHistory).where(SelectionHistory.user_id == user.id))
    await session.execute(delete(UsageEvent).where(UsageEvent.user_id == user.id))
    await session.execute(delete(User).where(User.id == user.id))
    await session.flush()
    return True


async def save_error(
    session: AsyncSession,
    error_type: str,
    message: str,
    traceback_text: str = "",
    telegram_id: int | None = None,
) -> None:
    session.add(
        ErrorLog(
            telegram_id=telegram_id,
            error_type=error_type[:128],
            message=message[:4000],
            traceback=traceback_text[:20000],
        )
    )
    await session.flush()
