"""Анализ модернизации ПК. Чистая логика, без БД.

Три варианта (бюджетный / оптимальный / максимальный) строятся перебором наборов замен из каталога.
Каждая итоговая конфигурация проходит проверку совместимости. Оценка эффекта — по классам
производительности каталога (это не FPS), поэтому в тексте всегда есть соответствующая оговорка.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from bot.services.builder import performance, vram_factor
from bot.services.compatibility import (
    PSU_HEADROOM,
    CompatReport,
    Severity,
    check_compatibility,
    estimate_load_w,
)
from bot.services.parts import CATEGORY_SHORT, Part, cpu_max_power

GOALS = {
    "fps": "FPS в играх",
    "work": "Производительность в работе",
    "temps": "Температуры",
    "power": "Энергопотребление",
    "load": "Скорость загрузки",
    "all": "Всё сразу",
}
MIN_BENEFIT = {"fps": 8.0, "work": 8.0, "all": 8.0, "temps": 10.0, "power": 20.0, "load": 1.0}
MAX_PER_CATEGORY = {"gpu": 6, "cpu": 5, "ram": 2, "ssd": 1, "cooler": 2}
UNSPECIFIED = object()


@dataclass(frozen=True, slots=True)
class Action:
    category: str
    old: Part | None
    new: Part
    reason: str

    @property
    def cost(self) -> int:
        return self.new.price or 0


@dataclass(slots=True)
class UpgradeOption:
    label: str
    actions: list[Action]
    cost: int
    benefit: float
    benefit_text: str
    notes: list[str]
    report: CompatReport
    after: dict[str, Part]


@dataclass(slots=True)
class UpgradeResult:
    goal: str
    resolution: str
    balance_note: str | None
    options: list[UpgradeOption] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)   # проблемы текущей конфигурации
    message: str | None = None                           # почему вариантов нет


# ------------------------------------------------------------------ вспомогательное

def balance_note(cpu: Part | None, gpu: Part | None, resolution: str) -> str | None:
    """Осторожная оценка баланса. Никогда не утверждает, что узкое место есть «всегда»."""
    if cpu is None or gpu is None:
        return None
    c, g = cpu.spec("gaming"), gpu.spec("gaming")
    if c is None or g is None:
        return "Нет данных о классе производительности процессора или видеокарты — баланс оценить нельзя."
    if g / c >= 1.4:
        return (f"По классам каталога видеокарта заметно сильнее процессора. В играх, которые больше "
                f"нагружают процессор (например, CS2, Dota 2, Minecraft), особенно на {resolution} с низкими "
                f"настройками, процессор может ограничивать FPS. В тяжёлых по графике играх и на высоком "
                f"разрешении ограничение слабее — всё зависит от игры и настроек.")
    if c / g >= 1.4:
        return (f"По классам каталога процессор заметно сильнее видеокарты. В графически тяжёлых играх и на "
                f"{resolution} FPS чаще упирается в видеокарту; в играх, которые нагружают процессор, "
                f"разницы может не быть — всё зависит от игры и настроек.")
    return "Процессор и видеокарта близки по классу — явного перекоса нет (зависит от игры и настроек)."


def _perf(parts: dict[str, Part], goal: str, resolution: str) -> float | None:
    cpu, gpu = parts.get("cpu"), parts.get("gpu")
    if cpu is None or gpu is None:
        return None
    ram = parts.get("ram")
    ram_gb = ram.spec("capacity_gb") if ram is not None else None
    if goal == "fps":
        return performance(cpu, gpu, "gaming", (), resolution, ram_gb)
    if goal == "work":
        return performance(cpu, gpu, "work", (), resolution, ram_gb)
    return 0.5 * performance(cpu, gpu, "gaming", (), resolution, ram_gb) + \
        0.5 * performance(cpu, gpu, "work", (), resolution, ram_gb)


def _load(parts: dict[str, Part]) -> int | None:
    return estimate_load_w(parts.get("cpu"), parts.get("gpu"))


def current_problems(current: dict[str, Part]) -> list[str]:
    report = check_compatibility(current)
    return [i.text for i in report.issues if i.severity in (Severity.BAD, Severity.WARN)]


def _eligible(parts: list[Part], category: str) -> list[Part]:
    return [p for p in parts if p.category == category and p.price is not None and p.in_stock
            and p.condition == "new"]


def _prune(items: list[tuple[Part, float]], limit: int) -> list[Part]:
    """Оставляем недоминируемые (дороже и не лучше — выбрасываем) и ограничиваем число."""
    items = sorted(items, key=lambda x: (x[0].price, -x[1]))
    kept: list[tuple[Part, float]] = []
    best = float("-inf")
    for part, value in items:
        if value > best + 1e-9:
            kept.append((part, value))
            best = value
    if len(kept) > limit:
        step = (len(kept) - 1) / (limit - 1) if limit > 1 else 0
        kept = [kept[round(i * step)] for i in range(limit)]
    return [p for p, _ in kept]


# ------------------------------------------------------------------ генерация замен

def _candidates(current: dict[str, Part], catalog: list[Part], goal: str,
                resolution: str, budget: int) -> dict[str, list[Action]]:
    cpu, gpu, board, ram = (current.get(k) for k in ("cpu", "gpu", "motherboard", "ram"))
    ssd, psu = current.get("ssd"), current.get("psu")
    result: dict[str, list[Action]] = {}
    base = _perf(current, goal if goal in {"fps", "work", "all"} else "fps", resolution)

    def gain(parts: dict[str, Part]) -> float | None:
        value = _perf(parts, goal if goal in {"fps", "work", "all"} else "fps", resolution)
        return None if value is None or not base else (value / base - 1) * 100

    perf_goals = goal in {"fps", "work", "all"}
    if gpu is not None and cpu is not None and (perf_goals or goal == "power"):
        options = []
        for cand in _eligible(catalog, "gpu"):
            if cand.id == gpu.id or cand.price > budget:
                continue
            g = gain({**current, "gpu": cand})
            if goal == "power":
                saves = (gpu.spec("tgp_w") or 0) - (cand.spec("tgp_w") or 0)
                if g is not None and g >= -5 and saves >= MIN_BENEFIT["power"]:
                    options.append((cand, float(saves)))
            elif g is not None and g >= 5:
                options.append((cand, g))
        result["gpu"] = [Action("gpu", gpu, c, "") for c in _prune(options, MAX_PER_CATEGORY["gpu"])]

    if cpu is not None and gpu is not None and (perf_goals or goal == "power"):
        socket = board.spec("socket") if board is not None else cpu.spec("socket")
        options = []
        for cand in _eligible(catalog, "cpu"):
            if cand.id == cpu.id or cand.price > budget or cand.spec("socket") != socket:
                continue
            probe = {**current, "cpu": cand}
            if board is not None:
                pair = check_compatibility({"cpu": cand, "motherboard": board, **({"ram": ram} if ram else {})})
                if pair.has_blockers or pair.by_severity(Severity.UNKNOWN):
                    continue
            g = gain(probe)
            if goal == "power":
                saves = (cpu_max_power(cpu) or 0) - (cpu_max_power(cand) or 0)
                if g is not None and g >= -5 and saves >= MIN_BENEFIT["power"]:
                    options.append((cand, float(saves)))
            elif g is not None and g >= 5:
                options.append((cand, g))
        result["cpu"] = [Action("cpu", cpu, c, "") for c in _prune(options, MAX_PER_CATEGORY["cpu"])]

    if ram is not None and board is not None and goal in {"work", "all", "fps"}:
        target = ram.spec("capacity_gb") or 0
        options = []
        for cand in _eligible(catalog, "ram"):
            cap = cand.spec("capacity_gb") or 0
            if cap <= target or cand.price > budget or cand.spec("type") != board.spec("memory_type"):
                continue
            probe = check_compatibility({"cpu": cpu, "motherboard": board, "ram": cand})
            if probe.has_blockers or probe.by_severity(Severity.UNKNOWN):
                continue
            options.append((cand, float(cap)))
        result["ram"] = [Action("ram", ram, c, "") for c in _prune(options, MAX_PER_CATEGORY["ram"])]

    if goal in {"load", "all"} and board is not None:
        was_sata = ssd is not None and ssd.spec("interface") == "SATA"
        if ssd is None or was_sata:
            options = []
            for cand in _eligible(catalog, "ssd"):
                if cand.spec("interface") != "NVMe" or cand.price > budget:
                    continue
                if check_compatibility({"motherboard": board, "ssd": cand}).has_blockers:
                    continue
                if (cand.spec("capacity_gb") or 0) >= (ssd.spec("capacity_gb") if ssd else 500):
                    options.append((cand, float(cand.spec("capacity_gb") or 0)))
            options = sorted(options, key=lambda x: x[0].price)[:1]
            result["ssd"] = [Action("ssd", ssd, c, "") for c, _ in options]

    if goal in {"temps", "all"} and cpu is not None:
        options = []
        for cand in _eligible(catalog, "cooler"):
            if cand.price > budget:
                continue
            chk = check_compatibility({"cpu": cpu, "cooler": cand})
            if chk.has_blockers or chk.by_severity(Severity.UNKNOWN):
                continue
            power = cpu_max_power(cpu) or 0
            if power and (cand.spec("tdp_w") or 0) >= power * 1.2:
                options.append((cand, float(cand.spec("tdp_w") or 0)))
        result["cooler"] = [Action("cooler", None, c, "") for c in _prune(options, MAX_PER_CATEGORY["cooler"])]
    return result


def _benefit(goal: str, before: dict[str, Part], after: dict[str, Part], resolution: str,
             actions: list[Action]) -> tuple[float, str]:
    cats = {a.category for a in actions}
    if goal in {"fps", "work", "all"}:
        b = _perf(before, goal, resolution)
        a = _perf(after, goal, resolution)
        pieces = []
        if b and a is not None:
            pct = (a / b - 1) * 100
            pieces.append(f"+{pct:.0f}% по классу производительности каталога (оценка, не замер FPS)")
            value = pct
        else:
            value = 0.0
        if "ssd" in cats:
            pieces.append("быстрее загрузка системы и игр (NVMe)")
            value += 1
        if "cooler" in cats:
            pieces.append("ниже температуры процессора под нагрузкой")
            value += 1
        return value, "; ".join(pieces) or "заметного прироста не ожидается"
    if goal == "power":
        b, a = _load(before), _load(after)
        saves = (b - a) if b is not None and a is not None else 0
        return float(saves), f"расчётная пиковая нагрузка ниже примерно на {saves} Вт"
    if goal == "temps":
        cooler = next((x.new for x in actions if x.category == "cooler"), None)
        cpu = after.get("cpu")
        if cooler is None or cpu is None or not cpu_max_power(cpu):
            return 0.0, "эффект не оценить"
        head = (cooler.spec("tdp_w") / cpu_max_power(cpu) - 1) * 100
        return head, f"запас по отводу тепла ~{head:.0f}% сверх пиковой мощности процессора"
    return 1.0, "быстрее загрузка системы и программ; на FPS почти не влияет"


def _reason(action: Action, goal: str, resolution: str) -> str:
    n = action.new
    if action.category == "gpu":
        return (f"Более производительная видеокарта ({n.spec('vram_gb')} ГБ памяти) — основной рост в играх на {resolution}"
                if goal != "power" else f"Экономичнее текущей: {n.spec('tgp_w')} Вт против {action.old.spec('tgp_w')} Вт")
    if action.category == "cpu":
        return f"Тот же сокет {n.spec('socket')} — меняется только процессор, без замены платы и памяти"
    if action.category == "ram":
        return f"Увеличение объёма до {n.spec('capacity_gb')} ГБ ({n.spec('type')}) — меньше подкачки в тяжёлых программах"
    if action.category == "ssd":
        return "NVMe-накопитель заметно быстрее SATA при загрузке системы, игр и проектов"
    if action.category == "psu":
        return "Нужен для питания новой конфигурации с запасом не менее 25%"
    return "Больший запас по отводу тепла — тише и холоднее под нагрузкой"


# ------------------------------------------------------------------ главный алгоритм

def analyze_upgrade(current: dict[str, Part], catalog: list[Part], goal: str,
                    resolution: str, budget: int) -> UpgradeResult:
    result = UpgradeResult(goal=goal, resolution=resolution,
                           balance_note=balance_note(current.get("cpu"), current.get("gpu"), resolution))
    result.problems = current_problems(current)
    if budget < 1000:
        result.message = "Бюджет слишком мал для замены деталей."
        return result
    needs_pair = goal in {"fps", "work", "all", "power"}
    if needs_pair and (current.get("cpu") is None or current.get("gpu") is None):
        result.message = "Для этой цели нужны и процессор, и видеокарта — укажите обе детали."
        return result
    if goal == "temps" and current.get("cpu") is None:
        result.message = "Для улучшения температур нужно указать процессор."
        return result
    if goal == "load" and current.get("motherboard") is None:
        result.message = "Для улучшения скорости загрузки нужно указать материнскую плату (проверим слоты M.2)."
        return result

    cands = _candidates(current, catalog, goal, resolution, budget)
    cats = [c for c in ("gpu", "cpu", "ram", "ssd", "cooler") if cands.get(c)]
    if not cats:
        result.message = ("В рамках вашего бюджета и платформы улучшений по каталогу не нашлось. "
                          "Возможно, текущая конфигурация уже хороша для этой цели, либо нужны деталь с другим "
                          "сокетом (замена платформы) или больший бюджет.")
        return result

    evaluated: list[tuple[float, int, list[Action], dict[str, Part], CompatReport, list[str]]] = []
    psu_pool = sorted(_eligible(catalog, "psu"), key=lambda p: p.price)
    choices = [[None, *cands[c]] for c in cats]
    for combo in product(*choices):
        actions = [a for a in combo if a is not None]
        if not actions:
            continue
        after = dict(current)
        for a in actions:
            after[a.category] = a.new
        notes: list[str] = []
        extra: Action | None = None

        load = _load(after)
        psu = after.get("psu")
        if load is not None:
            need = max(load * PSU_HEADROOM, (after["gpu"].spec("rec_psu_w") or 0))
            if psu is None:
                notes.append(f"Вы не указали блок питания: убедитесь, что его мощность не менее {int(need)} Вт.")
            elif (psu.spec("wattage") or 0) < need:
                replacement = next((p for p in psu_pool if (p.spec("wattage") or 0) >= need), None)
                if replacement is None:
                    continue
                extra = Action("psu", psu, replacement, "")
                after["psu"] = replacement
        cost = sum(a.cost for a in actions) + (extra.cost if extra else 0)
        if cost > budget:
            continue
        report = check_compatibility(after)
        if report.has_blockers:
            continue
        new_actions = [*actions, *([extra] if extra else [])]
        evaluated.append((0.0, cost, new_actions, after, report, notes))

    if not evaluated:
        result.message = ("Подходящие замены есть, но вместе с необходимым блоком питания они не укладываются "
                          "в бюджет или несовместимы с вашей платформой.")
        return result

    scored = []
    for _, cost, actions, after, report, notes in evaluated:
        value, text = _benefit(goal, current, after, resolution, actions)
        scored.append((value, text, cost, actions, after, report, notes))
    threshold = MIN_BENEFIT.get(goal, 5.0)
    scored = [s for s in scored if s[0] >= threshold]
    if not scored:
        result.message = ("Найденные замены дают слишком малый эффект для этой цели. "
                          "Лучше накопить на более существенный апгрейд или выбрать другую цель.")
        return result

    def build_option(label: str, item) -> UpgradeOption:
        value, text, cost, actions, after, report, notes = item
        all_notes = list(notes)
        for a in actions:
            if a.category == "psu":
                all_notes.append("Потребуется замена блока питания — она включена в стоимость.")
        for issue in report.issues:
            if issue.severity == Severity.WARN:
                all_notes.append(issue.text)
        fixed = [Action(a.category, a.old, a.new, _reason(a, goal, resolution)) for a in actions]
        return UpgradeOption(label, fixed, cost, value, text, list(dict.fromkeys(all_notes)), report, after)

    best_value = max(s[0] for s in scored)
    budget_pool = [s for s in scored if s[2] <= budget * 0.4] or [min(scored, key=lambda s: s[2])]
    budget_pick = max(budget_pool, key=lambda s: (s[0], -s[2]))
    good = [s for s in scored if s[0] >= best_value * 0.5]
    optimal_pick = max(good, key=lambda s: (s[0] / max(s[2], 1), s[0]))
    max_pick = max(scored, key=lambda s: (s[0], -s[2]))

    seen: list[tuple] = []
    for label, pick in (("💰 Бюджетный апгрейд", budget_pick), ("⚡ Оптимальный апгрейд", optimal_pick),
                        ("🚀 Максимальный апгрейд", max_pick)):
        signature = tuple(sorted((a.category, a.new.id) for a in pick[3]))
        if signature in seen:
            continue
        seen.append(signature)
        result.options.append(build_option(label, pick))
    return result


def action_title(action: Action) -> str:
    return CATEGORY_SHORT.get(action.category, action.category)


def gpu_vram_hint(gpu: Part, resolution: str) -> float:
    return vram_factor(gpu, "gaming", resolution)
