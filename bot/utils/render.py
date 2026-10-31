"""Форматирование деталей, сборок и цен в сообщения Telegram (HTML)."""
from __future__ import annotations

from html import escape

from bot.services.catalog_service import format_price
from bot.services.compatibility import CompatReport
from bot.services.parts import CATEGORIES, CATEGORY_TITLES, Part

_SPEC_LINES: dict[str, list[tuple[str, str, str]]] = {
    # категория: [(ключ, подпись, суффикс)]
    "cpu": [("socket", "Сокет", ""), ("cores", "Ядер", ""), ("threads", "Потоков", ""), ("tdp_w", "TDP", " Вт")],
    "gpu": [("vram_gb", "Память", " ГБ"), ("tgp_w", "Питание карты", " Вт"), ("pcie_gen", "PCIe", ".0")],
    "motherboard": [("socket", "Сокет", ""), ("chipset", "Чипсет", ""), ("form_factor", "Формат", ""),
                    ("memory_type", "Память", "")],
    "ram": [("type", "Тип", ""), ("capacity_gb", "Объём", " ГБ"), ("speed_mhz", "Частота", " МГц")],
    "ssd": [("capacity_gb", "Объём", " ГБ"), ("interface", "Интерфейс", "")],
    "psu": [("wattage", "Мощность", " Вт"), ("efficiency", "Сертификат", "")],
    "case": [("max_gpu_length_mm", "Видеокарта до", " мм"), ("max_cooler_height_mm", "Кулер до", " мм")],
    "cooler": [("height_mm", "Высота", " мм"), ("tdp_w", "Отвод тепла", " Вт")],
}


def specs_line(part: Part) -> str:
    chunks = []
    for key, label, suffix in _SPEC_LINES.get(part.category, []):
        value = part.specs.get(key)
        if value is not None:
            chunks.append(f"{label}: {escape(str(value))}{suffix}")
    return " · ".join(chunks) if chunks else "характеристики не заполнены"


def price_line(part: Part) -> str:
    if part.price is not None:
        shop = f" ({escape(part.price_shop)})" if part.price_shop else ""
        return f"{format_price(part.price)}{shop}"
    if part.price_stale:
        return "цена устарела"
    return "нет данных о цене"


def render_part(part: Part, reason: str | None = None) -> str:
    title = CATEGORY_TITLES.get(part.category, part.category)
    lines = [f"{title}", f"<b>{escape(part.name)}</b>", f"<i>{specs_line(part)}</i>", f"💰 {price_line(part)}"]
    if reason:
        lines.append(f"💡 {escape(reason)}")
    return "\n".join(lines)


def render_build(
    parts: dict[str, Part],
    *,
    title: str | None = None,
    reasons: dict[str, str] | None = None,
    total: int | None = None,
    report: CompatReport | None = None,
    compact: bool = False,
) -> str:
    out: list[str] = []
    if title:
        out.append(f"<b>{escape(title)}</b>\n")
    for category in CATEGORIES:
        part = parts.get(category)
        if part is None:
            continue
        if compact:
            out.append(f"{CATEGORY_TITLES[category]}: <b>{escape(part.name)}</b> — {price_line(part)}")
        else:
            out.append(render_part(part, (reasons or {}).get(category)) + "\n")
    known = sum(p.price for p in parts.values() if p.price is not None)
    unknown = sum(1 for p in parts.values() if p.price is None)
    total_value = total if total is not None else known
    line = f"\n💵 <b>Итого: {format_price(total_value)}</b>"
    if unknown:
        line += f"\n⚠️ Цены нет у {unknown} дет. — сумма неполная."
    out.append(line)
    if report is not None:
        out.append(f"{report.emoji} Совместимость: <b>{report.title}</b>")
    return "\n".join(out)


def money(value: int) -> str:
    return format_price(value)
