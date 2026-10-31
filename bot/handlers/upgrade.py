"""🔧 Улучшить мой ПК: текущая конфигурация -> бюджет -> цель -> три варианта апгрейда."""
from __future__ import annotations

import asyncio
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.picker import register_flow, selected_parts
from bot.handlers.states import UpgradeStates
from bot.keyboards import Cb, nav_row
from bot.models import User
from bot.services import build_service, catalog_service, user_service
from bot.services.parts import CATEGORY_SHORT
from bot.services.upgrade import GOALS, UpgradeResult, analyze_upgrade, action_title
from bot.utils import texts
from bot.utils.parsing import InputError, parse_budget
from bot.utils.telegram import show

FLOW = "upgrade"
HOME = "up:home"
INPUT_CATEGORIES = ("cpu", "gpu", "motherboard", "ram", "psu", "ssd")
BUDGET_CHOICES = [10_000, 25_000, 50_000, 100_000]
RESOLUTIONS = ["1080p", "1440p", "4K"]


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]], back: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, nav_row(back=back)])


def _fmt(value: int) -> str:
    return catalog_service.format_price(value)


async def render_home(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.update_data(pk_flow=FLOW, pk_back=HOME)
    parts = await selected_parts(session, state)
    rows = []
    for category in INPUT_CATEGORIES:
        part = parts.get(category)
        label = f"✅ {part.name}" if part else f"➕ {CATEGORY_SHORT[category]}"
        rows.append([_btn(label[:60], f"pk:{category}:0")])
    rows.append([_btn("📸 Загрузить фото CPU-Z / GPU-Z", "up:photo")])
    if "cpu" in parts or "gpu" in parts:
        rows.append([_btn("➡️ Далее: бюджет и цель", "up:next")])
    text = ("🔧 <b>Улучшить мой ПК</b>\n\nУкажите, что установлено сейчас. Нужны как минимум процессор и видеокарта; "
            "остальное повышает точность (плата и память — для проверки совместимости, блок питания — для "
            "расчёта нагрузки).\n\nЕсли вашей модели нет в списке, бот не станет угадывать её характеристики — "
            "попросите администратора добавить деталь в каталог.")
    await show(event, text, _kb(rows, back=None))


async def _ask_budget(event: Message | CallbackQuery) -> None:
    rows = [[_btn(_fmt(v), f"up:b:{v}") for v in BUDGET_CHOICES[:2]], [_btn(_fmt(v), f"up:b:{v}") for v in BUDGET_CHOICES[2:]],
            [_btn("✏️ Своя сумма", "up:b:custom")]]
    await show(event, "💰 <b>Бюджет на модернизацию</b>\n\nСколько готовы потратить на новые детали?", _kb(rows, back=HOME))


async def _ask_goal(event: Message | CallbackQuery) -> None:
    rows = [[_btn(label, f"up:g:{key}")] for key, label in GOALS.items()]
    await show(event, "🎯 <b>Что хочешь улучшить?</b>", _kb(rows, back="up:next"))


def _serialize(result: UpgradeResult) -> dict:
    return {
        "goal": result.goal, "resolution": result.resolution, "balance": result.balance_note,
        "problems": result.problems, "message": result.message,
        "options": [{
            "label": o.label, "cost": o.cost, "benefit_text": o.benefit_text, "notes": o.notes,
            "emoji": o.report.emoji, "status": o.report.title,
            "actions": [{"cat": a.category, "old": a.old.id if a.old else None, "new": a.new.id, "reason": a.reason}
                        for a in o.actions],
        } for o in result.options],
    }


async def run_analysis(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    current = await selected_parts(session, state)
    await show(event, "⏳ Анализирую конфигурацию и ищу варианты…", None)
    catalog = await catalog_service.list_parts(session)
    result = await asyncio.to_thread(analyze_upgrade, current, catalog, data.get("up_goal", "fps"),
                                     data.get("up_res", "1080p"), data.get("up_budget", 0))
    await state.update_data(up_result=_serialize(result))
    await user_service.log_usage(session, user, "upgrade")
    names = ", ".join(p.name for p in (current.get("cpu"), current.get("gpu")) if p)
    await build_service.add_history(session, user, "upgrade",
                                    f"{GOALS[result.goal]}: {names}", {"goal": result.goal, "budget": data.get("up_budget")},
                                    result.options[0].cost if result.options else None)
    await render_summary(event, state)


async def render_summary(event: Message | CallbackQuery, state: FSMContext) -> None:
    res = (await state.get_data()).get("up_result")
    if not res:
        await show(event, "Результат устарел. Начните заново.", _kb([[_btn("🔧 Заново", Cb.UPGRADE)]]))
        return
    lines = [f"🔧 <b>Модернизация: {escape(GOALS[res['goal']])}</b>"]
    if res["balance"]:
        lines.append(f"\n⚖️ {escape(res['balance'])}")
    if res["problems"]:
        lines.append("\n⚠️ <b>Проблемы текущей конфигурации</b>")
        lines.extend(f"• {escape(p)}" for p in res["problems"][:4])
    rows = []
    if res["message"]:
        lines.append(f"\n📭 {escape(res['message'])}")
    for i, opt in enumerate(res["options"]):
        lines.append(f"\n{opt['label']} — <b>{_fmt(opt['cost'])}</b>")
        lines.append(f"   {escape(opt['benefit_text'])}")
        rows.append([_btn(f"{opt['label']} · {_fmt(opt['cost'])}", f"up:o:{i}")])
    if res["options"]:
        lines.append("\nℹ️ Прирост — оценка по классам каталога, а не замер. Реальные FPS смотрите в разделе «FPS в играх».")
    rows.append([_btn("🔁 Другая цель", "up:next")])
    await show(event, "\n".join(lines)[:4000], _kb(rows, back=None))


async def render_option(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, index: int) -> None:
    res = (await state.get_data()).get("up_result") or {}
    options = res.get("options") or []
    if not 0 <= index < len(options):
        await render_summary(event, state)
        return
    opt = options[index]
    ids = [a["new"] for a in opt["actions"]] + [a["old"] for a in opt["actions"] if a["old"]]
    parts = await catalog_service.get_parts(session, ids)
    lines = [f"<b>{opt['label']}</b> — {_fmt(opt['cost'])}\n"]
    for a in opt["actions"]:
        new = parts.get(a["new"])
        old = parts.get(a["old"]) if a["old"] else None
        if new is None:
            continue
        title = CATEGORY_SHORT.get(a["cat"], a["cat"])
        before = f"{escape(old.name)} → " if old else "добавить: "
        lines.append(f"🔹 <b>{title}</b>: {before}<b>{escape(new.name)}</b> — {_fmt(new.price or 0)}")
        lines.append(f"   💡 {escape(a['reason'])}")
    lines.append(f"\n📈 <b>Ожидаемый эффект:</b> {escape(opt['benefit_text'])}")
    lines.append(f"{opt['emoji']} Совместимость новой конфигурации: {opt['status']}")
    if opt["notes"]:
        lines.append("\n⚠️ <b>Ограничения</b>")
        lines.extend(f"• {escape(n)}" for n in opt["notes"][:5])
    lines.append("\nℹ️ Цены — лучшие актуальные предложения из каталога. Старые детали в расчёт не включены.")
    await show(event, "\n".join(lines)[:4000], _kb([[_btn("📋 Все варианты", "up:res")]], back=None))


def build_router() -> Router:
    router = Router(name="upgrade")
    register_flow(FLOW, render_home)

    @router.callback_query(F.data == Cb.UPGRADE)
    async def cb_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await state.update_data(parts={})
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == HOME)
    async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.set_state(None)
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == "up:photo")
    async def cb_photo(callback: CallbackQuery) -> None:
        await callback.answer()
        await show(callback, texts.PHOTO_NOT_AVAILABLE, _kb([], back=HOME))

    @router.callback_query(F.data == "up:next")
    async def cb_next(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(None)
        if (await state.get_data()).get("up_budget"):
            await _ask_goal(callback)
        else:
            await _ask_budget(callback)

    @router.callback_query(F.data.regexp(r"^up:b:\d+$"))
    async def cb_budget(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.update_data(up_budget=int(callback.data.split(":")[2]))
        await _ask_goal(callback)

    @router.callback_query(F.data == "up:b:custom")
    async def cb_budget_custom(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(UpgradeStates.budget)
        await show(callback, "✏️ Напишите сумму в рублях, например: <i>40000</i> или <i>40к</i>.", _kb([], back=HOME))

    @router.message(StateFilter(UpgradeStates.budget), F.text, ~F.text.startswith("/"))
    async def msg_budget(message: Message, state: FSMContext) -> None:
        try:
            value = parse_budget(message.text)
        except InputError as exc:
            await message.answer(f"❌ {escape(str(exc))}")
            return
        await state.set_state(None)
        await state.update_data(up_budget=value)
        await _ask_goal(message)

    @router.callback_query(F.data.regexp(r"^up:g:[a-z]+$"))
    async def cb_goal(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        goal = callback.data.split(":")[2]
        if goal not in GOALS:
            await callback.answer("Неизвестная цель", show_alert=True)
            return
        await callback.answer()
        await state.update_data(up_goal=goal)
        if goal in {"fps", "all"}:
            rows = [[_btn(r, f"up:r:{r}") for r in RESOLUTIONS]]
            await show(callback, "🖥 <b>Разрешение монитора?</b>\nОт него зависит, что ограничивает FPS.", _kb(rows, back="up:next"))
        else:
            await state.update_data(up_res="1080p")
            await run_analysis(callback, state, session, db_user)

    @router.callback_query(F.data.regexp(r"^up:r:(1080p|1440p|4K)$"))
    async def cb_resolution(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.update_data(up_res=callback.data.split(":")[2])
        await run_analysis(callback, state, session, db_user)

    @router.callback_query(F.data == "up:res")
    async def cb_results(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await render_summary(callback, state)

    @router.callback_query(F.data.regexp(r"^up:o:\d+$"))
    async def cb_option(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        await render_option(callback, state, session, int(callback.data.split(":")[2]))

    return router
