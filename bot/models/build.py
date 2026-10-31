"""Сохранённые сборки пользователей и история подборов."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, UTCDateTime, utcnow


class Build(Base):
    __tablename__ = "builds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Уникальный публичный ID: используется в ссылке для друзей и в интерфейсе
    public_id: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BuildItem(Base):
    __tablename__ = "build_items"
    __table_args__ = (UniqueConstraint("build_id", "category", name="uq_build_category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(32))
    component_id: Mapped[int] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"))
    price_at_save: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SelectionHistory(Base):
    """История подборов и анализов апгрейда."""

    __tablename__ = "selection_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # build | upgrade
    summary: Mapped[str] = mapped_column(Text)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_rub: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
