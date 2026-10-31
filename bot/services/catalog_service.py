"""Каталог: загрузка деталей из БД вместе с лучшей актуальной ценой, поиск, посев каталога."""
from __future__ import annotations

import difflib
import logging
import re
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Component, PriceOffer
from bot.models.base import utcnow
from bot.services.parts import CATEGORIES, Part
from bot.services.seed_data import SEED_COMPONENTS, SOURCE_NOTE

logger = logging.getLogger(__name__)

PRICE_MAX_AGE_DAYS = 7


def normalize(text: str) -> str:
    """Для сопоставления названий: нижний регистр, без знаков и лишних пробелов."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_part(row: Component, offers: list[PriceOffer], max_age_days: int) -> Part:
    fresh_limit = utcnow() - timedelta(days=max_age_days)
    in_stock = [o for o in offers if o.in_stock]
    fresh = [o for o in in_stock if o.updated_at >= fresh_limit]
    best = min(fresh, key=lambda o: o.price) if fresh else None
    return Part(
        id=row.id,
        category=row.category,
        name=row.name,
        brand=row.brand,
        condition=row.condition,
        specs=dict(row.specs or {}),
        price=best.price if best else None,
        price_shop=best.shop if best else None,
        price_updated_at=best.updated_at if best else None,
        in_stock=best is not None,
        offers_count=len({o.shop for o in fresh}),
        price_stale=bool(in_stock) and best is None,
    )


async def _offers_by_component(
    session: AsyncSession, component_ids: list[int] | None = None
) -> dict[int, list[PriceOffer]]:
    query = select(PriceOffer)
    if component_ids is not None:
        query = query.where(PriceOffer.component_id.in_(component_ids))
    grouped: dict[int, list[PriceOffer]] = defaultdict(list)
    for offer in (await session.execute(query)).scalars():
        grouped[offer.component_id].append(offer)
    return grouped


async def list_parts(
    session: AsyncSession,
    category: str | None = None,
    *,
    only_active: bool = True,
    max_age_days: int = PRICE_MAX_AGE_DAYS,
) -> list[Part]:
    query = select(Component).order_by(Component.category, Component.name)
    if category:
        query = query.where(Component.category == category)
    if only_active:
        query = query.where(Component.is_active.is_(True))
    rows = (await session.execute(query)).scalars().all()
    offers = await _offers_by_component(session, [r.id for r in rows])
    return [_to_part(r, offers.get(r.id, []), max_age_days) for r in rows]


async def get_part(
    session: AsyncSession, part_id: int, *, max_age_days: int = PRICE_MAX_AGE_DAYS
) -> Part | None:
    row = await session.get(Component, part_id)
    if row is None:
        return None
    offers = await _offers_by_component(session, [row.id])
    return _to_part(row, offers.get(row.id, []), max_age_days)


async def get_parts(session: AsyncSession, ids: list[int]) -> dict[int, Part]:
    if not ids:
        return {}
    rows = (await session.execute(select(Component).where(Component.id.in_(ids)))).scalars().all()
    offers = await _offers_by_component(session, [r.id for r in rows])
    return {r.id: _to_part(r, offers.get(r.id, []), PRICE_MAX_AGE_DAYS) for r in rows}


async def get_offers(session: AsyncSession, part_id: int) -> list[PriceOffer]:
    result = await session.execute(
        select(PriceOffer).where(PriceOffer.component_id == part_id).order_by(PriceOffer.price)
    )
    return list(result.scalars())


def search_parts(parts: list[Part], query: str, limit: int = 8) -> list[Part]:
    """Нечёткий поиск по названию: сначала вхождение всех слов, затем похожесть строк."""
    q = normalize(query)
    if not q:
        return []
    tokens = q.split()
    exact = [p for p in parts if all(t in normalize(p.name) for t in tokens)]
    if exact:
        return sorted(exact, key=lambda p: len(p.name))[:limit]
    scored = [
        (difflib.SequenceMatcher(None, q, normalize(p.name)).ratio(), p) for p in parts
    ]
    scored = [item for item in scored if item[0] >= 0.5]
    scored.sort(key=lambda item: -item[0])
    return [p for _, p in scored[:limit]]


async def seed_catalog(session: AsyncSession) -> int:
    """Добавляет недостающие детали стартового каталога. Существующие записи не трогает."""
    existing = {
        (c, n) for c, n in (await session.execute(select(Component.category, Component.name))).all()
    }
    added = 0
    for entry in SEED_COMPONENTS:
        key = (entry["category"], entry["name"])
        if key in existing:
            continue
        session.add(
            Component(
                category=entry["category"],
                brand=entry["brand"],
                name=entry["name"],
                condition=entry.get("condition", "new"),
                specs=entry["specs"],
                source_note=SOURCE_NOTE,
            )
        )
        added += 1
    await session.flush()
    if added:
        logger.info("В каталог добавлено %d деталей", added)
    return added


async def catalog_counts(session: AsyncSession) -> dict[str, int]:
    rows = await session.execute(
        select(Component.category, func.count()).where(Component.is_active.is_(True)).group_by(Component.category)
    )
    counts = {c: 0 for c in CATEGORIES}
    counts.update({c: n for c, n in rows.all()})
    return counts


async def price_coverage(session: AsyncSession) -> tuple[int, int, int]:
    """(активных деталей, деталей со свежей ценой, деталей с устаревшей ценой)."""
    parts = await list_parts(session)
    priced = sum(1 for p in parts if p.price is not None)
    stale = sum(1 for p in parts if p.price_stale)
    return len(parts), priced, stale


async def find_by_name(session: AsyncSession, category: str, name: str) -> Component | None:
    result = await session.execute(
        select(Component).where(Component.category == category, Component.name == name)
    )
    return result.scalar_one_or_none()


async def resolve_component(session: AsyncSession, category: str | None, name: str) -> Component | None:
    """Сопоставление названия из файла/фида с деталью каталога (без нечёткого угадывания)."""
    query = select(Component)
    if category:
        query = query.where(Component.category == category)
    target = normalize(name)
    for row in (await session.execute(query)).scalars():
        if normalize(row.name) == target:
            return row
    return None


def format_price(value: int | None) -> str:
    if value is None:
        return "нет данных"
    return f"{value:,}".replace(",", " ") + " ₽"
