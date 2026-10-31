from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, UTCDateTime, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class UsageEvent(Base):
    """Одно использование функции бота (для статистики и истории)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    feature: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class ErrorLog(Base):
    """Необработанные ошибки — для раздела «Просмотр ошибок» в админ-панели."""

    __tablename__ = "error_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_type: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    traceback: Mapped[str] = mapped_column(Text, default="")
