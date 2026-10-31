"""Разбор пользовательского ввода: бюджет и запрос «одной фразой» (без внешнего ИИ, по правилам)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_BUDGET = 15_000
MAX_BUDGET = 1_000_000


class InputError(ValueError):
    """Ввод пользователя не удалось разобрать (сообщение показывается пользователю)."""


def parse_budget(text: str) -> int:
    """'100к', '100 000', '100000 ₽', '1.5м', '80 тыс' -> int. Бросает InputError."""
    raw = (text or "").strip().lower().replace(" ", " ")
    match = re.fullmatch(r"(\d[\d\s]*(?:[.,]\d+)?)\s*(млн|м|m|тысяч|тыс|т\.?р?\.?|к|k)?\s*(?:₽|руб(?:лей|\.)?|р\.?)?", raw)
    if not match:
        raise InputError("Не понял сумму. Напишите, например: 85000, 85 000 или 85к.")
    number = float(match.group(1).replace(" ", "").replace(",", "."))
    suffix = match.group(2) or ""
    if suffix in {"млн", "м", "m"}:
        number *= 1_000_000
    elif suffix:
        number *= 1_000
    value = int(round(number))
    if value < MIN_BUDGET:
        raise InputError(f"Минимальный бюджет — {MIN_BUDGET:,} ₽.".replace(",", " "))
    if value > MAX_BUDGET:
        raise InputError(f"Максимальный бюджет — {MAX_BUDGET:,} ₽.".replace(",", " "))
    return value


_GAME_ALIASES = {
    "CS2": ("cs2", "cs 2", "кс2", "кс 2", "кс", "counter-strike", "counter strike", "контру"),
    "Dota 2": ("dota", "дота"),
    "GTA V": ("gta", "гта"),
    "Cyberpunk 2077": ("cyberpunk", "киберпанк"),
    "Fortnite": ("fortnite", "фортнайт"),
    "Minecraft": ("minecraft", "майнкрафт", "майн"),
}


@dataclass(slots=True)
class PhraseRequest:
    budget: int | None = None
    purpose: str | None = None
    games: list[str] = field(default_factory=list)
    resolution: str | None = None
    priority: str | None = None
    cpu_vendor: str = "any"
    gpu_vendor: str = "any"
    allow_used: bool = False
    min_ram_gb: int | None = None
    target_fps: int | None = None
    ignored: list[str] = field(default_factory=list)   # что бот понял, но каталог не умеет учитывать


def _word(text: str, *aliases: str) -> bool:
    return any(re.search(rf"(?<![а-яa-z0-9]){re.escape(a)}(?![а-яa-z0-9])", text) for a in aliases)


def parse_phrase(text: str) -> PhraseRequest:
    """Достаёт параметры подбора из свободной фразы. Что не распознано — остаётся пустым."""
    t = (text or "").lower().replace("ё", "е")
    req = PhraseRequest()

    # target FPS убираем до разбора бюджета, чтобы «300 fps» не принять за деньги
    fps_match = re.search(r"(\d{2,3})\s*(?:fps|фпс)", t)
    if fps_match:
        req.target_fps = int(fps_match.group(1))
        t = t.replace(fps_match.group(0), " ")

    ram_match = re.search(r"(\d{1,3})\s*(?:гб|gb|гиг\w*)\s*(?:оператив\w*|озу|ram|памят\w*)", t) or \
        re.search(r"(?:оператив\w*|озу|ram|памят\w*)\s*(\d{1,3})\s*(?:гб|gb)?", t)
    if ram_match:
        req.min_ram_gb = int(ram_match.group(1))
        t = t.replace(ram_match.group(0), " ")

    budget_match = re.search(
        r"(?:за|до|бюджет\w*|около|примерно)?\s*(\d[\d\s]*(?:[.,]\d+)?\s*(?:млн|тысяч|тыс|к|k|т\.р|₽|руб\w*|р\b))", t)
    if budget_match:
        try:
            req.budget = parse_budget(budget_match.group(1))
        except InputError:
            req.budget = None
    if req.budget is None:
        plain = re.search(r"(?:за|до|бюджет\w*)\s*(\d{5,7})", t)
        if plain:
            try:
                req.budget = parse_budget(plain.group(1))
            except InputError:
                pass

    for game, aliases in _GAME_ALIASES.items():
        if _word(t, *aliases):
            req.games.append(game)

    if re.search(r"4\s?k|2160|uhd", t):
        req.resolution = "4K"
    elif re.search(r"1440|2\s?k\b|qhd|2к", t):
        req.resolution = "1440p"
    elif re.search(r"1080|fullhd|full hd|фхд|fhd", t):
        req.resolution = "1080p"

    if _word(t, "nvidia", "нвидиа", "нвидия", "geforce", "rtx", "джифорс"):
        req.gpu_vendor = "nvidia"
    elif _word(t, "radeon", "радеон", "rx"):
        req.gpu_vendor = "amd"
    if _word(t, "intel", "интел", "core"):
        req.cpu_vendor = "intel"
    elif _word(t, "ryzen", "райзен"):
        req.cpu_vendor = "amd"

    if re.search(r"б/у|\bбу\b|подержан", t):
        req.allow_used = True

    if re.search(r"монтаж|premiere|premier|davinci|видеомонтаж", t):
        req.purpose = "video"
    elif re.search(r"3d|блендер|blender|рендер", t):
        req.purpose = "3d"
    elif re.search(r"программир|разработ|кодить|код\b", t):
        req.purpose = "programming"
    elif req.games or re.search(r"игр|играть|гейм|gaming", t):
        req.purpose = "gaming"
    elif re.search(r"работ|офис|учеб", t):
        req.purpose = "work"
    elif re.search(r"универсальн", t):
        req.purpose = "universal"

    if re.search(r"тих|бесшум", t):
        req.priority = "quiet"
    elif re.search(r"дешев|мин\w* цен|эконом|подешевле", t):
        req.priority = "price"
    elif re.search(r"апгрейд|модернизац", t):
        req.priority = "upgrade"
    elif re.search(r"макс\w* (?:fps|фпс)|побольше fps|максимальн\w* производит", t) or req.target_fps:
        req.priority = "fps"

    if re.search(r"бел(?:ый|ого|ом|ая|ое)|черн(?:ый|ого|ом|ая|ое)|rgb|подсветк", t):
        req.ignored.append("цвет и подсветка корпуса (в каталоге нет данных об оформлении)")
    if re.search(r"\bamd\b", t) and req.cpu_vendor == "any" and req.gpu_vendor == "any":
        req.ignored.append("«AMD» без уточнения: процессор (Ryzen) или видеокарта (Radeon)?")
    return req
