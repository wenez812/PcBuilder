"""📦 Мои сборки: список, просмотр, переименование, редактирование, удаление, сравнение, шеринг."""
from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.picker import register_flow
from bot.handlers.states import MyBuildsStates
from bot.keyboards import Cb, nav_row
from bot.models import Build, User
from bot.services import build_service, catalog_service, user_service
from bot.services.build_service import BuildError
from bot.services.compatibility import check_compatibility, format_report
from bot.services.parts import CATEGORIES, CATEGORY_SHORT, CATEGORY_TITLES, Part
from bot.utils.render import render_build
from bot.utils.telegram import show

PAGE_SIZE = 8
EDIT_FLOW = "edit"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]], *, back: str | None = Cb.BUILDS) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, nav_row(back=back)])


def _date(value) -> str:
    return value.strftime("%d.%m.%Y")


async def _own_build(callback: CallbackQuery, session: AsyncSession, user: User, build_id: int) -> Build | None:
    build = await build_service.get_user_build(session, user, build_id)
    if build is None:
        await callback.answer("Сборка не найдена — возможно, она удалена.", show_alert=True)
    return build


def build_card(build: Build, parts: dict[str, Part]) -> str:
    report = check_compatibility(parts)
    text = render_build(parts, title=build.name, report=report, compact=True)
    shared = "🔗 Доступна по ссылке" if build.is_shared else "🔒 Видна только вам"
    return (f"📦 {text}\n\n🆔 <code>{build.public_id}</code> · {shared}\n"
            f"📅 Создана {_date(build.created_at)}, изменена {_date(build.updated_at)}")


async def show_shared(message: Message, session: AsyncSession, public_id: str) -> None:
    """Просмотр чужой сборки по ссылке (только для сборок, которыми владелец поделился)."""
    build = await build_service.get_shared_build(session, public_id)
    if build is None:
        await message.answer("🔍 Сборка не найдена или владелец закрыл доступ.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav_row()]))
        return
    parts = await build_service.load_parts(session, build)
    report = check_compatibility(parts)
    text = "👥 <b>Сборка от друга</b>\n\n" + render_build(parts, title=build.name, report=report, compact=True)
    await message.answer(text[:4000], reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [_btn("💾 Сохранить себе", f"sh:copy:{build.public_id}")], nav_row()]))


async def _sync_edit(session: AsyncSession, build: Build, selected: dict[str, int]) -> None:
    """Приводит сохранённую сборку в соответствие с выбором в сценарии редактирования."""
    current = await build_service.load_parts(session, build)
    wanted = await catalog_service.get_parts(session, list(selected.values()))
    for category in CATEGORIES:
        chosen_id = selected.get(category)
        if chosen_id is None:
            if category in current:
                await build_service.remove_item(session, build, category)
        elif chosen_id in wanted and (category not in current or current[category].id != chosen_id):
            await build_service.replace_item(session, build, category, wanted[chosen_id])


