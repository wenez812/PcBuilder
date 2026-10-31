"""Интерфейс источников цен. Подключён только то, что реально работает и разрешено."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class SourceStatus(str, Enum):
    CONNECTED = "connected"
    NOT_CONFIGURED = "not_configured"   # код есть, но не заданы параметры (например, URL фида)
    NOT_CONNECTED = "not_connected"     # интеграции нет: нет официального API / доступа


STATUS_LABEL = {
    SourceStatus.CONNECTED: "🟢 подключён",
    SourceStatus.NOT_CONFIGURED: "🟡 не настроен",
    SourceStatus.NOT_CONNECTED: "⚫ не подключён",
}


class SourceError(RuntimeError):
    """Ошибка получения данных от источника."""


class NotConnectedError(SourceError):
    """Источник не подключён — данных нет и придумывать их нельзя."""


@dataclass(frozen=True, slots=True)
class PriceRecord:
    category: str | None
    name: str
    shop: str
    price: int
    in_stock: bool = True
    url: str | None = None


class PriceSource(ABC):
    key: str
    title: str

    @property
    @abstractmethod
    def status(self) -> SourceStatus: ...

    @property
    @abstractmethod
    def status_reason(self) -> str: ...

    @abstractmethod
    async def fetch(self) -> list[PriceRecord]:
        """Возвращает актуальные предложения. Для неподключённых источников бросает NotConnectedError."""
