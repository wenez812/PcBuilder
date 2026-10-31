"""Тестовые помощники. ЦЕНЫ ЗДЕСЬ СИНТЕТИЧЕСКИЕ и существуют только в тестах — в бота они не попадают."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bot.services import catalog_service
from bot.services.importers import upsert_offer
from bot.services.parts import Part

# Условная «цена по классу» — лишь бы получить правдоподобные соотношения в тестах
_BASE = {"cpu": 12000, "gpu": 20000, "motherboard": 8000, "ram": 4000, "ssd": 3000,
         "psu": 5000, "case": 3500, "cooler": 2000}


def synthetic_price(part: Part) -> int:
    base = _BASE[part.category]
    if part.category == "cpu":
        return int(5000 + part.spec("gaming", 5) * 3500 + part.spec("multi", 5) * 800)
    if part.category == "gpu":
        return int(6000 + part.spec("gaming", 5) ** 2 * 900)
    if part.category == "ram":
        return int(2000 + part.spec("capacity_gb", 16) * 150 + (1500 if part.spec("type") == "DDR5" else 0))
    if part.category == "ssd":
        return int(2000 + part.spec("capacity_gb", 500) * 3)
    if part.category == "psu":
        return int(2500 + part.spec("wattage", 500) * 7)
    if part.category == "motherboard":
        return int(5500 + (3000 if part.spec("form_factor") == "ATX" else 0)
                   + (2500 if part.spec("socket") in {"AM5", "LGA1851"} else 0))
    return base


async def seed_all(session: AsyncSession, *, with_prices: bool = True) -> None:
    await catalog_service.seed_catalog(session)
    if with_prices:
        for part in await catalog_service.list_parts(session):
            await upsert_offer(session, part.id, "ТестМагазин", synthetic_price(part), True,
                               "https://example.test/item")
            await upsert_offer(session, part.id, "ТестМагазин2", synthetic_price(part) + 700, True, None)
    await session.commit()
