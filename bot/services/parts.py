"""Лёгкие неизменяемые объекты для чистой логики (совместимость, подбор, апгрейд)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

CATEGORIES: tuple[str, ...] = ("cpu", "gpu", "motherboard", "ram", "ssd", "psu", "case", "cooler")

CATEGORY_TITLES: dict[str, str] = {
    "cpu": "🧠 Процессор",
    "gpu": "🎮 Видеокарта",
    "motherboard": "🧩 Материнская плата",
    "ram": "💾 Оперативная память",
    "ssd": "⚡ SSD",
    "psu": "🔌 Блок питания",
    "case": "🗄 Корпус",
    "cooler": "❄️ Охлаждение CPU",
}

CATEGORY_SHORT: dict[str, str] = {
    "cpu": "Процессор",
    "gpu": "Видеокарта",
    "motherboard": "Материнская плата",
    "ram": "Оперативная память",
    "ssd": "SSD",
    "psu": "Блок питания",
    "case": "Корпус",
    "cooler": "Охлаждение",
}


@dataclass(frozen=True, slots=True)
class Part:
    id: int
    category: str
    name: str
    specs: Mapping[str, Any] = field(default_factory=dict)
    brand: str = ""
    condition: str = "new"
    price: int | None = None            # лучшая цена среди предложений «в наличии»
    price_shop: str | None = None
    price_updated_at: datetime | None = None
    in_stock: bool = False
    offers_count: int = 0
    price_stale: bool = False

    def spec(self, key: str, default: Any = None) -> Any:
        value = self.specs.get(key, default)
        return default if value is None else value

    def has(self, *keys: str) -> bool:
        return all(self.specs.get(k) is not None for k in keys)

    def __hash__(self) -> int:
        return hash((self.id, self.category))


def cpu_max_power(cpu: Part) -> int | None:
    return cpu.spec("max_power_w") or cpu.spec("tdp_w")
