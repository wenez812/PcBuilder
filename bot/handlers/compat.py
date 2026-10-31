"""⚙️ Проверить совместимость: пользователь выбирает детали из каталога, бот проверяет их."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.picker import register_flow, selected_parts
from bot.keyboards import Cb, nav_row
from bot.models import User
from bot.services import user_service
from bot.services.compatibility import check_compatibility, format_report
from bot.services.parts import CATEGORIES, CATEGORY_SHORT
from bot.utils.telegram import show

FLOW = "compat"
BACK = "cp:home"

INTRO = ("⚙️ <b>Проверка совместимости</b>\n\n"
         "Выберите детали из каталога — достаточно двух связанных (например, процессор и плата). "
         "Чем больше деталей, тем полнее проверка.\n"
         "Статусы: 🟢 совместимо · 🟡 есть ограничения · 🔴 несовместимо · ⚪ недостаточно данных")


async def render(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.update_data(pk_flow=FLOW, pk_back=BACK)
    parts = await selected_parts(session, state)
    rows = []
    for category in CATEGORIES:
        part = parts.get(category)
        label = f"✅ {part.name}" if part else f"➕ {CATEGORY_SHORT[category]}"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"pk:{category}:0")])
    if parts:
        rows.append([InlineKeyboardButton(text="♻️ Сбросить всё", callback_data="cp:reset")])
    rows.append(nav_row())
    text = INTRO
    if parts:
        report = check_compatibility(parts)
        text = f"{INTRO}\n\n———————————\n{format_report(report)}"
    await show(event, text[:4000], InlineKeyboardMarkup(inline_keyboard=rows))


def build_router() -> Router:
    router = Router(name="compat")
    register_flow(FLOW, render)

    @router.callback_query(F.data == Cb.COMPAT)
    async def cb_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await user_service.log_usage(session, db_user, "compat")
        await render(callback, state, session, db_user)

    @router.callback_query(F.data == BACK)
    async def cb_back(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.set_state(None)
        await render(callback, state, session, db_user)

    @router.callback_query(F.data == "cp:reset")
    async def cb_reset(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer("Сброшено")
        await state.update_data(parts={})
        await render(callback, state, session, db_user)

    return router
