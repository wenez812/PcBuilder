"""Сквозные тесты: апдейты проходят через реальный Dispatcher, сеть Telegram подменена."""
from sqlalchemy import func, select

from aiogram.methods import EditMessageText, SendMessage

from bot.keyboards import Cb
from bot.models import UsageEvent, User
from bot.services import user_service
from tests.conftest import callback_update, message_update

USER = 42


async def test_start_registers_user_and_shows_menu(dp, bot, db, fake_session):
    await dp.feed_raw_update(bot, message_update(1, USER, "/start"))

    sent = fake_session.sent(SendMessage)
    assert len(sent) == 1
    assert "Добро пожаловать в PC Builder" in sent[0].text
    buttons = [b for row in sent[0].reply_markup.inline_keyboard for b in row]
    assert len(buttons) == 8

    async with db.session_factory() as s:
        assert await user_service.get_user(s, USER) is not None


async def test_main_menu_callback(dp, bot, fake_session):
    await dp.feed_raw_update(bot, callback_update(1, USER, Cb.MAIN))
    edits = fake_session.sent(EditMessageText)
    assert len(edits) == 1 and "Главное меню" in edits[0].text


async def test_help_command(dp, bot, fake_session):
    await dp.feed_raw_update(bot, message_update(1, USER, "/help"))
    assert "Помощь" in fake_session.sent(SendMessage)[0].text


async def test_profile_shows_real_data(dp, bot, fake_session):
    await dp.feed_raw_update(bot, message_update(1, USER, "/start"))
    await dp.feed_raw_update(bot, callback_update(2, USER, Cb.PROFILE))
    text = fake_session.sent(EditMessageText)[-1].text
    assert f"<code>{USER}</code>" in text
    assert "Дата регистрации" in text
    assert "Сохранённых сборок: 0" in text
    assert "Уведомления: включены" in text


async def test_toggle_notifications_persists(dp, bot, db, fake_session):
    await dp.feed_raw_update(bot, message_update(1, USER, "/start"))
    await dp.feed_raw_update(bot, callback_update(2, USER, Cb.PROFILE_TOGGLE_NOTIFY))
    assert "выключены" in fake_session.sent(EditMessageText)[-1].text
    async with db.session_factory() as s:
        user = await user_service.get_user(s, USER)
        assert user.notifications_enabled is False


async def test_delete_data_flow(dp, bot, db, fake_session):
    await dp.feed_raw_update(bot, message_update(1, USER, "/start"))
    await dp.feed_raw_update(bot, callback_update(2, USER, Cb.PROFILE_DELETE))
    assert "Удаление данных" in fake_session.sent(EditMessageText)[-1].text
    # подтверждение ещё не нажато — данные на месте
    async with db.session_factory() as s:
        assert await user_service.get_user(s, USER) is not None

    await dp.feed_raw_update(bot, callback_update(3, USER, Cb.PROFILE_DELETE_CONFIRM))
    assert "удалены" in fake_session.sent(EditMessageText)[-1].text
    async with db.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(User)) == 0
        assert await s.scalar(select(func.count()).select_from(UsageEvent)) == 0


async def test_spam_is_throttled(settings, db, bot, fake_session):
    from dataclasses import replace

    from bot.app import build_dispatcher

    strict = replace(settings, throttle_max_actions=2, throttle_window=60, throttle_cooldown=60)
    dp = build_dispatcher(strict, db)
    for i in range(1, 6):
        await dp.feed_raw_update(bot, message_update(i, USER, "/help"))

    texts = [m.text for m in fake_session.sent(SendMessage)]
    assert sum("Помощь" in t for t in texts) == 2          # пропущены только 2 действия
    assert sum("Слишком много действий" in t for t in texts) == 1  # предупреждение — один раз


async def test_handler_error_is_logged_and_user_notified(settings, db, bot, fake_session, monkeypatch):
    from bot.app import build_dispatcher
    from bot.models import ErrorLog

    async def boom(*args, **kwargs):
        raise RuntimeError("тестовый сбой")

    monkeypatch.setattr(user_service, "log_usage", boom)
    dp = build_dispatcher(settings, db)
    await dp.feed_raw_update(bot, message_update(1, USER, "/start"))

    assert "Что-то пошло не так" in fake_session.sent(SendMessage)[-1].text
    async with db.session_factory() as s:
        row = (await s.execute(select(ErrorLog))).scalar_one()
        assert row.error_type == "RuntimeError" and "тестовый сбой" in row.message
