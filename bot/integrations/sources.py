"""Конкретные источники цен.

* JsonFeedSource — настоящая интеграция с любым разрешённым JSON-фидом цен
  (ваш прайс-лист, партнёрский API, собственный парсер на вашей стороне). Включается через PRICE_FEED_URL.
* StoreSource — заготовки DNS, Ситилинк, Ozon, Wildberries. Они ЧЕСТНО помечены как не подключённые:
  публичного API цен для покупателей у них нет, а парсинг сайтов без разрешения мы не делаем.
  Чтобы подключить магазин, реализуйте метод fetch() в наследнике (получив доступ к официальному API/фиду).
"""
from __future__ import annotations

import json
import logging
from urllib.parse import urlparse

import aiohttp

from bot.integrations.base import (
    NotConnectedError,
    PriceRecord,
    PriceSource,
    SourceError,
    SourceStatus,
)
from bot.services.importers import validate_offer

logger = logging.getLogger(__name__)

MAX_FEED_BYTES = 5_000_000
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}


def parse_feed(payload: object, default_shop: str | None) -> tuple[list[PriceRecord], list[str]]:
    """Разбирает JSON фида. Формат: [{"category","name","shop","price","in_stock","url"}, ...]."""
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    if not isinstance(payload, list):
        raise SourceError("Фид должен быть JSON-списком (или объектом с полем items)")
    records: list[PriceRecord] = []
    errors: list[str] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict) or not item.get("name"):
            errors.append(f"элемент {index}: нет названия")
            continue
        shop = str(item.get("shop") or default_shop or "")
        price, error = validate_offer(shop, item.get("price", ""), item.get("url"))
        if error:
            errors.append(f"элемент {index}: {error}")
            continue
        records.append(PriceRecord(item.get("category"), str(item["name"]).strip(), shop, price,
                                   bool(item.get("in_stock", True)), item.get("url")))
    return records, errors


class JsonFeedSource(PriceSource):
    key = "json_feed"
    title = "JSON-фид цен"

    def __init__(self, url: str | None, token: str | None = None, default_shop: str | None = None) -> None:
        self.url, self.token, self.default_shop = url, token, default_shop
        self.last_errors: list[str] = []

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.CONNECTED if self.url else SourceStatus.NOT_CONFIGURED

    @property
    def status_reason(self) -> str:
        if not self.url:
            return "Задайте PRICE_FEED_URL в .env, чтобы цены загружались автоматически."
        return f"Источник: {urlparse(self.url).hostname}"

    async def fetch(self) -> list[PriceRecord]:
        if not self.url:
            raise NotConnectedError("PRICE_FEED_URL не задан")
        if not _is_allowed_url(self.url):
            raise SourceError("PRICE_FEED_URL должен начинаться с https://")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
                async with http.get(self.url, headers=headers) as response:
                    if response.status != 200:
                        raise SourceError(f"Фид вернул HTTP {response.status}")
                    raw = await response.content.read(MAX_FEED_BYTES + 1)
        except aiohttp.ClientError as exc:
            raise SourceError(f"Не удалось получить фид: {exc}") from exc
        if len(raw) > MAX_FEED_BYTES:
            raise SourceError("Фид больше 5 МБ")
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except ValueError as exc:
            raise SourceError("Фид не является корректным JSON") from exc
        records, self.last_errors = parse_feed(payload, self.default_shop)
        return records


class StoreSource(PriceSource):
    """Магазин без подключённой интеграции. Данных не возвращает и не имитирует."""

    def __init__(self, key: str, title: str, reason: str) -> None:
        self.key, self.title, self._reason = key, title, reason

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.NOT_CONNECTED

    @property
    def status_reason(self) -> str:
        return self._reason

    async def fetch(self) -> list[PriceRecord]:
        raise NotConnectedError(f"{self.title}: интеграция не подключена")


_NO_API = "нет официального API цен для покупателей — автоматическая загрузка не включена"


def build_sources(feed_url: str | None, feed_token: str | None, feed_shop: str | None) -> list[PriceSource]:
    return [
        StoreSource("dns", "DNS", _NO_API),
        StoreSource("citilink", "Ситилинк", _NO_API),
        StoreSource("ozon", "Ozon", _NO_API),
        StoreSource("wildberries", "Wildberries", _NO_API),
        JsonFeedSource(feed_url, feed_token, feed_shop),
    ]
