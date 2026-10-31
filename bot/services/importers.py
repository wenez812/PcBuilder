"""Импорт каталога (JSON), цен (CSV) и бенчмарков FPS (CSV) с построчной валидацией.

Ничего не придумываем: строка с ошибкой пропускается и попадает в отчёт.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models import Component, FpsBenchmark, PriceOffer
from bot.models.base import utcnow
from bot.services import catalog_service
from bot.services.parts import CATEGORIES

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 2_000_000
MAX_PRICE_RUB = 5_000_000
MAX_FPS = 2000


@dataclass(slots=True)
class ImportReport:
    added: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, where: str, message: str) -> None:
        self.skipped += 1
        self.errors.append(f"{where}: {message}")

    def summary(self, max_errors: int = 10) -> str:
        lines = [f"✅ Добавлено: {self.added}", f"🔄 Обновлено: {self.updated}", f"⏭ Пропущено: {self.skipped}"]
        if self.errors:
            lines.append("\n⚠️ Ошибки:")
            lines.extend(f"• {e}" for e in self.errors[:max_errors])
            if len(self.errors) > max_errors:
                lines.append(f"…и ещё {len(self.errors) - max_errors}")
        return "\n".join(lines)


def _decode(raw: bytes) -> str:
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"Файл больше {MAX_FILE_BYTES // 1_000_000} МБ")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251")


def _csv_rows(raw: bytes) -> list[dict[str, str]]:
    text = _decode(raw)
    sample = text[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()} for row in reader]


def _to_bool(value: str, default: bool = True) -> bool:
    if value == "":
        return default
    return value.strip().lower() in {"1", "true", "да", "yes", "y", "есть", "в наличии"}


# ------------------------------------------------------------------ каталог (JSON)

async def import_components_json(session: AsyncSession, raw: bytes) -> ImportReport:
    """Формат: [{"category": "gpu", "brand": "...", "name": "...", "condition": "new", "specs": {...}}, ...]"""
    report = ImportReport()
    try:
        data = json.loads(_decode(raw))
    except (ValueError, UnicodeDecodeError) as exc:
        report.add_error("файл", f"некорректный JSON ({exc})")
        return report
    if not isinstance(data, list):
        report.add_error("файл", "ожидается JSON-список объектов")
        return report

    for index, item in enumerate(data, start=1):
        where = f"запись {index}"
        if not isinstance(item, dict):
            report.add_error(where, "ожидается объект")
            continue
        category, name = item.get("category"), str(item.get("name") or "").strip()
        specs = item.get("specs")
        if category not in CATEGORIES:
            report.add_error(where, f"категория должна быть одной из: {', '.join(CATEGORIES)}")
        elif not name or len(name) > 160:
            report.add_error(where, "название пустое или длиннее 160 символов")
        elif not isinstance(specs, dict):
            report.add_error(where, "specs должен быть объектом")
        elif item.get("condition", "new") not in {"new", "used"}:
            report.add_error(where, "condition: new или used")
        else:
            existing = await catalog_service.find_by_name(session, category, name)
            if existing is None:
                session.add(Component(
                    category=category, brand=str(item.get("brand") or "")[:64], name=name,
                    condition=item.get("condition", "new"), specs=specs,
                    source_note=str(item.get("source") or "Импорт администратора")[:255],
                ))
                report.added += 1
            else:
                existing.specs = specs
                existing.brand = str(item.get("brand") or existing.brand)[:64]
                existing.condition = item.get("condition", existing.condition)
                existing.is_active = True
                report.updated += 1
    await session.flush()
    return report


# ------------------------------------------------------------------ цены (CSV)

PRICE_COLUMNS = ("category", "name", "shop", "price", "in_stock", "url")


async def upsert_offer(
    session: AsyncSession, component_id: int, shop: str, price: int, in_stock: bool, url: str | None
) -> bool:
    """True — создано новое предложение, False — обновлено существующее."""
    result = await session.execute(
        select(PriceOffer).where(PriceOffer.component_id == component_id, PriceOffer.shop == shop)
    )
    offer = result.scalar_one_or_none()
    if offer is None:
        session.add(PriceOffer(component_id=component_id, shop=shop, price=price,
                               in_stock=in_stock, url=url, updated_at=utcnow()))
        return True
    offer.price, offer.in_stock, offer.url, offer.updated_at = price, in_stock, url, utcnow()
    return False


def validate_offer(shop: str, price_raw: str | int | float, url: str | None) -> tuple[int | None, str | None]:
    """Возвращает (цена, ошибка)."""
    if not shop or len(shop) > 64:
        return None, "магазин не указан или длиннее 64 символов"
    try:
        price = int(float(str(price_raw).replace(" ", "").replace(" ", "").replace(",", ".")))
    except ValueError:
        return None, f"цена «{price_raw}» не является числом"
    if price <= 0 or price > MAX_PRICE_RUB:
        return None, f"цена {price} вне допустимого диапазона"
    if url and not (url.startswith("http://") or url.startswith("https://")):
        return None, "ссылка должна начинаться с http:// или https://"
    return price, None


async def import_prices_csv(session: AsyncSession, raw: bytes, *, default_shop: str | None = None) -> ImportReport:
    """Колонки: category;name;shop;price;in_stock;url. Название должно точно совпадать с каталогом."""
    report = ImportReport()
    try:
        rows = _csv_rows(raw)
    except ValueError as exc:
        report.add_error("файл", str(exc))
        return report
    if not rows:
        report.add_error("файл", "нет строк с данными")
        return report
    missing = {"name", "price"} - set(rows[0])
    if missing:
        report.add_error("заголовок", f"нет колонок: {', '.join(sorted(missing))}")
        return report

    for line, row in enumerate(rows, start=2):
        where = f"строка {line}"
        category = row.get("category") or None
        if category and category not in CATEGORIES:
            report.add_error(where, f"неизвестная категория «{category}»")
            continue
        if not row.get("price"):
            report.skipped += 1  # пустая цена в шаблоне — не ошибка
            continue
        component = await catalog_service.resolve_component(session, category, row.get("name", ""))
        if component is None:
            report.add_error(where, f"«{row.get('name', '')}» нет в каталоге")
            continue
        shop = row.get("shop") or default_shop or ""
        price, error = validate_offer(shop, row["price"], row.get("url") or None)
        if error:
            report.add_error(where, error)
            continue
        created = await upsert_offer(session, component.id, shop, price,
                                     _to_bool(row.get("in_stock", "")), row.get("url") or None)
        report.added += int(created)
        report.updated += int(not created)
    await session.flush()
    return report


def price_template_csv(components: list[tuple[str, str]]) -> bytes:
    """Шаблон для заполнения цен: все детали каталога с пустыми shop/price."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(PRICE_COLUMNS)
    for category, name in components:
        writer.writerow([category, name, "", "", "1", ""])
    return buffer.getvalue().encode("utf-8-sig")


