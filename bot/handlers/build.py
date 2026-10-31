"""🖥 Собрать ПК: анкета, запрос «одной фразой», показ вариантов и сохранение сборки."""
from __future__ import annotations

import asyncio
import logging
from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.states import BuildStates
from bot.keyboards import Cb, nav_row
from bot.models import User
from bot.services import build_service, catalog_service, fps_service, user_service
from bot.services.build_service import BuildError
from bot.services.builder import GAMES, PRIORITIES, PURPOSES, BuildRequest, BuildResult, select_builds
from bot.services.compatibility import Severity, format_report
from bot.utils.parsing import InputError, PhraseRequest, parse_budget, parse_phrase
from bot.utils.render import render_build
from bot.utils.telegram import show

logger = logging.getLogger(__name__)

BUDGETS = [(30_000, "До 30 000 ₽"), (50_000, "30 000–50 000 ₽"), (80_000, "50 000–80 000 ₽"),
           (120_000, "80 000–120 000 ₽"), (200_000, "120 000–200 000 ₽")]
RESOLUTIONS = ["1080p", "1440p", "4K"]
CPU_VENDORS = {"any": "Без разницы", "intel": "Intel", "amd": "AMD"}
GPU_VENDORS = {"any": "Без разницы", "nvidia": "NVIDIA", "amd": "AMD"}
VENDOR_TITLES = {"any": "без разницы", "intel": "Intel", "amd": "AMD", "nvidia": "NVIDIA"}

DEFAULTS = {"purpose": "gaming", "games": [], "resolution": "1080p", "priority": "balanced",
            "cpu_vendor": "any", "gpu_vendor": "any", "allow_used": False, "min_ram_gb": None, "target_fps": None}


def steps_for(data: dict) -> list[str]:
    steps = ["budget", "purpose"]
    if data.get("purpose") == "gaming":
        steps += ["games", "resolution"]
    return steps + ["priority", "cpu_vendor", "gpu_vendor", "condition"]


def progress(step: str, data: dict) -> str:
    steps = steps_for(data)
    index = steps.index(step) + 1 if step in steps else 1
    return f"Шаг {index}/{len(steps)}  " + "▰" * index + "▱" * (len(steps) - index)


