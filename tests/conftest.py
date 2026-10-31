from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetFile, GetMe, SendMessage
from aiogram.types import Chat, File, Message, User as TgUser

from bot.app import build_dispatcher
from bot.database import Database
from config import Settings

TEST_TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


class FakeSession(BaseSession):
    """Подменяет сетевой слой aiogram: запоминает вызовы, ничего не отправляет в Telegram."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list = []
        self.file_payload: bytes = b""

    async def close(self) -> None:  # pragma: no cover
        return None

    async def stream_content(self, *args, **kwargs):
        yield self.file_payload

    async def make_request(self, bot: Bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 1
            return Message(
                message_id=len(self.requests),
                date=datetime.now(),
                chat=Chat(id=int(chat_id), type="private"),
                text=method.text,
            )
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="docs/file.bin")
        if isinstance(method, GetMe):
            return TgUser(id=999, is_bot=True, first_name="PC Builder", username="pc_builder_test_bot")
        if isinstance(method, AnswerCallbackQuery):
            return True
        return True

    def sent(self, method_type) -> list:
        return [m for m in self.requests if isinstance(m, method_type)]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token=TEST_TOKEN,
        admin_ids=frozenset({1}),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        log_level="WARNING",
        log_dir=tmp_path,
        throttle_max_actions=100,
        throttle_window=5,
        throttle_cooldown=5,
    )


@pytest_asyncio.fixture
async def db(settings: Settings):
    database = Database(settings.database_url)
    await database.create_all()
    yield database
    await database.dispose()


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(fake_session: FakeSession) -> Bot:
    return Bot(
        token=TEST_TOKEN,
        session=fake_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@pytest.fixture
def dp(settings: Settings, db: Database):
    return build_dispatcher(settings, db)


def message_update(update_id: int, user_id: int, text: str) -> dict:
    entities = []
    if text.startswith("/"):
        entities = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1_700_000_000,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Тест", "username": "tester"},
            "text": text,
            "entities": entities,
        },
    }


def callback_update(update_id: int, user_id: int, data: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": str(update_id),
            "chat_instance": "ci",
            "data": data,
            "from": {"id": user_id, "is_bot": False, "first_name": "Тест", "username": "tester"},
            "message": {
                "message_id": 10,
                "date": 1_700_000_000,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": 999, "is_bot": True, "first_name": "PC Builder"},
                "text": "menu",
            },
        },
    }


@pytest_asyncio.fixture
async def seeded(db: Database):
    """Каталог + СИНТЕТИЧЕСКИЕ цены (только для тестов)."""
    from tests.helpers import seed_all

    async with db.session_factory() as session:
        await seed_all(session)
    return db


async def part_id(db: Database, category: str, name_part: str) -> int:
    from sqlalchemy import select

    from bot.models import Component

    async with db.session_factory() as session:
        rows = (await session.execute(select(Component).where(Component.category == category))).scalars().all()
    for row in rows:
        if name_part.lower() in row.name.lower():
            return row.id
    raise LookupError(f"{category}: {name_part}")


def photo_update(update_id: int, user_id: int) -> dict:
    base = message_update(update_id, user_id, "x")
    base["message"].pop("text"); base["message"].pop("entities")
    base["message"]["photo"] = [{"file_id": "p", "file_unique_id": "pu", "width": 10, "height": 10}]
    return base


def document_update(update_id: int, user_id: int, name: str = "file.csv") -> dict:
    base = message_update(update_id, user_id, "x")
    base["message"].pop("text"); base["message"].pop("entities")
    base["message"]["document"] = {"file_id": "d", "file_unique_id": "du", "file_name": name, "file_size": 100}
    return base
