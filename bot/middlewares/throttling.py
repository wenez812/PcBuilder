"""Защита от спама: скользящее окно + временная блокировка при превышении."""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.utils.texts import SPAM_WARNING


class RateLimiter:
    """Не более `max_actions` действий за `window` секунд; при превышении — блок на `cooldown`."""

    def __init__(
        self,
        max_actions: int = 8,
        window: float = 5.0,
        cooldown: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_actions = max_actions
        self.window = window
        self.cooldown = cooldown
        self._clock = clock
        self._events: dict[int, deque[float]] = {}
        self._blocked_until: dict[int, float] = {}
        self._warned: set[int] = set()

    def check(self, user_id: int) -> tuple[bool, bool]:
        """Возвращает (разрешено, нужно_ли_предупредить)."""
        now = self._clock()

        blocked_until = self._blocked_until.get(user_id)
        if blocked_until is not None:
            if now < blocked_until:
                first_warning = user_id not in self._warned
                self._warned.add(user_id)
                return False, first_warning
            del self._blocked_until[user_id]
            self._warned.discard(user_id)
            self._events.pop(user_id, None)

        events = self._events.setdefault(user_id, deque())
        while events and now - events[0] > self.window:
            events.popleft()

        if len(events) >= self.max_actions:
            self._blocked_until[user_id] = now + self.cooldown
            self._warned.add(user_id)
            return False, True

        events.append(now)
        return True, False

    def cleanup(self) -> None:
        """Удаляет устаревшие записи, чтобы словари не росли бесконечно."""
        now = self._clock()
        for user_id in [u for u, ev in self._events.items() if not ev or now - ev[-1] > self.window]:
            self._events.pop(user_id, None)
        for user_id in [u for u, t in self._blocked_until.items() if now >= t]:
            self._blocked_until.pop(user_id, None)
            self._warned.discard(user_id)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, limiter: RateLimiter) -> None:
        self.limiter = limiter
        self._calls = 0

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            return await handler(event, data)

        self._calls += 1
        if self._calls % 500 == 0:
            self.limiter.cleanup()

        allowed, warn = self.limiter.check(tg_user.id)
        if allowed:
            return await handler(event, data)

        if warn:
            if isinstance(event, CallbackQuery):
                await event.answer(SPAM_WARNING, show_alert=False)
            elif isinstance(event, Message):
                await event.answer(SPAM_WARNING)
        elif isinstance(event, CallbackQuery):
            await event.answer()  # убрать «часики» на кнопке
        return None
