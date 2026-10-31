"""Конфигурация приложения. Все секреты читаются только из окружения / .env."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Формат токена Telegram: <числовой id бота>:<секрет>
_TOKEN_RE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,50}$")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class ConfigError(RuntimeError):
    """Ошибка конфигурации (понятное сообщение для пользователя)."""


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    database_url: str
    log_level: str
    log_dir: Path
    throttle_max_actions: int
    throttle_window: float
    throttle_cooldown: float
    price_feed_url: str | None = None
    price_feed_token: str | None = None
    price_feed_shop: str | None = None
    price_refresh_hours: float = 6.0

    def is_admin(self, telegram_id: int) -> bool:
        return telegram_id in self.admin_ids

    def __repr__(self) -> str:  # токен не должен попадать в логи
        return (
            f"Settings(admin_ids={sorted(self.admin_ids)}, database_url={self.database_url!r}, "
            f"log_level={self.log_level!r})"
        )


def parse_admin_ids(raw: str | None) -> frozenset[int]:
    """'1, 2;3' -> {1, 2, 3}. Нечисловые значения вызывают ConfigError."""
    if not raw or not raw.strip():
        return frozenset()
    result: set[int] = set()
    for part in re.split(r"[,\s;]+", raw.strip()):
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(f"ADMIN_IDS: «{part}» не является числовым Telegram ID")
        result.add(int(part))
    return frozenset(result)


def _positive_number(name: str, raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: «{raw}» — это не число") from exc
    if value <= 0:
        raise ConfigError(f"{name}: значение должно быть больше нуля")
    return value


def _non_negative_number(name: str, raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name}: «{raw}» — это не число") from exc
    if value < 0:
        raise ConfigError(f"{name}: значение не может быть отрицательным")
    return value


def _normalize_db_url(url: str) -> str:
    """Относительный путь SQLite считаем от корня проекта, создаём папку для файла БД."""
    prefix = "sqlite+aiosqlite:///"
    if url.startswith(prefix) and not url.startswith(prefix + "/") and ":memory:" not in url:
        path = BASE_DIR / url[len(prefix):]
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{path}"
    return url


def load_settings(env_file: str | os.PathLike[str] | None = None, *, require_token: bool = True) -> Settings:
    load_dotenv(env_file or BASE_DIR / ".env", override=False)

    token = os.getenv("BOT_TOKEN", "").strip()
    if require_token:
        if not token:
            raise ConfigError(
                "Не задан BOT_TOKEN. Скопируйте .env.example в .env и вставьте токен от @BotFather."
            )
        if not _TOKEN_RE.match(token):
            raise ConfigError("BOT_TOKEN имеет неверный формат (ожидается вид 123456789:AA...).")

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL: допустимы {', '.join(sorted(_LOG_LEVELS))}")

    db_url = os.getenv("DATABASE_URL", "").strip() or "sqlite+aiosqlite:///data/pc_builder.db"

    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        bot_token=token,
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS")),
        database_url=_normalize_db_url(db_url),
        log_level=log_level,
        log_dir=log_dir,
        throttle_max_actions=int(_positive_number("THROTTLE_MAX_ACTIONS", os.getenv("THROTTLE_MAX_ACTIONS"), 8)),
        throttle_window=_positive_number("THROTTLE_WINDOW", os.getenv("THROTTLE_WINDOW"), 5),
        throttle_cooldown=_positive_number("THROTTLE_COOLDOWN", os.getenv("THROTTLE_COOLDOWN"), 5),
        price_feed_url=os.getenv("PRICE_FEED_URL", "").strip() or None,
        price_feed_token=os.getenv("PRICE_FEED_TOKEN", "").strip() or None,
        price_feed_shop=os.getenv("PRICE_FEED_SHOP", "").strip() or None,
        price_refresh_hours=_non_negative_number("PRICE_REFRESH_HOURS", os.getenv("PRICE_REFRESH_HOURS"), 6.0),
    )