# ------------------------------------------------------------------ бенчмарки FPS (CSV)

BENCH_COLUMNS = ("cpu", "gpu", "game", "resolution", "preset", "avg_fps", "low1_fps",
                 "ram", "game_version", "test_date", "source_name", "source_url", "notes")
RESOLUTIONS = {"1080p", "1440p", "4k"}


async def import_benchmarks_csv(session: AsyncSession, raw: bytes) -> ImportReport:
    report = ImportReport()
    try:
        rows = _csv_rows(raw)
    except ValueError as exc:
        report.add_error("файл", str(exc))
        return report
    if not rows:
        report.add_error("файл", "нет строк с данными")
        return report
    required = {"cpu", "gpu", "game", "resolution", "preset", "avg_fps", "source_name"}
    missing = required - set(rows[0])
    if missing:
        report.add_error("заголовок", f"нет колонок: {', '.join(sorted(missing))}")
        return report

    for line, row in enumerate(rows, start=2):
        where = f"строка {line}"
        cpu = await catalog_service.resolve_component(session, "cpu", row["cpu"])
        gpu = await catalog_service.resolve_component(session, "gpu", row["gpu"])
        resolution = row["resolution"].lower()
        if cpu is None or gpu is None:
            report.add_error(where, "процессор или видеокарта не найдены в каталоге")
        elif not row["game"] or not row["preset"]:
            report.add_error(where, "не указана игра или настройки графики")
        elif resolution not in RESOLUTIONS:
            report.add_error(where, "разрешение: 1080p, 1440p или 4K")
        elif not row["source_name"]:
            report.add_error(where, "источник замера обязателен")
        else:
            try:
                avg = int(float(row["avg_fps"]))
                low1 = int(float(row["low1_fps"])) if row.get("low1_fps") else None
                tested = date.fromisoformat(row["test_date"]) if row.get("test_date") else None
            except ValueError:
                report.add_error(where, "FPS должен быть числом, дата — ГГГГ-ММ-ДД")
                continue
            if not (0 < avg <= MAX_FPS) or (low1 is not None and not (0 < low1 <= avg)):
                report.add_error(where, "FPS вне диапазона (1% Low не может быть выше среднего)")
                continue
            if row.get("source_url") and not row["source_url"].startswith(("http://", "https://")):
                report.add_error(where, "ссылка на источник должна начинаться с http(s)://")
                continue
            session.add(FpsBenchmark(
                cpu_id=cpu.id, gpu_id=gpu.id, game=row["game"][:64],
                resolution="4K" if resolution == "4k" else resolution, preset=row["preset"][:32],
                avg_fps=avg, low1_fps=low1, ram_desc=row.get("ram") or None,
                game_version=row.get("game_version") or None, test_date=tested,
                source_name=row["source_name"][:128], source_url=row.get("source_url") or None,
                notes=row.get("notes") or None,
            ))
            report.added += 1
    await session.flush()
    return report
