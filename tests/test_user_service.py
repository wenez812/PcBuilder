from sqlalchemy import func, select

from bot.models import ErrorLog, UsageEvent, User
from bot.services import user_service


async def test_create_then_get_existing(db):
    async with db.session_factory() as s:
        user, created = await user_service.get_or_create_user(s, 5, "nick", "Имя")
        await s.commit()
        assert created and user.id is not None

    async with db.session_factory() as s:
        user, created = await user_service.get_or_create_user(s, 5, "new_nick", "Имя")
        await s.commit()
        assert not created
        assert user.username == "new_nick"
        count = await s.scalar(select(func.count()).select_from(User))
        assert count == 1


async def test_registration_date_is_utc_aware(db):
    async with db.session_factory() as s:
        await user_service.get_or_create_user(s, 6)
        await s.commit()
    async with db.session_factory() as s:
        user = await user_service.get_user(s, 6)
        assert user.created_at.tzinfo is not None


async def test_toggle_notifications(db):
    async with db.session_factory() as s:
        user, _ = await user_service.get_or_create_user(s, 7)
        assert user.notifications_enabled is True
        assert await user_service.toggle_notifications(s, user) is False
        assert await user_service.toggle_notifications(s, user) is True


async def test_profile_counts_events(db):
    async with db.session_factory() as s:
        user, _ = await user_service.get_or_create_user(s, 8)
        await user_service.log_usage(s, user, "a")
        await user_service.log_usage(s, user, "b")
        profile = await user_service.get_profile(s, user)
        assert profile.actions_count == 2
        assert profile.telegram_id == 8


async def test_delete_user_data_removes_everything(db):
    async with db.session_factory() as s:
        user, _ = await user_service.get_or_create_user(s, 9)
        other, _ = await user_service.get_or_create_user(s, 10)
        await user_service.log_usage(s, user, "x")
        await user_service.log_usage(s, other, "y")
        await s.commit()

    async with db.session_factory() as s:
        assert await user_service.delete_user_data(s, 9) is True
        assert await user_service.delete_user_data(s, 9) is False  # повторно — нечего удалять
        await s.commit()

    async with db.session_factory() as s:
        assert await user_service.get_user(s, 9) is None
        assert await user_service.get_user(s, 10) is not None
        events = await s.scalar(select(func.count()).select_from(UsageEvent))
        assert events == 1  # события другого пользователя остались


async def test_save_error(db):
    async with db.session_factory() as s:
        await user_service.save_error(s, "ValueError", "boom", "tb", telegram_id=1)
        await s.commit()
    async with db.session_factory() as s:
        row = (await s.execute(select(ErrorLog))).scalar_one()
        assert row.error_type == "ValueError" and row.telegram_id == 1
