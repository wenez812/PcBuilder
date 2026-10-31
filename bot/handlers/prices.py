"""💰 Сравнить цены: предложения магазинов по детали и статус источников."""
from __future__ import annotations

from datetime import timedelta
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.picker import register_flow, selected_parts
from bot.integrations import STATUS_LABEL, PriceSource
from bot.keyboards import Cb, nav_row
from bot.models import User
from bot.models.base import utcnow
from bot.services import catalog_service, user_service
from bot.services.catalog_service import PRICE_MAX_AGE_DAYS, format_price
from bot.services.parts import CATEGORIES, CATEGORY_SHORT
from bot.utils.render import specs_line
from bot.utils.telegram import show

FLOW = "prices"
HOME = "pr:home"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]], back: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, nav_row(back=back)])


async def render_home(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.update_data(pk_flow=FLOW, pk_back=HOME, parts={})
    rows = []
    cats = list(CATEGORIES)
    for i in range(0, len(cats), 2):
        rows.append([_btn(CATEGORY_SHORT[c], f"pk:{c}:0") for c in cats[i:i + 2]])
    rows.append([_btn("📡 Источники цен", "pr:sources")])
    total, priced, stale = await catalog_service.price_coverage(session)
    text = ("💰 <b>Сравнение цен</b>\n\nВыберите категорию и модель — покажу предложения магазинов.\n\n"
            f"📊 Деталей в каталоге: {total}, со свежей ценой: {priced}")
    if stale:
        text += f", с устаревшей: {stale}"
    text += "\n\nℹ️ Я показываю только загруженные данные. Если цены нет — так и пишу."
    await show(event, text, _kb(rows))


async def render_part_offers(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    parts = await selected_parts(session, state)
    category = (await state.get_data()).get("pk_cat")
    part = parts.get(category)
    if part is None:
        await render_home(event, state, session, user)
        return
    offers = await catalog_service.get_offers(session, part.id)
    lines = [f"💰 <b>{escape(part.name)}</b>", f"<i>{specs_line(part)}</i>\n"]
    fresh_limit = utcnow() - timedelta(days=PRICE_MAX_AGE_DAYS)
    if not offers:
        lines.append("📭 Нет данных о ценах на эту деталь.\nЗагрузите цены в админ-панели или подключите источник "
                     "(см. «Источники цен»). Придумывать цены я не буду.")
    else:
        usable = []
        for o in offers:
            stock = "в наличии" if o.in_stock else "нет в наличии"
            fresh = o.updated_at >= fresh_limit
            link = f' · <a href="{escape(o.url, quote=True)}">ссылка</a>' if o.url else ""
            note = "" if fresh else " ⚠️ данные устарели"
            lines.append(f"🏪 <b>{escape(o.shop)}</b> — {format_price(o.price)} · {stock} · "
                         f"обновлено {o.updated_at.strftime('%d.%m.%Y')}{link}{note}")
            if o.in_stock and fresh:
                usable.append(o)
        if usable:
            best = min(usable, key=lambda x: x.price)
            lines.append(f"\n🏆 <b>Лучшая цена: {format_price(best.price)}</b> ({escape(best.shop)})")
        else:
            lines.append("\nАктуальных предложений «в наличии» нет — лучшую цену назвать не могу.")
    await show(event, "\n".join(lines)[:4000], _kb([[_btn("🔎 Другая деталь", HOME)], [_btn("📡 Источники цен", "pr:sources")]]))


def build_router() -> Router:
    router = Router(name="prices")
    register_flow(FLOW, render_part_offers)

    @router.callback_query(F.data == Cb.PRICES)
    async def cb_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await user_service.log_usage(session, db_user, "prices")
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == HOME)
    async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.set_state(None)
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == "pr:sources")
    async def cb_sources(callback: CallbackQuery, price_sources: list[PriceSource]) -> None:
        await callback.answer()
        lines = ["📡 <b>Источники цен</b>\n"]
        for source in price_sources:
            lines.append(f"{STATUS_LABEL[source.status]} <b>{escape(source.title)}</b>\n   {escape(source.status_reason)}")
        lines.append("\n📄 Также цены можно загрузить вручную: администратор импортирует CSV в админ-панели.")
        lines.append("Магазины без официального API помечены как не подключённые — демонстрационные цены я не показываю.")
        await show(callback, "\n".join(lines), _kb([], back=HOME))

    return router
