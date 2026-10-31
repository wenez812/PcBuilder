"""Профиль, настройки и удаление пользовательских данных."""
from __future__ import annotations

import logging
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import Cb, confirm_delete_kb, nav_row, profile_kb
from bot.models import User
from bot.services import build_service, user_service
from bot.utils import texts
from bot.utils.telegram import show

logger = logging.getLogger(__name__)


async def _render_profile(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    profile = await user_service.get_profile(session, user)
    await show(
        callback,
        texts.profile_text(
            telegram_id=profile.telegram_id,
            registered_at=profile.registered_at,
            notifications_enabled=profile.notifications_enabled,
            actions_count=profile.actions_count,
            builds_count=profile.builds_count,
        ),
        profile_kb(profile.notifications_enabled),
    )


def build_router() -> Router:
    router = Router(name="profile")

    @router.callback_query(F.data == Cb.PROFILE)
    async def cb_profile(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await user_service.log_usage(session, db_user, "open:profile")
        await _render_profile(callback, session, db_user)

    @router.callback_query(F.data == Cb.PROFILE_TOGGLE_NOTIFY)
    async def cb_toggle_notifications(
        callback: CallbackQuery, session: AsyncSession, db_user: User
    ) -> None:
        enabled = await user_service.toggle_notifications(session, db_user)
        await callback.answer("🔔 Уведомления включены" if enabled else "🔕 Уведомления выключены")
        await _render_profile(callback, session, db_user)

    @router.callback_query(F.data == Cb.PROFILE_HISTORY)
    async def cb_history(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        items = await build_service.list_history(session, db_user)
        if not items:
            text = "📜 <b>История подборов</b>\n\nПока пусто: подберите ПК или проанализируйте апгрейд."
        else:
            lines = ["📜 <b>История подборов</b>\n"]
            for h in items:
                kind = "🖥 Подбор" if h.kind == "build" else "🔧 Апгрейд"
                total = f" — {h.total_rub:,} ₽".replace(",", " ") if h.total_rub else ""
                lines.append(f"{h.created_at.strftime('%d.%m.%Y')} · {kind}: {escape(h.summary)}{total}")
            text = "\n".join(lines)
        await show(callback, text[:4000], InlineKeyboardMarkup(inline_keyboard=[nav_row(back=Cb.PROFILE)]))

    @router.callback_query(F.data == Cb.PROFILE_DELETE)
    async def cb_delete_ask(callback: CallbackQuery) -> None:
        await callback.answer()
        await show(callback, texts.delete_confirm_text(), confirm_delete_kb())

    @router.callback_query(F.data == Cb.PROFILE_DELETE_CONFIRM)
    async def cb_delete_confirm(
        callback: CallbackQuery, session: AsyncSession, state: FSMContext
    ) -> None:
        await callback.answer()
        deleted = await user_service.delete_user_data(session, callback.from_user.id)
        await state.clear()
        logger.info(
            "Пользователь %s удалил свои данные (были ли данные: %s)", callback.from_user.id, deleted
        )
        await show(callback, texts.delete_done_text(), None)

    return router