async def render_edit(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    build = await build_service.get_user_build(session, user, data.get("edit_build_id", 0))
    if build is None:
        await show(event, "Сборка не найдена.", _kb([], back=None))
        return
    await _sync_edit(session, build, data.get("parts") or {})
    parts = await build_service.load_parts(session, build)
    await state.update_data(pk_flow=EDIT_FLOW, pk_back=f"mb:edit:{build.id}", parts={c: p.id for c, p in parts.items()})
    rows = []
    for category in CATEGORIES:
        part = parts.get(category)
        label = f"{CATEGORY_SHORT[category]}: {part.name}" if part else f"➕ {CATEGORY_SHORT[category]}"
        rows.append([_btn(label[:60], f"pk:{category}:0")])
    rows.append([_btn("✅ Готово", f"mb:view:{build.id}")])
    report = check_compatibility(parts)
    text = (f"✏️ <b>Редактирование: {escape(build.name)}</b>\n\nВыберите деталь, которую хотите заменить.\n\n"
            f"{format_report(report)}")
    await show(event, text[:4000], InlineKeyboardMarkup(inline_keyboard=rows))


def build_router() -> Router:
    router = Router(name="builds")
    register_flow(EDIT_FLOW, render_edit)

    @router.callback_query(F.data == Cb.BUILDS)
    async def cb_list(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await user_service.log_usage(session, db_user, "builds")
        await _render_list(callback, session, db_user, 0)

    @router.callback_query(F.data.regexp(r"^mb:list:\d+$"))
    async def cb_list_page(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await _render_list(callback, session, db_user, int(callback.data.split(":")[2]))

    async def _render_list(event, session: AsyncSession, user: User, page: int) -> None:
        builds = await build_service.list_builds(session, user)
        if not builds:
            await show(event, "📦 <b>Мои сборки</b>\n\nПока пусто. Подберите ПК и сохраните сборку — она появится здесь.",
                       InlineKeyboardMarkup(inline_keyboard=[[_btn("🖥 Собрать ПК", Cb.BUILD)], nav_row()]))
            return
        pages = (len(builds) + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, pages - 1))
        rows = [[_btn(f"📦 {b.name}"[:60], f"mb:view:{b.id}")] for b in builds[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]]
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(_btn("◀️", f"mb:list:{page - 1}"))
            nav.append(_btn(f"{page + 1}/{pages}", "noop"))
            if page < pages - 1:
                nav.append(_btn("▶️", f"mb:list:{page + 1}"))
            rows.append(nav)
        await show(event, f"📦 <b>Мои сборки</b> ({len(builds)} из {build_service.MAX_BUILDS_PER_USER})\n\n"
                          "Выберите сборку:", _kb(rows, back=None))

    @router.callback_query(F.data.regexp(r"^mb:view:\d+$"))
    async def cb_view(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer()
        await state.set_state(None)
        parts = await build_service.load_parts(session, build)
        rows = [
            [_btn("✏️ Изменить детали", f"mb:edit:{build.id}"), _btn("🏷 Переименовать", f"mb:rename:{build.id}")],
            [_btn("⚙️ Совместимость", f"mb:chk:{build.id}"), _btn("⚖️ Сравнить", f"mb:cmp:{build.id}")],
            [_btn("🔒 Закрыть ссылку" if build.is_shared else "🔗 Поделиться",
                  f"mb:{'unshare' if build.is_shared else 'share'}:{build.id}"),
             _btn("🗑 Удалить", f"mb:del:{build.id}")],
        ]
        await show(callback, build_card(build, parts)[:4000], _kb(rows))

    @router.callback_query(F.data.regexp(r"^mb:chk:\d+$"))
    async def cb_check(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer()
        parts = await build_service.load_parts(session, build)
        await show(callback, format_report(check_compatibility(parts))[:4000], _kb([], back=f"mb:view:{build.id}"))

    # ---- переименование
    @router.callback_query(F.data.regexp(r"^mb:rename:\d+$"))
    async def cb_rename(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer()
        await state.update_data(rename_id=build.id)
        await state.set_state(MyBuildsStates.rename)
        await show(callback, f"🏷 Новое название для <b>{escape(build.name)}</b> (до 64 символов):",
                   _kb([], back=f"mb:view:{build.id}"))

    @router.message(StateFilter(MyBuildsStates.rename), F.text, ~F.text.startswith("/"))
    async def msg_rename(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        build = await build_service.get_user_build(session, db_user, (await state.get_data()).get("rename_id", 0))
        if build is None:
            await state.set_state(None)
            await message.answer("Сборка не найдена.")
            return
        try:
            await build_service.rename_build(session, build, message.text)
        except BuildError as exc:
            await message.answer(f"❌ {escape(str(exc))}")
            return
        await state.set_state(None)
        await message.answer(f"✅ Переименовано: <b>{escape(build.name)}</b>",
                             reply_markup=_kb([[_btn("📦 Открыть сборку", f"mb:view:{build.id}")]]))

    # ---- удаление
    @router.callback_query(F.data.regexp(r"^mb:del:\d+$"))
    async def cb_delete_ask(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer()
        await show(callback, f"🗑 Удалить сборку <b>{escape(build.name)}</b>? Это действие необратимо.",
                   _kb([[_btn("✅ Да, удалить", f"mb:delok:{build.id}")]], back=f"mb:view:{build.id}"))

    @router.callback_query(F.data.regexp(r"^mb:delok:\d+$"))
    async def cb_delete(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer("Удалено")
        await build_service.delete_build(session, build)
        await _render_list(callback, session, db_user, 0)

    # ---- редактирование
    @router.callback_query(F.data.regexp(r"^mb:edit:\d+$"))
    async def cb_edit(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        await callback.answer()
        await state.set_state(None)
        parts = await build_service.load_parts(session, build)
        await state.update_data(edit_build_id=build.id, parts={c: p.id for c, p in parts.items()})
        await render_edit(callback, state, session, db_user)

    # ---- шеринг
    @router.callback_query(F.data.regexp(r"^mb:(share|unshare):\d+$"))
    async def cb_share(callback: CallbackQuery, bot: Bot, session: AsyncSession, db_user: User) -> None:
        action, build_id = callback.data.split(":")[1:]
        build = await _own_build(callback, session, db_user, int(build_id))
        if build is None:
            return
        await callback.answer()
        if action == "unshare":
            await build_service.set_shared(session, build, False)
            await show(callback, "🔒 Ссылка закрыта: сборка снова видна только вам.",
                       _kb([], back=f"mb:view:{build.id}"))
            return
        await build_service.set_shared(session, build, True)
        me = await bot.me()
        link = build_service.share_link(me.username or "", build)
        await show(callback, "🔗 <b>Ссылка для друзей</b>\n\nОтправьте её — по ней откроется ваша сборка "
                             "(только просмотр, с возможностью сохранить копию):\n\n"
                             f"<code>{escape(link)}</code>\n\nЗакрыть доступ можно в любой момент.",
                   _kb([], back=f"mb:view:{build.id}"))

    @router.callback_query(F.data.regexp(r"^sh:copy:[0-9a-f]{8}$"))
    async def cb_copy_shared(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        source = await build_service.get_shared_build(session, callback.data.split(":")[2])
        if source is None:
            await callback.answer("Сборка недоступна.", show_alert=True)
            return
        try:
            copy = await build_service.copy_build(session, source, db_user)
        except BuildError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer("Сохранено")
        await show(callback, f"✅ Копия сохранена: <b>{escape(copy.name)}</b>",
                   InlineKeyboardMarkup(inline_keyboard=[[_btn("📦 Мои сборки", Cb.BUILDS)], nav_row()]))

    # ---- сравнение
    @router.callback_query(F.data.regexp(r"^mb:cmp:\d+$"))
    async def cb_compare_pick(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        build = await _own_build(callback, session, db_user, int(callback.data.split(":")[2]))
        if build is None:
            return
        others = [b for b in await build_service.list_builds(session, db_user) if b.id != build.id]
        await callback.answer()
        if not others:
            await show(callback, "⚖️ Для сравнения нужна ещё хотя бы одна сохранённая сборка.",
                       _kb([], back=f"mb:view:{build.id}"))
            return
        rows = [[_btn(f"⚖️ {b.name}"[:60], f"mb:cmpto:{build.id}:{b.id}")] for b in others[:10]]
        await show(callback, f"С какой сборкой сравнить <b>{escape(build.name)}</b>?", _kb(rows, back=f"mb:view:{build.id}"))

    @router.callback_query(F.data.regexp(r"^mb:cmpto:\d+:\d+$"))
    async def cb_compare(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
        a_id, b_id = (int(x) for x in callback.data.split(":")[2:])
        a = await _own_build(callback, session, db_user, a_id)
        b = await _own_build(callback, session, db_user, b_id)
        if a is None or b is None:
            return
        await callback.answer()
        pa, pb = await build_service.load_parts(session, a), await build_service.load_parts(session, b)
        lines = [f"⚖️ <b>{escape(a.name)}</b>  vs  <b>{escape(b.name)}</b>\n"]
        for row in build_service.compare_builds(pa, pb):
            name_a = escape(row["a"].name) if row["a"] else "—"
            name_b = escape(row["b"].name) if row["b"] else "—"
            if row["same"]:
                lines.append(f"{CATEGORY_TITLES[row['category']]}\n   ＝ {name_a}")
            else:
                diff = ""
                if row["price_diff"] is not None:
                    sign = "+" if row["price_diff"] > 0 else ""
                    diff = f"  ({sign}{catalog_service.format_price(row['price_diff'])})"
                lines.append(f"{CATEGORY_TITLES[row['category']]}\n   A: {name_a}\n   B: {name_b}{diff}")
        ta, ma = build_service.build_total(pa)
        tb, mb = build_service.build_total(pb)
        lines.append(f"\n💵 A: <b>{catalog_service.format_price(ta)}</b> · B: <b>{catalog_service.format_price(tb)}</b>")
        if ma or mb:
            lines.append("⚠️ У части деталей нет актуальной цены — суммы неполные.")
        lines.append(f"{check_compatibility(pa).emoji} A: {check_compatibility(pa).title} · "
                     f"{check_compatibility(pb).emoji} B: {check_compatibility(pb).title}")
        await show(callback, "\n".join(lines)[:4000], _kb([], back=f"mb:view:{a.id}"))

    return router
