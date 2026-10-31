"""Алгоритм автоматического подбора ПК из каталога. Чистая логика, без БД.

Шаги (соответствуют ТЗ):
1. фильтруем каталог: только детали с актуальной ценой и наличием, предпочтения и б/у;
2. для каждой пары «процессор + видеокарта» оцениваем производительность под задачу;
3. под пару подбираем платформу (плата, память, SSD, корпус, кулер) и блок питания,
   каждую деталь проверяем движком совместимости;
4. отбрасываем всё, что дороже бюджета или имеет несовместимость / недостаток данных;
5. оставшиеся конфигурации оцениваем по критериям (производительность, цена, запас
   на апгрейд, тишина, энергопотребление, доступность) с весами по выбранному приоритету.

Классы производительности (`gaming`, `multi`, `compute`) — редакторская шкала каталога 0–10,
а не FPS. Для реального FPS есть отдельный модуль с бенчмарками.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import product

from bot.services.compatibility import (
    PSU_HEADROOM,
    CompatReport,
    Severity,
    check_compatibility,
    estimate_load_w,
)
from bot.services.parts import CATEGORIES, CATEGORY_SHORT, Part, cpu_max_power

logger = logging.getLogger(__name__)

PURPOSES = {
    "gaming": "Игры",
    "work": "Работа",
    "programming": "Программирование",
    "video": "Монтаж видео",
    "3d": "3D-моделирование",
    "universal": "Универсальный ПК",
}
PRIORITIES = {
    "fps": "Максимальный FPS",
    "quiet": "Тихая работа",
    "price": "Минимальная цена",
    "upgrade": "Возможность модернизации",
    "balanced": "Сбалансированная сборка",
}
GAMES = ["CS2", "Dota 2", "GTA V", "Cyberpunk 2077", "Fortnite", "Minecraft", "Другие игры"]

# Доля влияния процессора в играх (остальное — видеокарта). Зависит от игры и разрешения
GAME_CPU_WEIGHT = {
    "CS2": 0.65, "Dota 2": 0.60, "GTA V": 0.55, "Cyberpunk 2077": 0.30,
    "Fortnite": 0.50, "Minecraft": 0.70, "Другие игры": 0.50,
}
RESOLUTION_CPU_SHIFT = {"1080p": 0.0, "1440p": -0.10, "4K": -0.20}
RESOLUTION_MIN_VRAM = {"1080p": 0, "1440p": 8, "4K": 12}
RESOLUTION_GOOD_VRAM = {"1080p": 8, "1440p": 12, "4K": 16}

PURPOSE_CPU_WEIGHT = {"work": 0.65, "programming": 0.80, "video": 0.55, "3d": 0.40}
IDEAL_RAM_GB = {"gaming": 16, "work": 32, "programming": 32, "video": 32, "3d": 32, "universal": 32}
PLATFORM_FUTURE = {"AM5": 1.0, "LGA1851": 0.8, "LGA1700": 0.3, "AM4": 0.3}

WEIGHTS: dict[str, dict[str, float]] = {
    "fps":      {"perf": 0.70, "value": 0.10, "upgrade": 0.05, "quiet": 0.05, "power": 0.05, "avail": 0.05},
    "quiet":    {"perf": 0.25, "value": 0.10, "upgrade": 0.05, "quiet": 0.35, "power": 0.20, "avail": 0.05},
    "price":    {"perf": 0.25, "value": 0.60, "upgrade": 0.00, "quiet": 0.00, "power": 0.10, "avail": 0.05},
    "upgrade":  {"perf": 0.35, "value": 0.10, "upgrade": 0.35, "quiet": 0.05, "power": 0.05, "avail": 0.10},
    "balanced": {"perf": 0.40, "value": 0.25, "upgrade": 0.10, "quiet": 0.10, "power": 0.10, "avail": 0.05},
}


@dataclass(frozen=True, slots=True)
class BuildRequest:
    budget: int
    purpose: str = "gaming"
    games: tuple[str, ...] = ()
    resolution: str = "1080p"
    priority: str = "balanced"
    cpu_vendor: str = "any"      # any | amd | intel
    gpu_vendor: str = "any"      # any | nvidia | amd
    allow_used: bool = False
    min_ram_gb: int | None = None

    def to_params(self) -> dict:
        return {
            "budget": self.budget, "purpose": self.purpose, "games": list(self.games),
            "resolution": self.resolution, "priority": self.priority, "cpu_vendor": self.cpu_vendor,
            "gpu_vendor": self.gpu_vendor, "allow_used": self.allow_used, "min_ram_gb": self.min_ram_gb,
        }

    @classmethod
    def from_params(cls, data: dict) -> "BuildRequest":
        return cls(
            budget=int(data["budget"]), purpose=data.get("purpose", "gaming"),
            games=tuple(data.get("games") or ()), resolution=data.get("resolution", "1080p"),
            priority=data.get("priority", "balanced"), cpu_vendor=data.get("cpu_vendor", "any"),
            gpu_vendor=data.get("gpu_vendor", "any"), allow_used=bool(data.get("allow_used", False)),
            min_ram_gb=data.get("min_ram_gb"),
        )


@dataclass(slots=True)
class BuildOption:
    label: str
    parts: dict[str, Part]
    total: int
    perf: float
    score: float
    report: CompatReport
    reasons: dict[str, str] = field(default_factory=dict)

    @property
    def part_ids(self) -> dict[str, int]:
        return {cat: p.id for cat, p in self.parts.items()}


@dataclass(slots=True)
class BuildResult:
    status: str                       # ok | relaxed | over_budget | no_data
    options: list[BuildOption] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    min_total: int | None = None      # цена самой дешёвой собираемой конфигурации
    missing_data: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- оценка производительности

def _weighted_harmonic(cpu_val: float, gpu_val: float, cpu_weight: float) -> float:
    """Узкое звено тянет итог вниз: дисбаланс процессора и видеокарты наказывается."""
    cpu_val, gpu_val = max(cpu_val, 0.1), max(gpu_val, 0.1)
    return 1.0 / (cpu_weight / cpu_val + (1.0 - cpu_weight) / gpu_val)


def game_cpu_weight(games: tuple[str, ...], resolution: str) -> float:
    names = games or ("Другие игры",)
    base = sum(GAME_CPU_WEIGHT.get(g, 0.5) for g in names) / len(names)
    return min(0.8, max(0.15, base + RESOLUTION_CPU_SHIFT.get(resolution, 0.0)))


def vram_factor(gpu: Part, purpose: str, resolution: str) -> float:
    vram = gpu.spec("vram_gb")
    if vram is None:
        return 0.9
    if purpose in {"gaming", "universal"}:
        good = RESOLUTION_GOOD_VRAM.get(resolution, 8)
    elif purpose in {"video", "3d"}:
        good = 12
    else:
        good = 8
    return 1.0 if vram >= good else max(0.8, 1.0 - 0.05 * (good - vram) / 2)


def ram_factor(ram_gb: int, purpose: str, resolution: str) -> float:
    ideal = IDEAL_RAM_GB.get(purpose, 16)
    if purpose == "gaming" and resolution == "4K":
        ideal = 32
    return min(1.0, (ram_gb / ideal) ** 0.5)


def performance(cpu: Part, gpu: Part, purpose: str, games: tuple[str, ...] = (),
                resolution: str = "1080p", ram_gb: int | None = None) -> float:
    """Балл 0–10 в шкале каталога. Это не FPS."""
    gaming = _weighted_harmonic(cpu.spec("gaming", 1.0), gpu.spec("gaming", 1.0),
                                game_cpu_weight(games, resolution))
    if purpose == "gaming":
        score = gaming
    elif purpose == "universal":
        work = _weighted_harmonic(cpu.spec("multi", 1.0), gpu.spec("compute", 1.0), 0.6)
        score = 0.6 * gaming + 0.4 * work
    else:
        score = _weighted_harmonic(cpu.spec("multi", 1.0), gpu.spec("compute", 1.0),
                                   PURPOSE_CPU_WEIGHT.get(purpose, 0.6))
    score *= vram_factor(gpu, purpose, resolution)
    if ram_gb is not None:
        score *= ram_factor(ram_gb, purpose, resolution)
    return score


# ----------------------------------------------------------------- фильтрация кандидатов

def _vendor_ok(part: Part, wanted: str) -> bool:
    if wanted == "any":
        return True
    return part.brand.lower() == wanted


def filter_candidates(parts: list[Part], req: BuildRequest, *, strict_prefs: bool = True) -> dict[str, list[Part]]:
    result: dict[str, list[Part]] = {c: [] for c in CATEGORIES}
    for p in parts:
        if p.category not in result or p.price is None or not p.in_stock:
            continue
        if p.condition == "used" and not req.allow_used:
            continue
        if strict_prefs:
            if p.category == "cpu" and not _vendor_ok(p, req.cpu_vendor):
                continue
            if p.category == "gpu" and not _vendor_ok(p, req.gpu_vendor):
                continue
        if p.category == "gpu":
            min_vram = RESOLUTION_MIN_VRAM.get(req.resolution, 0) if req.purpose in {"gaming", "universal"} else 0
            if (p.spec("vram_gb") or 0) < min_vram:
                continue
        result[p.category].append(p)
    for items in result.values():
        items.sort(key=lambda x: (x.price, x.name))
    return result


MAX_SHORTLIST = 30


def shortlist(items: list[Part], metric, limit: int = MAX_SHORTLIST) -> list[Part]:
    """Для больших каталогов: оставляем недоминируемые по «цена/класс» детали и не более `limit` штук."""
    if len(items) <= limit:
        return items
    kept: list[Part] = []
    best = float("-inf")
    for part in sorted(items, key=lambda p: (p.price, -metric(p))):
        value = metric(part)
        if value > best + 1e-9:
            kept.append(part)
            best = value
    if len(kept) > limit:
        step = (len(kept) - 1) / (limit - 1)
        kept = [kept[round(i * step)] for i in range(limit)]
    return kept


def _usable(report: CompatReport) -> bool:
    """В сборку берём только подтверждённо совместимое: без 🔴 и без ⚪."""
    return not any(i.severity in (Severity.BAD, Severity.UNKNOWN) for i in report.issues)


# ----------------------------------------------------------------- сборка платформы

@dataclass(slots=True)
class _Platform:
    parts: dict[str, Part]
    cost: int


def _ram_targets(req: BuildRequest) -> list[int]:
    ideal = IDEAL_RAM_GB.get(req.purpose, 16)
    sizes = {16, 32}
    if req.purpose in {"video", "3d"} and req.budget >= 150_000:
        sizes.add(64)
    if req.min_ram_gb:
        sizes = {s for s in sizes | {req.min_ram_gb} if s >= req.min_ram_gb}
    if req.purpose in {"work", "programming", "video", "3d"}:
        sizes = {s for s in sizes if s >= 32} or {max(sizes)}
    del ideal
    return sorted(sizes)


def _ssd_targets(req: BuildRequest) -> list[int]:
    if req.purpose in {"work", "programming", "video", "3d"}:
        return [1000, 2000] if req.budget >= 120_000 else [1000]
    return [500, 1000]


def _build_platform(cpu: Part, cands: dict[str, list[Part]], ram_gb: int, ssd_gb: int) -> _Platform | None:
    """Самая дешёвая подтверждённо совместимая платформа под процессор."""
    boards = []
    for b in cands["motherboard"]:
        if b.spec("socket") != cpu.spec("socket"):
            continue
        pair = check_compatibility({"cpu": cpu, "motherboard": b})
        if _usable(pair):
            boards.append((len(pair.by_severity(Severity.WARN)), b))
    boards.sort(key=lambda x: (x[0], x[1].price))

    for _, board in boards[:4]:
        ram = next((r for r in cands["ram"]
                    if r.spec("capacity_gb") == ram_gb
                    and _usable(check_compatibility({"cpu": cpu, "motherboard": board, "ram": r}))), None)
        ssd = next((s for s in cands["ssd"]
                    if s.spec("capacity_gb") == ssd_gb
                    and _usable(check_compatibility({"motherboard": board, "ssd": s}))), None)
        if ram is None or ssd is None:
            continue
        for case in cands["case"]:
            if not _usable(check_compatibility({"motherboard": board, "case": case})):
                continue
            cooler = next((c for c in cands["cooler"]
                           if _usable(check_compatibility({"cpu": cpu, "cooler": c, "case": case}))
                           and not check_compatibility({"cpu": cpu, "cooler": c}).by_severity(Severity.WARN)), None)
            if cooler is None:
                continue
            parts = {"cpu": cpu, "motherboard": board, "ram": ram, "ssd": ssd, "case": case, "cooler": cooler}
            return _Platform(parts, sum(p.price for p in parts.values()))
    return None


def _pick_psu(cpu: Part, gpu: Part, cands: dict[str, list[Part]]) -> Part | None:
    load = estimate_load_w(cpu, gpu)
    if load is None:
        return None
    need = load * PSU_HEADROOM
    rec = gpu.spec("rec_psu_w") or 0
    for psu in cands["psu"]:
        wattage = psu.spec("wattage")
        if wattage is not None and wattage >= max(need, rec):
            if not check_compatibility({"cpu": cpu, "gpu": gpu, "psu": psu}).by_severity(Severity.UNKNOWN):
                return psu
    return None


# ----------------------------------------------------------------- метрики и оценка

def _upgrade_potential(parts: dict[str, Part], load: int) -> float:
    board, ram, psu = parts["motherboard"], parts["ram"], parts["psu"]
    socket = PLATFORM_FUTURE.get(board.spec("socket"), 0.2)
    slots, modules = board.spec("ram_slots", 2), ram.spec("modules", 2)
    free_slots = max(0, slots - modules) / max(slots, 1)
    headroom = min(1.0, max(0.0, (psu.spec("wattage", 0) / max(load, 1) - 1.0) / 1.0))
    return 0.5 * socket + 0.2 * free_slots + 0.3 * headroom


def _quietness(parts: dict[str, Part]) -> float:
    cpu, gpu, cooler = parts["cpu"], parts["gpu"], parts["cooler"]
    heat = (cpu_max_power(cpu) or 150) + (gpu.spec("tgp_w") or 250)
    cooler_margin = min(2.0, (cooler.spec("tdp_w", 100) / max(cpu_max_power(cpu) or 100, 1)))
    return max(0.0, 1.0 - heat / 700.0) * 0.6 + min(1.0, cooler_margin / 2.0) * 0.4


def _availability(parts: dict[str, Part]) -> float:
    return sum(min(1.0, p.offers_count / 2) for p in parts.values()) / len(parts)


def _normalize(values: list[float]) -> list[float]:
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return [1.0] * len(values)
    return [(v - low) / (high - low) for v in values]


@dataclass(slots=True)
class _Candidate:
    parts: dict[str, Part]
    total: int
    perf: float
    load: int
    metrics: dict[str, float] = field(default_factory=dict)
    score: float = 0.0


def _enumerate(cands: dict[str, list[Part]], req: BuildRequest, budget: int | None) -> list[_Candidate]:
    """Все подтверждённо совместимые конфигурации не дороже budget (None — без ограничения)."""
    results: list[_Candidate] = []
    cpus = shortlist(cands["cpu"], lambda p: p.spec("gaming", 0) + p.spec("multi", 0))
    gpus = shortlist(cands["gpu"], lambda p: p.spec("gaming", 0) + p.spec("compute", 0))
    if not (cpus and gpus):
        return results

    platforms: dict[tuple[int, int, int], _Platform | None] = {}
    for cpu in cpus:
        for ram_gb, ssd_gb in product(_ram_targets(req), _ssd_targets(req)):
            platforms[(cpu.id, ram_gb, ssd_gb)] = _build_platform(cpu, cands, ram_gb, ssd_gb)

    for cpu, gpu in product(cpus, gpus):
        load = estimate_load_w(cpu, gpu)
        if load is None:
            continue
        psu = _pick_psu(cpu, gpu, cands)
        if psu is None:
            continue
        for ram_gb, ssd_gb in product(_ram_targets(req), _ssd_targets(req)):
            platform = platforms.get((cpu.id, ram_gb, ssd_gb))
            if platform is None:
                continue
            total = platform.cost + gpu.price + psu.price
            if budget is not None and total > budget:
                continue
            parts = {**platform.parts, "gpu": gpu, "psu": psu}
            report = check_compatibility(parts)
            if not _usable(report):
                continue
            perf = performance(cpu, gpu, req.purpose, req.games, req.resolution, ram_gb)
            results.append(_Candidate(parts, total, perf, load))
    return results


def _score(candidates: list[_Candidate], priority: str) -> None:
    weights = WEIGHTS.get(priority, WEIGHTS["balanced"])
    perf = _normalize([c.perf for c in candidates])
    value = _normalize([c.perf / c.total for c in candidates])
    upgrade = _normalize([_upgrade_potential(c.parts, c.load) for c in candidates])
    quiet = _normalize([_quietness(c.parts) for c in candidates])
    power = _normalize([-c.load for c in candidates])
    avail = _normalize([_availability(c.parts) for c in candidates])
    for i, c in enumerate(candidates):
        c.metrics = {"perf": perf[i], "value": value[i], "upgrade": upgrade[i],
                     "quiet": quiet[i], "power": power[i], "avail": avail[i]}
        c.score = sum(weights[k] * c.metrics[k] for k in weights)


# ----------------------------------------------------------------- объяснения выбора

def explain(category: str, part: Part, parts: dict[str, Part], req: BuildRequest) -> str:
    """Причина выбора строится только из характеристик каталога."""
    if category == "cpu":
        cores, threads = part.spec("cores"), part.spec("threads")
        base = f"{cores} ядер / {threads} потоков" if cores and threads else "характеристики в каталоге"
        if req.purpose in {"gaming", "universal"}:
            return f"Подходит под ваши игры и баланс с видеокартой; {base}, сокет {part.spec('socket')}."
        return f"{base} — основной ресурс для задач «{PURPOSES[req.purpose].lower()}»."
    if category == "gpu":
        vram = part.spec("vram_gb")
        extra = ""
        if vram is not None:
            need = RESOLUTION_GOOD_VRAM.get(req.resolution, 8)
            extra = (f" Видеопамяти {vram} ГБ хватает для {req.resolution}." if vram >= need
                     else f" Видеопамяти {vram} ГБ — на пределе для {req.resolution}, в тяжёлых играх снижайте текстуры.")
        return f"Лучший вариант по баланс «цена/производительность» в вашем бюджете.{extra}"
    if category == "motherboard":
        return (f"Чипсет {part.spec('chipset')}, {part.spec('memory_type')}, форм-фактор {part.spec('form_factor')}: "
                f"совместима с процессором, без лишней переплаты за функции.")
    if category == "ram":
        return (f"{part.spec('capacity_gb')} ГБ {part.spec('type')} — "
                f"{'достаточно для игр' if req.purpose == 'gaming' else 'комфортный объём для рабочих задач'}; "
                f"двухканальный комплект ({part.spec('modules')} модуля).")
    if category == "ssd":
        return f"{part.spec('capacity_gb')} ГБ {part.spec('interface')}: быстрая загрузка системы и игр."
    if category == "psu":
        load = estimate_load_w(parts["cpu"], parts["gpu"])
        return (f"Расчётная нагрузка ~{load} Вт, мощность {part.spec('wattage')} Вт даёт запас "
                f"не менее 25%; сертификат {part.spec('efficiency')}.")
    if category == "case":
        return (f"Поддерживает плату {parts['motherboard'].spec('form_factor')}, видеокарты до "
                f"{part.spec('max_gpu_length_mm')} мм, кулеры до {part.spec('max_cooler_height_mm')} мм.")
    if category == "cooler":
        return (f"Отводит до {part.spec('tdp_w')} Вт (процессор до {cpu_max_power(parts['cpu'])} Вт), "
                f"высота {part.spec('height_mm')} мм подходит корпусу.")
    return ""


def _make_option(label: str, cand: _Candidate, req: BuildRequest) -> BuildOption:
    return BuildOption(
        label=label, parts=cand.parts, total=cand.total, perf=cand.perf, score=cand.score,
        report=check_compatibility(cand.parts),
        reasons={cat: explain(cat, p, cand.parts, req) for cat, p in cand.parts.items()},
    )


# ----------------------------------------------------------------- публичный интерфейс

def coverage_problems(parts: list[Part]) -> list[str]:
    """Категории, для которых нет ни одной детали с актуальной ценой."""
    priced = {c: 0 for c in CATEGORIES}
    for p in parts:
        if p.price is not None and p.in_stock and p.category in priced:
            priced[p.category] += 1
    return [CATEGORY_SHORT[c] for c, n in priced.items() if n == 0]


def select_builds(all_parts: list[Part], req: BuildRequest) -> BuildResult:
    missing = coverage_problems([p for p in all_parts if p.condition == "new" or req.allow_used])
    if missing:
        return BuildResult(
            status="no_data", missing_data=missing,
            notes=["Для подбора нужны актуальные цены на детали каждой категории. "
                   "Сейчас цен нет для: " + ", ".join(m.lower() for m in missing) + "."],
        )

    notes: list[str] = []
    strict = filter_candidates(all_parts, req, strict_prefs=True)
    found = _enumerate(strict, req, req.budget)
    status = "ok"

    if not found and (req.cpu_vendor != "any" or req.gpu_vendor != "any"):
        relaxed = filter_candidates(all_parts, req, strict_prefs=False)
        found = _enumerate(relaxed, req, req.budget)
        if found:
            status = "relaxed"
            strict = relaxed
            notes.append("С выбранными производителями собрать ПК в бюджет не получилось, "
                         "поэтому я ослабил это условие. Бюджет не превышен.")

    if not found:
        unlimited = _enumerate(filter_candidates(all_parts, req, strict_prefs=False), req, None)
        if not unlimited:
            return BuildResult(status="no_data", notes=[
                "В каталоге нет подтверждённо совместимой комбинации деталей с ценами. "
                "Нужно расширить каталог или обновить цены."])
        _score(unlimited, "price")
        cheapest = min(unlimited, key=lambda c: c.total)
        option = _make_option("💰 Минимально возможная сборка", cheapest, req)
        notes.append(
            f"В бюджет {req.budget:,} ₽ собрать полноценный ПК по каталогу не удаётся. "
            f"Самая дешёвая подтверждённо совместимая конфигурация стоит {cheapest.total:,} ₽ "
            f"(на {cheapest.total - req.budget:,} ₽ больше). Решение за вами: "
            f"увеличить бюджет или разрешить б/у детали.".replace(",", " "))
        return BuildResult(status="over_budget", options=[option], notes=notes, min_total=cheapest.total)

    _score(found, req.priority)
    best = max(found, key=lambda c: (c.score, c.perf))
    options = [_make_option("⭐ Рекомендуемая", best, req)]

    perf_floor = best.perf * 0.8
    cheaper = [c for c in found if c.total < best.total and c.perf >= perf_floor]
    if cheaper:
        pick = min(cheaper, key=lambda c: (c.total, -c.perf))
        options.append(_make_option("💰 Дешевле", pick, req))
    stronger = [c for c in found if c.perf > best.perf * 1.03]
    if stronger:
        pick = max(stronger, key=lambda c: (c.perf, -c.total))
        if all(pick.parts != o.parts for o in options):
            options.append(_make_option("🚀 Мощнее", pick, req))

    cheapest_total = min(c.total for c in found)
    return BuildResult(status=status, options=options, notes=notes, min_total=cheapest_total)
