"""🎮 FPS в играх: только реальные замеры из базы бенчмарков, с источниками и условиями теста."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.picker import register_flow, selected_parts
from bot.keyboards import Cb, nav_row
from bot.models import User
from bot.services import fps_service, user_service
from bot.utils.telegram import show

FLOW = "fps"
HOME = "fp:home"

EMPTY_DB = ("🎮 <b>FPS в играх</b>\n\n📭 <b>База замеров пока пуста.</b>\n\n"
            "Я показываю только реальные результаты тестов с указанием источника и условий "
            "(игра, разрешение, настройки, версия, дата). Придумывать FPS я не буду.\n\n"
            "Администратор может загрузить замеры в админ-панели («Бенчмарки FPS» → CSV).")


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]], back: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, nav_row(back=back)])


async def render_home(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.update_data(pk_flow=FLOW, pk_back=HOME)
    parts = await selected_parts(session, state)
    cpu, gpu = parts.get("cpu"), parts.get("gpu")
    rows = [[_btn(f"✅ {cpu.name}"[:60] if cpu else "➕ Процессор", "pk:cpu:0")],
            [_btn(f"✅ {gpu.name}"[:60] if gpu else "➕ Видеокарта", "pk:gpu:0")]]
    if cpu and gpu:
        rows.append([_btn("➡️ Выбрать игру", "fp:games")])
    total = await fps_service.benchmark_count(session)
    text = ("🎮 <b>FPS в играх</b>\n\nВыберите процессор и видеокарту, затем игру, разрешение и настройки. "
            f"В базе замеров: <b>{total}</b>.\n\nЕсли нужного замера нет, я прямо скажу об этом.")
    await show(event, text, _kb(rows))


def build_router() -> Router:
    router = Router(name="fps")
    register_flow(FLOW, render_home)

    @router.callback_query(F.data == Cb.FPS)
    async def cb_open(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await user_service.log_usage(session, db_user, "fps")
        if await fps_service.benchmark_count(session) == 0:
            await show(callback, EMPTY_DB, _kb([]))
            return
        await state.update_data(parts={})
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == HOME)
    async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.set_state(None)
        await render_home(callback, state, session, db_user)

    @router.callback_query(F.data == "fp:games")
    async def cb_games(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        games = await fps_service.available_games(session)
        await state.update_data(fp_games=games)
        rows = [[_btn(g, f"fp:g:{i}")] for i, g in enumerate(games)]
        await show(callback, "🎮 <b>Выберите игру</b>\nПоказаны игры, для которых есть замеры.", _kb(rows, back=HOME))

    @router.callback_query(F.data.regexp(r"^fp:g:\d+$"))
    async def cb_game(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        games = (await state.get_data()).get("fp_games") or []
        index = int(callback.data.split(":")[2])
        if not 0 <= index < len(games):
            await callback.answer("Список устарел", show_alert=True)
            return
        await callback.answer()
        await state.update_data(fp_game=games[index])
        resolutions = await fps_service.available_resolutions(session, games[index])
        await state.update_data(fp_resolutions=resolutions)
        rows = [[_btn(r, f"fp:r:{i}") for i, r in enumerate(resolutions)]]
        await show(callback, f"🖥 <b>{games[index]}</b>\nВыберите разрешение:", _kb(rows, back="fp:games"))

    @router.callback_query(F.data.regexp(r"^fp:r:\d+$"))
    async def cb_resolution(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        data = await state.get_data()
        resolutions = data.get("fp_resolutions") or []
        index = int(callback.data.split(":")[2])
        if not 0 <= index < len(resolutions):
            await callback.answer("Список устарел", show_alert=True)
            return
        await callback.answer()
        resolution = resolutions[index]
        presets = await fps_service.available_presets(session, data["fp_game"], resolution)
        await state.update_data(fp_resolution=resolution, fp_presets=presets)
        rows = [[_btn(p, f"fp:p:{i}")] for i, p in enumerate(presets)]
        await show(callback, f"⚙️ <b>{data['fp_game']} · {resolution}</b>\nНастройки графики:", _kb(rows, back=f"fp:g:{(data.get('fp_games') or []).index(data['fp_game'])}"))

    @router.callback_query(F.data.regexp(r"^fp:p:\d+$"))
    async def cb_preset(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        data = await state.get_data()
        presets = data.get("fp_presets") or []
        index = int(callback.data.split(":")[2])
        if not 0 <= index < len(presets):
            await callback.answer("Список устарел", show_alert=True)
            return
        await callback.answer()
        parts = await selected_parts(session, state)
        cpu, gpu = parts.get("cpu"), parts.get("gpu")
        if cpu is None or gpu is None:
            await render_home(callback, state, session, None)  # type: ignore[arg-type]
            return
        game, resolution, preset = data["fp_game"], data["fp_resolution"], presets[index]
        rows = await fps_service.find_benchmarks(session, cpu.id, gpu.id, game, resolution, preset)
        others = [] if rows else await fps_service.other_measurements(session, game, resolution, preset)
        text = fps_service.render_benchmarks(cpu.name, gpu.name, game, resolution, preset, rows, others)
        await show(callback, text[:4000], _kb([[_btn("🔁 Другая игра / настройки", "fp:games")]], back=HOME))

    return router
