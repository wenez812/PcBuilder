"""Универсальный выбор детали из каталога (список с пагинацией и поиском).

Любой сценарий (совместимость, апгрейд, FPS, цены, редактирование сборки) делает так:
1. кладёт в FSM-данные `pk_flow` (своё имя) и `pk_back` (callback экрана-обзора);
2. показывает кнопки категорий с callback_data `pk:<категория>:0`;
3. регистрирует функцию возврата через `register_flow`, которая вызывается после выбора детали.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.states import PickerStates
from bot.keyboards import nav_row
from bot.models import User
from bot.services import catalog_service
from bot.services.parts import CATEGORIES, CATEGORY_TITLES, Part
from bot.utils.telegram import show

PAGE_SIZE = 6
FlowReturn = Callable[[Message | CallbackQuery, FSMContext, AsyncSession, User], Awaitable[None]]
FLOW_RETURNS: dict[str, FlowReturn] = {}


def register_flow(name: str, handler: FlowReturn) -> None:
    FLOW_RETURNS[name] = handler


def _button_text(part: Part) -> str:
    name = part.name if len(part.name) <= 38 else part.name[:37] + "…"
    price = f" · {catalog_service.format_price(part.price)}" if part.price is not None else ""
    return f"{name}{price}"


def _list_keyboard(parts: list[Part], category: str, page: int, back_cb: str,
                   has_selection: bool) -> InlineKeyboardMarkup:
    pages = max(1, (len(parts) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    rows = [[InlineKeyboardButton(text=_button_text(p), callback_data=f"sel:{p.id}")]
            for p in parts[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"pk:{category}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"pk:{category}:{page + 1}"))
        rows.append(nav)
    tools = [InlineKeyboardButton(text="🔍 Поиск", callback_data=f"srch:{category}")]
    if has_selection:
        tools.append(InlineKeyboardButton(text="🗑 Убрать выбор", callback_data=f"clr:{category}"))
    rows.append(tools)
    rows.append(nav_row(back=back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_list(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession,
                    category: str, page: int = 0) -> None:
    data = await state.get_data()
    await state.update_data(pk_cat=category)
    parts = await catalog_service.list_parts(session, category)
    selected = (data.get("parts") or {}).get(category)
    title = CATEGORY_TITLES[category]
    if not parts:
        text = f"{title}\n\nКаталог этой категории пуст. Администратор может загрузить детали в админ-панели."
    else:
        text = f"{title}\n\nВыберите модель ({len(parts)} в каталоге) или воспользуйтесь поиском:"
    await show(event, text, _list_keyboard(parts, category, page, data.get("pk_back", "menu:main"),
                                           selected is not None))


async def selected_parts(session: AsyncSession, state: FSMContext) -> dict[str, Part]:
    """Выбранные в текущем сценарии детали {категория: Part}."""
    ids = (await state.get_data()).get("parts") or {}
    loaded = await catalog_service.get_parts(session, list(ids.values()))
    return {cat: loaded[pid] for cat, pid in ids.items() if pid in loaded}


async def _finish_selection(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession,
                            user: User, category: str, part: Part) -> None:
    data = await state.get_data()
    parts = dict(data.get("parts") or {})
    parts[category] = part.id
    await state.update_data(parts=parts)
    await state.set_state(None)
    flow = data.get("pk_flow")
    handler = FLOW_RETURNS.get(flow or "")
    if handler is None:
        await show(event, "Сценарий устарел. Вернитесь в главное меню.",
                   InlineKeyboardMarkup(inline_keyboard=[nav_row()]))
        return
    await handler(event, state, session, user)


def build_router() -> Router:
    router = Router(name="picker")

    @router.callback_query(F.data == "noop")
    async def cb_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    @router.callback_query(F.data.regexp(r"^pk:[a-z]+:\d+$"))
    async def cb_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        _, category, page = callback.data.split(":")
        if category not in CATEGORIES:
            await callback.answer("Неизвестная категория", show_alert=True)
            return
        await callback.answer()
        await state.set_state(None)
        await show_list(callback, state, session, category, int(page))

    @router.callback_query(F.data.regexp(r"^sel:\d+$"))
    async def cb_select(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        part = await catalog_service.get_part(session, int(callback.data.split(":")[1]))
        category = (await state.get_data()).get("pk_cat")
        if part is None or part.category != category:
            await callback.answer("Эта деталь больше недоступна. Откройте список заново.", show_alert=True)
            return
        await callback.answer(f"Выбрано: {part.name}"[:190])
        await _finish_selection(callback, state, session, db_user, category, part)

    @router.callback_query(F.data.regexp(r"^clr:[a-z]+$"))
    async def cb_clear(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        category = callback.data.split(":")[1]
        data = await state.get_data()
        parts = dict(data.get("parts") or {})
        parts.pop(category, None)
        await state.update_data(parts=parts)
        await callback.answer("Выбор убран")
        handler = FLOW_RETURNS.get(data.get("pk_flow") or "")
        if handler is not None:
            await handler(callback, state, session, db_user)

    @router.callback_query(F.data.regexp(r"^srch:[a-z]+$"))
    async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
        category = callback.data.split(":")[1]
        await callback.answer()
        await state.update_data(pk_cat=category)
        await state.set_state(PickerStates.search)
        back = [InlineKeyboardButton(text="⬅️ К списку", callback_data=f"pk:{category}:0")]
        await show(callback, f"🔍 {CATEGORY_TITLES[category]}\n\nНапишите часть названия, например: <i>5060</i> "
                             f"или <i>ryzen 5</i>.", InlineKeyboardMarkup(inline_keyboard=[back, nav_row()]))

    @router.message(StateFilter(PickerStates.search), F.text, ~F.text.startswith("/"))
    async def msg_search(message: Message, state: FSMContext, session: AsyncSession) -> None:
        category = (await state.get_data()).get("pk_cat")
        if category not in CATEGORIES:
            await state.set_state(None)
            await message.answer("Сценарий устарел. Откройте /menu.")
            return
        found = catalog_service.search_parts(await catalog_service.list_parts(session, category),
                                             message.text[:80])
        back = (await state.get_data()).get("pk_back", "menu:main")
        if not found:
            await message.answer(f"Ничего не нашлось по запросу «{escape(message.text[:80])}». "
                                 "Попробуйте другое слово или откройте полный список.",
                                 reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                     [InlineKeyboardButton(text="📋 Весь список", callback_data=f"pk:{category}:0")],
                                     nav_row(back=back)]))
            return
        rows = [[InlineKeyboardButton(text=_button_text(p), callback_data=f"sel:{p.id}")] for p in found]
        rows.append([InlineKeyboardButton(text="📋 Весь список", callback_data=f"pk:{category}:0")])
        rows.append(nav_row(back=back))
        await state.set_state(None)
        await message.answer(f"Нашлось: {len(found)}. Выберите модель:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    return router