def _kb(rows: list[list[InlineKeyboardButton]], back: bool = True) -> InlineKeyboardMarkup:
    rows = [*rows, nav_row(back="bd:back" if back else None)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _fmt(value: int) -> str:
    return f"{value:,}".replace(",", " ")


async def ask(event: Message | CallbackQuery, state: FSMContext, step: str) -> None:
    data = (await state.get_data()).get("bd", {})
    await state.update_data(bd_step=step)
    head = f"🖥 <b>Подбор ПК</b>\n<code>{progress(step, data)}</code>\n\n"
    if step == "budget":
        rows = [[_btn(label, f"bd:b:{value}")] for value, label in BUDGETS]
        rows.append([_btn("✏️ Свой бюджет", "bd:b:custom")])
        rows.append([_btn("💬 Описать одной фразой", "bd:phrase")])
        await show(event, head + "💰 <b>Какой у тебя бюджет?</b>\nПодберу максимум в пределах верхней границы.",
                   _kb(rows, back=False))
    elif step == "purpose":
        items = list(PURPOSES.items())
        rows = [[_btn(items[i][1], f"bd:p:{items[i][0]}")] + ([_btn(items[i + 1][1], f"bd:p:{items[i + 1][0]}")]
                                                              if i + 1 < len(items) else [])
                for i in range(0, len(items), 2)]
        await show(event, head + "🎯 <b>Для чего нужен компьютер?</b>", _kb(rows))
    elif step == "games":
        chosen = set(data.get("games") or [])
        rows = [[_btn(("✅ " if g in chosen else "▫️ ") + g, f"bd:g:{i}")] for i, g in enumerate(GAMES)]
        rows.append([_btn("➡️ Далее", "bd:gdone")])
        await show(event, head + "🎮 <b>В какие игры будешь играть?</b>\nМожно выбрать несколько.", _kb(rows))
    elif step == "resolution":
        rows = [[_btn(r, f"bd:r:{r}") for r in RESOLUTIONS]]
        await show(event, head + "🖥 <b>Разрешение монитора?</b>", _kb(rows))
    elif step == "priority":
        rows = [[_btn(label, f"bd:pr:{key}")] for key, label in PRIORITIES.items()]
        await show(event, head + "⚖️ <b>Что важнее всего?</b>", _kb(rows))
    elif step == "cpu_vendor":
        rows = [[_btn(label, f"bd:cv:{key}") for key, label in CPU_VENDORS.items()]]
        await show(event, head + "🧠 <b>Какой процессор предпочитаешь?</b>", _kb(rows))
    elif step == "gpu_vendor":
        rows = [[_btn(label, f"bd:gv:{key}") for key, label in GPU_VENDORS.items()]]
        await show(event, head + "🎮 <b>Какая видеокарта предпочтительнее?</b>", _kb(rows))
    elif step == "condition":
        rows = [[_btn("🆕 Только новые", "bd:c:new")], [_btn("♻️ Допускаются б/у детали", "bd:c:used")]]
        await show(event, head + "📦 <b>Новый ПК или допускаются б/у детали?</b>", _kb(rows))


async def _set(state: FSMContext, **values) -> dict:
    data = dict((await state.get_data()).get("bd", {}))
    data.update(values)
    await state.update_data(bd=data)
    return data


async def _advance(event: Message | CallbackQuery, state: FSMContext, current: str) -> None:
    data = (await state.get_data()).get("bd", {})
    steps = steps_for(data)
    index = steps.index(current) if current in steps else 0
    if index + 1 < len(steps):
        await ask(event, state, steps[index + 1])
    else:
        await ask_confirm(event, state)


def request_from_data(data: dict) -> BuildRequest:
    merged = {**DEFAULTS, **data}
    if merged["purpose"] != "gaming":
        merged["games"], merged["resolution"] = [], merged["resolution"] if merged["purpose"] == "universal" else "1080p"
    return BuildRequest.from_params(merged)


def summary_text(data: dict) -> str:
    req = request_from_data(data)
    lines = [f"💰 Бюджет: до <b>{_fmt(req.budget)} ₽</b>", f"🎯 Назначение: {PURPOSES[req.purpose]}"]
    if req.games:
        lines.append("🎮 Игры: " + ", ".join(req.games))
    if req.purpose in {"gaming", "universal"}:
        lines.append(f"🖥 Разрешение: {req.resolution}")
    lines.append(f"⚖️ Приоритет: {PRIORITIES[req.priority]}")
    lines.append(f"🧠 CPU: {VENDOR_TITLES[req.cpu_vendor]} · 🎮 GPU: {VENDOR_TITLES[req.gpu_vendor]}")
    lines.append("📦 " + ("Допускаются б/у детали" if req.allow_used else "Только новые детали"))
    if req.min_ram_gb:
        lines.append(f"💾 Оперативной памяти не менее {req.min_ram_gb} ГБ")
    return "\n".join(lines)


async def ask_confirm(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = (await state.get_data()).get("bd", {})
    await state.update_data(bd_step="confirm")
    await show(event, "📋 <b>Проверь параметры</b>\n\n" + summary_text(data),
               _kb([[_btn("🚀 Подобрать сборку", "bd:go")], [_btn("✏️ Изменить", "bd:restart")]]))


# ---------------------------------------------------------------- подбор и показ результата

async def _run(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = (await state.get_data()).get("bd", {})
    req = request_from_data(data)
    await show(event, "⏳ Подбираю комплектующие и проверяю совместимость…", None)
    parts = await catalog_service.list_parts(session)
    result: BuildResult = await asyncio.to_thread(select_builds, parts, req)

    stored = [{"label": o.label, "ids": o.part_ids, "total": o.total} for o in result.options]
    await state.update_data(bd_result={"status": result.status, "options": stored, "notes": result.notes,
                                       "min_total": result.min_total, "missing": result.missing_data})
    summary = (f"{PURPOSES[req.purpose]}, до {_fmt(req.budget)} ₽" +
               (f" → {_fmt(result.options[0].total)} ₽" if result.options else ""))
    await build_service.add_history(session, user, "build", summary, req.to_params(),
                                    result.options[0].total if result.options else None)
    await user_service.log_usage(session, user, "build")
    await render_result(event, state)


async def render_result(event: Message | CallbackQuery, state: FSMContext) -> None:
    res = (await state.get_data()).get("bd_result")
    if not res:
        await show(event, "Результат устарел. Начните подбор заново.", _kb([[_btn("🖥 Новый подбор", "bd:restart")]], back=False))
        return
    notes = "\n".join(f"ℹ️ {escape(n)}" for n in res["notes"])
    if res["status"] == "no_data":
        text = "📭 <b>Не могу подобрать сборку</b>\n\n" + (notes or "Нет данных.")
        text += "\n\nЭто не ошибка бота: цены я не придумываю. Администратор может загрузить их в админ-панели."
        await show(event, text, _kb([[_btn("✏️ Изменить параметры", "bd:restart")]], back=False))
        return
    lines = []
    if res["status"] == "over_budget":
        lines.append("⚠️ <b>В бюджет не укладываюсь</b>")
    else:
        lines.append("✅ <b>Сборки готовы</b>")
    if notes:
        lines.append("\n" + notes)
    lines.append("")
    rows = []
    for i, opt in enumerate(res["options"]):
        lines.append(f"{opt['label']} — <b>{_fmt(opt['total'])} ₽</b>")
        rows.append([_btn(f"{opt['label']} · {_fmt(opt['total'])} ₽", f"bd:o:{i}")])
    rows.append([_btn("✏️ Изменить параметры", "bd:restart")])
    await show(event, "\n".join(lines), _kb(rows, back=False))


async def render_option(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, index: int) -> None:
    data = await state.get_data()
    res = data.get("bd_result") or {}
    options = res.get("options") or []
    if not 0 <= index < len(options):
        await render_result(event, state)
        return
    opt = options[index]
    loaded = await catalog_service.get_parts(session, list(opt["ids"].values()))
    parts = {cat: loaded[pid] for cat, pid in opt["ids"].items() if pid in loaded}
    if len(parts) != len(opt["ids"]):
        await show(event, "Часть деталей исчезла из каталога. Запустите подбор заново.",
                   _kb([[_btn("🔁 Подобрать заново", "bd:go")]], back=False))
        return
    from bot.services.builder import BuildRequest as _BR, explain
    from bot.services.compatibility import check_compatibility

    req = request_from_data(data.get("bd", {}))
    report = check_compatibility(parts)
    reasons = {cat: explain(cat, p, parts, req) for cat, p in parts.items()}
    total = sum(p.price for p in parts.values() if p.price is not None)
    text = render_build(parts, title=opt["label"], reasons=reasons, total=total, report=report)

    warns = [i.text for i in report.issues if i.severity == Severity.WARN]
    if warns:
        text += "\n\n🟡 <b>На что обратить внимание</b>\n" + "\n".join(f"• {escape(w)}" for w in warns[:4])
    if res.get("status") == "over_budget":
        text += f"\n\n⚠️ Эта сборка дороже бюджета ({_fmt(req.budget)} ₽): решение о покупке за вами."
    target = (data.get("bd") or {}).get("target_fps")
    if target and req.games:
        text += "\n\n" + await _fps_check(session, parts, req, target)
    rows = [[_btn("💾 Сохранить сборку", f"bd:save:{index}")],
            [_btn("⚙️ Подробно о совместимости", f"bd:cmp:{index}")],
            [_btn("📋 Все варианты", "bd:res")]]
    await show(event, text[:4000], _kb(rows, back=False))


async def _fps_check(session: AsyncSession, parts: dict, req: BuildRequest, target: int) -> str:
    lines = [f"🎯 <b>Цель: {target} FPS</b>"]
    for game in req.games[:3]:
        rows = await fps_service.find_for_pair(session, parts["cpu"].id, parts["gpu"].id, game, req.resolution)
        if not rows:
            lines.append(f"• {escape(game)}: нет замеров для этой связки на {req.resolution} — гарантировать цель не могу.")
        else:
            best = rows[0]
            lines.append(f"• {escape(game)}: замер {best.avg_fps} FPS ({escape(best.preset)}, {escape(best.source_name)})"
                         f" — {'цель достигнута' if best.avg_fps >= target else 'ниже цели'} в условиях теста.")
    return "\n".join(lines)


def default_name(data: dict) -> str:
    req = request_from_data(data)
    return f"{PURPOSES[req.purpose]} ПК {_fmt(req.budget)} ₽"


def build_router() -> Router:
    router = Router(name="build")

    @router.callback_query(F.data == Cb.BUILD)
    async def cb_start(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        await state.update_data(bd={})
        await ask(callback, state, "budget")

    @router.callback_query(F.data == "bd:restart")
    async def cb_restart(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(None)
        await state.update_data(bd={}, bd_result=None)
        await ask(callback, state, "budget")

    @router.callback_query(F.data == "bd:back")
    async def cb_back(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        data = await state.get_data()
        steps = steps_for(data.get("bd", {}))
        current = data.get("bd_step")
        if current == "confirm":
            await ask(callback, state, steps[-1])
        elif current in steps and steps.index(current) > 0:
            await ask(callback, state, steps[steps.index(current) - 1])
        else:
            await ask(callback, state, "budget")

    @router.callback_query(F.data.regexp(r"^bd:b:\d+$"))
    async def cb_budget(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _set(state, budget=int(callback.data.split(":")[2]))
        await _advance(callback, state, "budget")

    @router.callback_query(F.data == "bd:b:custom")
    async def cb_budget_custom(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(BuildStates.custom_budget)
        await show(callback, "✏️ <b>Свой бюджет</b>\n\nНапишите сумму в рублях, например: <i>85000</i> или <i>85к</i>.",
                   _kb([], back=False))

    @router.message(StateFilter(BuildStates.custom_budget), F.text, ~F.text.startswith("/"))
    async def msg_budget(message: Message, state: FSMContext) -> None:
        try:
            value = parse_budget(message.text)
        except InputError as exc:
            await message.answer(f"❌ {escape(str(exc))}")
            return
        await state.set_state(None)
        await _set(state, budget=value)
        await _advance(message, state, "budget")

    @router.callback_query(F.data.regexp(r"^bd:p:[a-z0-9]+$"))
    async def cb_purpose(callback: CallbackQuery, state: FSMContext) -> None:
        key = callback.data.split(":")[2]
        if key not in PURPOSES:
            await callback.answer("Неизвестный вариант", show_alert=True)
            return
        await callback.answer()
        await _set(state, purpose=key, games=[] if key != "gaming" else (await state.get_data()).get("bd", {}).get("games", []))
        await _advance(callback, state, "purpose")

    @router.callback_query(F.data.regexp(r"^bd:g:\d+$"))
    async def cb_game_toggle(callback: CallbackQuery, state: FSMContext) -> None:
        index = int(callback.data.split(":")[2])
        if not 0 <= index < len(GAMES):
            await callback.answer()
            return
        await callback.answer()
        games = list((await state.get_data()).get("bd", {}).get("games") or [])
        game = GAMES[index]
        games.remove(game) if game in games else games.append(game)
        await _set(state, games=games)
        await ask(callback, state, "games")

    @router.callback_query(F.data == "bd:gdone")
    async def cb_games_done(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        if not (await state.get_data()).get("bd", {}).get("games"):
            await _set(state, games=["Другие игры"])
        await _advance(callback, state, "games")

    @router.callback_query(F.data.regexp(r"^bd:r:(1080p|1440p|4K)$"))
    async def cb_resolution(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _set(state, resolution=callback.data.split(":")[2])
        await _advance(callback, state, "resolution")

    @router.callback_query(F.data.regexp(r"^bd:pr:[a-z]+$"))
    async def cb_priority(callback: CallbackQuery, state: FSMContext) -> None:
        key = callback.data.split(":")[2]
        if key not in PRIORITIES:
            await callback.answer("Неизвестный вариант", show_alert=True)
            return
        await callback.answer()
        await _set(state, priority=key)
        await _advance(callback, state, "priority")

    @router.callback_query(F.data.regexp(r"^bd:cv:(any|intel|amd)$"))
    async def cb_cpu_vendor(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _set(state, cpu_vendor=callback.data.split(":")[2])
        await _advance(callback, state, "cpu_vendor")

    @router.callback_query(F.data.regexp(r"^bd:gv:(any|nvidia|amd)$"))
    async def cb_gpu_vendor(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _set(state, gpu_vendor=callback.data.split(":")[2])
        await _advance(callback, state, "gpu_vendor")

    @router.callback_query(F.data.regexp(r"^bd:c:(new|used)$"))
    async def cb_condition(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await _set(state, allow_used=callback.data.endswith("used"))
        await _advance(callback, state, "condition")

    @router.callback_query(F.data == "bd:go")
    async def cb_go(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        if not (await state.get_data()).get("bd", {}).get("budget"):
            await ask(callback, state, "budget")
            return
        await _run(callback, state, session, db_user)

    @router.callback_query(F.data == "bd:res")
    async def cb_results(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await render_result(callback, state)

    @router.callback_query(F.data.regexp(r"^bd:o:\d+$"))
    async def cb_option(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        await render_option(callback, state, session, int(callback.data.split(":")[2]))

    @router.callback_query(F.data.regexp(r"^bd:cmp:\d+$"))
    async def cb_compat_detail(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        index = int(callback.data.split(":")[2])
        options = ((await state.get_data()).get("bd_result") or {}).get("options") or []
        if not 0 <= index < len(options):
            return
        loaded = await catalog_service.get_parts(session, list(options[index]["ids"].values()))
        parts = {cat: loaded[pid] for cat, pid in options[index]["ids"].items() if pid in loaded}
        from bot.services.compatibility import check_compatibility
        await show(callback, format_report(check_compatibility(parts))[:4000],
                   _kb([[_btn("⬅️ К сборке", f"bd:o:{index}")]], back=False))

    # ---- одной фразой
    @router.callback_query(F.data == "bd:phrase")
    async def cb_phrase(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(BuildStates.phrase)
        await show(callback,
                   "💬 <b>Собрать ПК одной фразой</b>\n\nОпишите, что нужно, своими словами. Например:\n"
                   "<i>Хочу компьютер за 100к, чтобы CS2 шла в 300 FPS, желательно NVIDIA и 32 ГБ оперативки</i>\n\n"
                   "Я определю бюджет, задачи и предпочтения, а подбор выполню по проверяемому каталогу. "
                   "Обещания по FPS я проверяю только по базе реальных замеров.",
                   _kb([], back=False))

    @router.message(StateFilter(BuildStates.phrase), F.text, ~F.text.startswith("/"))
    async def msg_phrase(message: Message, state: FSMContext) -> None:
        parsed: PhraseRequest = parse_phrase(message.text[:600])
        if parsed.budget is None:
            await message.answer("💰 Не нашёл бюджет в сообщении. Допишите сумму, например: <i>за 90к</i>, "
                                 "и отправьте фразу ещё раз.")
            return
        await state.set_state(None)
        purpose = parsed.purpose or ("gaming" if parsed.games else "universal")
        values = dict(budget=parsed.budget, purpose=purpose, games=parsed.games,
                      resolution=parsed.resolution or "1080p", priority=parsed.priority or "balanced",
                      cpu_vendor=parsed.cpu_vendor, gpu_vendor=parsed.gpu_vendor, allow_used=parsed.allow_used,
                      min_ram_gb=parsed.min_ram_gb, target_fps=parsed.target_fps)
        await state.update_data(bd=values, bd_result=None)
        extra = []
        if parsed.target_fps:
            extra.append(f"🎯 Цель {parsed.target_fps} FPS: гарантировать не могу, но сверю с базой замеров.")
        if parsed.resolution is None and purpose == "gaming":
            extra.append("🖥 Разрешение не указано — беру 1080p.")
        extra.extend(f"⚠️ Не учитываю: {escape(i)}" for i in parsed.ignored)
        text = "💬 <b>Я понял запрос так:</b>\n\n" + summary_text(values)
        if extra:
            text += "\n\n" + "\n".join(extra)
        await state.update_data(bd_step="confirm")
        await message.answer(text, reply_markup=_kb([[_btn("🚀 Подобрать сборку", "bd:go")],
                                                     [_btn("✏️ Уточнить в анкете", "bd:restart")]], back=False))

    # ---- сохранение
    @router.callback_query(F.data.regexp(r"^bd:save:\d+$"))
    async def cb_save(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        index = int(callback.data.split(":")[2])
        data = await state.get_data()
        await state.update_data(save_index=index)
        await state.set_state(BuildStates.save_name)
        suggestion = default_name(data.get("bd", {}))
        await show(callback, "💾 <b>Как назвать сборку?</b>\n\nНапишите название (до 64 символов) "
                             "или используйте предложенное.",
                   _kb([[_btn(f"✅ {suggestion}"[:60], "bd:savedef")], [_btn("⬅️ К сборке", f"bd:o:{index}")]], back=False))

    async def _persist(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession,
                       user: User, name: str) -> None:
        data = await state.get_data()
        res = (data.get("bd_result") or {}).get("options") or []
        index = data.get("save_index", 0)
        if not 0 <= index < len(res):
            await show(event, "Сборка устарела. Запустите подбор заново.", _kb([], back=False))
            return
        loaded = await catalog_service.get_parts(session, list(res[index]["ids"].values()))
        parts = {cat: loaded[pid] for cat, pid in res[index]["ids"].items() if pid in loaded}
        try:
            build = await build_service.create_build(session, user, name, parts, data.get("bd"))
        except BuildError as exc:
            await show(event, f"❌ {escape(str(exc))}", _kb([[_btn("⬅️ К сборке", f"bd:o:{index}")]], back=False))
            return
        await state.set_state(None)
        await show(event, f"✅ Сборка <b>{escape(build.name)}</b> сохранена.\nID: <code>{build.public_id}</code>",
                   InlineKeyboardMarkup(inline_keyboard=[[_btn("📦 Мои сборки", Cb.BUILDS)], nav_row()]))

    @router.callback_query(F.data == "bd:savedef")
    async def cb_save_default(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        name = default_name((await state.get_data()).get("bd", {}))
        await _persist(callback, state, session, db_user, name)

    @router.message(StateFilter(BuildStates.save_name), F.text, ~F.text.startswith("/"))
    async def msg_save_name(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        try:
            name = build_service.validate_build_name(message.text)
        except BuildError as exc:
            await message.answer(f"❌ {escape(str(exc))}")
            return
        await _persist(message, state, session, db_user, name)

    return router
