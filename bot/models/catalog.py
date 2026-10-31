"""Каталог комплектующих, предложения магазинов и база бенчмарков FPS."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.models.base import Base, UTCDateTime, utcnow


class Component(Base):
    """Одна комплектующая. Характеристики лежат в JSON, чтобы категории не требовали отдельных таблиц."""

    __tablename__ = "components"
    __table_args__ = (UniqueConstraint("category", "name", name="uq_component_category_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    brand: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160), index=True)
    condition: Mapped[str] = mapped_column(String(8), default="new")  # new | used
    specs: Mapped[dict] = mapped_column(JSON, default=dict)
    source_note: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PriceOffer(Base):
    """Предложение магазина. Цены появляются только из подключённых источников или импорта."""

    __tablename__ = "price_offers"
    __table_args__ = (UniqueConstraint("component_id", "shop", name="uq_offer_component_shop"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    component_id: Mapped[int] = mapped_column(
        ForeignKey("components.id", ondelete="CASCADE"), index=True
    )
    shop: Mapped[str] = mapped_column(String(64))
    price: Mapped[int] = mapped_column(Integer)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class FpsBenchmark(Base):
    """Реальный замер FPS с обязательным указанием источника и условий теста."""

    __tablename__ = "fps_benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cpu_id: Mapped[int] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True)
    gpu_id: Mapped[int] = mapped_column(ForeignKey("components.id", ondelete="CASCADE"), index=True)
    game: Mapped[str] = mapped_column(String(64), index=True)
    resolution: Mapped[str] = mapped_column(String(16))
    preset: Mapped[str] = mapped_column(String(32))
    avg_fps: Mapped[int] = mapped_column(Integer)
    low1_fps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ram_desc: Mapped[str | None] = mapped_column(String(64), nullable=True)
    game_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_name: Mapped[str] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
