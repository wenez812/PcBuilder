"""🛠 Админ-панель. Доступ только для Telegram ID из ADMIN_IDS."""
from __future__ import annotations

import asyncio
import logging
from html import escape
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, Filter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import Database
from bot.handlers.states import AdminStates
from bot.integrations import STATUS_LABEL, PriceSource
from bot.keyboards import Cb, nav_row
from bot.models import Component, User
from bot.services import catalog_service, fps_service, importers, price_updater, stats_service, user_service
from bot.services.parts import CATEGORY_SHORT
from bot.utils.telegram import show
from config import Settings

logger = logging.getLogger(__name__)
ADMIN_HOME = "ad:home"
BROADCAST_DELAY = 0.05
_background_tasks: set[asyncio.Task] = set()


class AdminFilter(Filter):
    """Пропускает только администраторов из ADMIN_IDS."""

    async def __call__(self, event: Message | CallbackQuery, settings: Settings) -> bool:
        return event.from_user is not None and settings.is_admin(event.from_user.id)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]], back: str | None = ADMIN_HOME) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[*rows, nav_row(back=back)])


async def _download(bot: Bot, message: Message) -> bytes | None:
    doc = message.document
    if doc is None:
        await message.answer("📎 Пришлите файл как документ.")
        return None
    if (doc.file_size or 0) > importers.MAX_FILE_BYTES:
        await message.answer("❌ Файл больше 2 МБ.")
        return None
    buffer = BytesIO()
    await bot.download(doc, destination=buffer)
    return buffer.getvalue()


async def broadcast(bot: Bot, recipients: list[int], text: str) -> tuple[int, int, int]:
    """Рассылка с учётом лимитов Telegram. Возвращает (доставлено, заблокировали бота, ошибок)."""
    sent = blocked = failed = 0
    for chat_id in recipients:
        for attempt in range(2):
            try:
                await bot.send_message(chat_id, text)
                sent += 1
                break
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 0.5)
            except TelegramForbiddenError:
                blocked += 1
                break
            except TelegramAPIError as exc:
                logger.warning("Рассылка: ошибка для %s: %s", chat_id, exc)
                failed += 1
                break
        else:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)
    return sent, blocked, failed


def build_router() -> Router:
    router = Router(name="admin")
    router.message.filter(AdminFilter())
    router.callback_query.filter(AdminFilter())

    async def home(event: Message | CallbackQuery) -> None:
        rows = [
            [_btn("📊 Статистика", "ad:stats"), _btn("🐞 Ошибки", "ad:errors")],
            [_btn("🧩 Каталог", "ad:catalog"), _btn("💰 Цены", "ad:prices")],
            [_btn("🎮 Бенчмарки FPS", "ad:bench"), _btn("📣 Рассылка", "ad:bc")],
        ]
        await show(event, "🛠 <b>Админ-панель</b>", _kb(rows, back=None))

    @router.message(Command("admin"))
    async def cmd_admin(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await state.clear()
        await user_service.log_usage(session, db_user, "admin")
        await home(message)

    @router.callback_query(F.data.in_({Cb.ADMIN, ADMIN_HOME}))
    async def cb_home(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
        await callback.answer()
        await state.clear()
        await user_service.log_usage(session, db_user, "admin")
        await home(callback)

    # ---- статистика
    @router.callback_query(F.data == "ad:stats")
    async def cb_stats(callback: CallbackQuery, session: AsyncSession) -> None:
        await callback.answer()
        s = await stats_service.collect_stats(session)
        lines = ["📊 <b>Статистика</b>\n",
                 f"👥 Пользователей: <b>{s.users_total}</b>",
                 f"🟢 Активных: за сутки {s.active_1d} · за 7 дней {s.active_7d} · за 30 дней {s.active_30d}",
                 f"📦 Создано сборок: <b>{s.builds_total}</b>",
                 f"🐞 Ошибок: всего {s.errors_total}, за сутки {s.errors_24h}"]
        if s.features:
            lines.append("\n🔥 <b>Использование функций</b>")
            lines.extend(f"• {escape(stats_service.feature_title(f))}: {n}" for f, n in s.features)
        await show(callback, "\n".join(lines), _kb([]))

    # ---- ошибки
    @router.callback_query(F.data == "ad:errors")
    async def cb_errors(callback: CallbackQuery, session: AsyncSession) -> None:
        await callback.answer()
        errors = await stats_service.recent_errors(session, 8)
        if not errors:
            await show(callback, "🐞 <b>Ошибки</b>\n\nОшибок нет 🎉", _kb([]))
            return
        lines = ["🐞 <b>Последние ошибки</b>\n"]
        for e in errors:
            who = f" · user {e.telegram_id}" if e.telegram_id else ""
            lines.append(f"<b>{e.created_at.strftime('%d.%m %H:%M')}</b>{who}\n"
                         f"{escape(e.error_type)}: {escape(e.message[:200])}")
        await show(callback, "\n\n".join(lines)[:4000], _kb([]))

    # ---- каталог
    @router.callback_query(F.data == "ad:catalog")
    async def cb_catalog(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        await state.set_state(None)
        counts = await catalog_service.catalog_counts(session)
        lines = ["🧩 <b>Каталог</b>\n"] + [f"• {CATEGORY_SHORT[c]}: {n}" for c, n in counts.items()]
        lines.append("\nЗагрузите JSON со списком деталей (категория, бренд, название, specs) — "
                     "новые добавятся, существующие обновятся. Характеристики вносите только из паспорта производителя.")
        rows = [[_btn("📥 Импорт каталога (JSON)", "ad:imp_comp")], [_btn("🔎 Найти / отключить деталь", "ad:search")],
                [_btn("📄 Пример JSON", "ad:comp_example")]]
        await show(callback, "\n".join(lines), _kb(rows))

    @router.callback_query(F.data == "ad:comp_example")
    async def cb_example(callback: CallbackQuery) -> None:
        await callback.answer()
        example = ('[\n  {\n    "category": "gpu",\n    "brand": "NVIDIA",\n    "name": "ASUS Dual GeForce RTX 5060 8GB",\n'
                   '    "condition": "new",\n    "specs": {"vram_gb": 8, "tgp_w": 145, "rec_psu_w": 550, "pcie_gen": 5,\n'
                   '              "pcie_lanes": 8, "gaming": 5.4, "compute": 5.4, "length_mm": 229,\n'
                   '              "power_connectors": ["8-pin"]},\n    "source": "ссылка на страницу производителя"\n  }\n]\n')
        await callback.message.answer_document(BufferedInputFile(example.encode(), "components_example.json"),
                                               caption="Категории: cpu, gpu, motherboard, ram, ssd, psu, case, cooler.")

    @router.callback_query(F.data == "ad:imp_comp")
    async def cb_import_components(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminStates.components_file)
        await show(callback, "📥 Пришлите JSON-файл каталога (до 2 МБ).", _kb([], back="ad:catalog"))

    @router.message(StateFilter(AdminStates.components_file), F.document)
    async def msg_components(message: Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
        raw = await _download(bot, message)
        if raw is None:
            return
        report = await importers.import_components_json(session, raw)
        await state.set_state(None)
        await message.answer("🧩 <b>Импорт каталога</b>\n\n" + escape(report.summary()).replace("&lt;", "<"),
                             reply_markup=_kb([], back="ad:catalog"))

    @router.callback_query(F.data == "ad:search")
    async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminStates.search)
        await show(callback, "🔎 Напишите часть названия детали.", _kb([], back="ad:catalog"))

    @router.message(StateFilter(AdminStates.search), F.text, ~F.text.startswith("/"))
    async def msg_search(message: Message, state: FSMContext, session: AsyncSession) -> None:
        parts = await catalog_service.list_parts(session, only_active=False)
        found = catalog_service.search_parts(parts, message.text[:80], limit=8)
        if not found:
            await message.answer("Ничего не найдено.")
            return
        await state.set_state(None)
        rows = [[_btn(f"{CATEGORY_SHORT[p.category]}: {p.name}"[:60], f"ad:part:{p.id}")] for p in found]
        await message.answer("Найдено:", reply_markup=_kb(rows, back="ad:catalog"))

    async def part_card(event: CallbackQuery, session: AsyncSession, part_id: int) -> None:
        row = await session.get(Component, part_id)
        if row is None:
            await event.answer("Деталь не найдена", show_alert=True)
            return
        status = "🟢 активна" if row.is_active else "⚫ отключена"
        offers = await catalog_service.get_offers(session, row.id)
        text = (f"🧩 <b>{escape(row.name)}</b>\n{CATEGORY_SHORT[row.category]} · {escape(row.brand)} · "
                f"{'новая' if row.condition == 'new' else 'б/у'}\nID: {row.id} · {status}\n"
                f"Предложений магазинов: {len(offers)}\n📝 {escape(row.source_note or '—')}")
        toggle = "⚫ Отключить" if row.is_active else "🟢 Включить"
        await show(event, text, _kb([[_btn(toggle, f"ad:toggle:{row.id}")]], back="ad:catalog"))

    @router.callback_query(F.data.regexp(r"^ad:part:\d+$"))
    async def cb_part(callback: CallbackQuery, session: AsyncSession) -> None:
        await callback.answer()
        await part_card(callback, session, int(callback.data.split(":")[2]))

    @router.callback_query(F.data.regexp(r"^ad:toggle:\d+$"))
    async def cb_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
        row = await session.get(Component, int(callback.data.split(":")[2]))
        if row is None:
            await callback.answer("Деталь не найдена", show_alert=True)
            return
        row.is_active = not row.is_active
        await session.flush()
        await callback.answer("Включена" if row.is_active else "Отключена")
        await part_card(callback, session, row.id)

    # ---- цены
    @router.callback_query(F.data == "ad:prices")
    async def cb_prices(callback: CallbackQuery, state: FSMContext, session: AsyncSession,
                        price_sources: list[PriceSource]) -> None:
        await callback.answer()
        await state.set_state(None)
        total, priced, stale = await catalog_service.price_coverage(session)
        lines = ["💰 <b>Цены</b>\n", f"Деталей: {total} · со свежей ценой: {priced} · с устаревшей: {stale}",
                 f"Цена считается свежей {catalog_service.PRICE_MAX_AGE_DAYS} дней.\n", "<b>Источники</b>"]
        lines.extend(f"{STATUS_LABEL[s.status]} {escape(s.title)}" for s in price_sources)
        lines.append("\nДля подбора ПК нужны цены на каждую категорию. Скачайте шаблон, заполните колонки "
                     "<code>shop</code> и <code>price</code> реальными данными и загрузите обратно.")
        rows = [[_btn("📄 Скачать шаблон CSV", "ad:tpl")], [_btn("📥 Загрузить CSV цен", "ad:imp_prices")],
                [_btn("🔄 Обновить из фида", "ad:refresh")]]
        await show(callback, "\n".join(lines), _kb(rows))

    @router.callback_query(F.data == "ad:tpl")
    async def cb_template(callback: CallbackQuery, session: AsyncSession) -> None:
        await callback.answer()
        parts = await catalog_service.list_parts(session)
        data = importers.price_template_csv([(p.category, p.name) for p in parts])
        await callback.message.answer_document(
            BufferedInputFile(data, "prices_template.csv"),
            caption="Заполните shop (название магазина) и price (рубли), in_stock: 1 или 0, url — ссылка на товар.")

    @router.callback_query(F.data == "ad:imp_prices")
    async def cb_import_prices(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminStates.prices_file)
        await show(callback, "📥 Пришлите CSV с ценами (колонки: category;name;shop;price;in_stock;url).",
                   _kb([], back="ad:prices"))

    @router.message(StateFilter(AdminStates.prices_file), F.document)
    async def msg_prices(message: Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
        raw = await _download(bot, message)
        if raw is None:
            return
        report = await importers.import_prices_csv(session, raw)
        await state.set_state(None)
        await message.answer("💰 <b>Импорт цен</b>\n\n" + escape(report.summary()), reply_markup=_kb([], back="ad:prices"))

    @router.callback_query(F.data == "ad:refresh")
    async def cb_refresh(callback: CallbackQuery, db: Database, price_sources: list[PriceSource]) -> None:
        await callback.answer("Обновляю…")
        report = await price_updater.refresh_prices(db, price_sources)
        await show(callback, "🔄 <b>Обновление цен</b>\n\n" + escape(report.summary()), _kb([], back="ad:prices"))

    # ---- бенчмарки
    @router.callback_query(F.data == "ad:bench")
    async def cb_bench(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        await state.set_state(None)
        count = await fps_service.benchmark_count(session)
        text = (f"🎮 <b>Бенчмарки FPS</b>\n\nЗамеров в базе: <b>{count}</b>.\n\n"
                "Каждая строка обязана иметь источник замера. CPU и GPU должны точно совпадать с названиями каталога. "
                "Разные условия теста — разные строки: бот их не смешивает.")
        rows = [[_btn("📄 Шаблон CSV", "ad:bench_tpl")], [_btn("📥 Загрузить CSV", "ad:imp_bench")]]
        await show(callback, text, _kb(rows))

    @router.callback_query(F.data == "ad:bench_tpl")
    async def cb_bench_template(callback: CallbackQuery) -> None:
        await callback.answer()
        header = ";".join(importers.BENCH_COLUMNS) + "\n"
        await callback.message.answer_document(
            BufferedInputFile(header.encode("utf-8-sig"), "benchmarks_template.csv"),
            caption="Заполняйте только реальными замерами. Формат даты: ГГГГ-ММ-ДД, разрешение: 1080p/1440p/4K.")

    @router.callback_query(F.data == "ad:imp_bench")
    async def cb_import_bench(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminStates.benchmarks_file)
        await show(callback, "📥 Пришлите CSV с замерами FPS.", _kb([], back="ad:bench"))

    @router.message(StateFilter(AdminStates.benchmarks_file), F.document)
    async def msg_bench(message: Message, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
        raw = await _download(bot, message)
        if raw is None:
            return
        report = await importers.import_benchmarks_csv(session, raw)
        await state.set_state(None)
        await message.answer("🎮 <b>Импорт замеров</b>\n\n" + escape(report.summary()), reply_markup=_kb([], back="ad:bench"))

    # ---- рассылка
    @router.callback_query(F.data == "ad:bc")
    async def cb_broadcast(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
        await callback.answer()
        recipients = await stats_service.broadcast_recipients(session)
        await state.set_state(AdminStates.broadcast_text)
        await show(callback, f"📣 <b>Рассылка</b>\n\nПолучателей (уведомления включены): <b>{len(recipients)}</b>.\n\n"
                             "Напишите текст сообщения. Перед отправкой я покажу его на проверку.", _kb([]))

    @router.message(StateFilter(AdminStates.broadcast_text), F.text, ~F.text.startswith("/"))
    async def msg_broadcast_text(message: Message, state: FSMContext) -> None:
        text = message.text.strip()
        if not text or len(text) > 3500:
            await message.answer("❌ Текст должен быть от 1 до 3500 символов.")
            return
        await state.update_data(broadcast=text)
        await state.set_state(None)
        await message.answer(f"👁 <b>Предпросмотр</b>\n\n{escape(text)}\n\nОтправить всем получателям?",
                             reply_markup=_kb([[_btn("✅ Отправить", "ad:bc_go")]], back="ad:bc"))

    @router.callback_query(F.data == "ad:bc_go")
    async def cb_broadcast_go(callback: CallbackQuery, bot: Bot, state: FSMContext, session: AsyncSession) -> None:
        text = (await state.get_data()).get("broadcast")
        if not text:
            await callback.answer("Текст не найден", show_alert=True)
            return
        await callback.answer()
        recipients = await stats_service.broadcast_recipients(session)
        await state.update_data(broadcast=None)
        admin_id = callback.from_user.id
        await show(callback, f"📤 Рассылка запущена: {len(recipients)} получателей. Пришлю отчёт по завершении.", _kb([]))

        async def job() -> None:
            sent, blocked, failed = await broadcast(bot, recipients, text)
            await bot.send_message(admin_id, f"📣 <b>Рассылка завершена</b>\n✅ Доставлено: {sent}\n"
                                             f"🚫 Заблокировали бота: {blocked}\n⚠️ Ошибок: {failed}")

        task = asyncio.create_task(job())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return router


def build_denied_router() -> Router:
    """Ответ тем, кто пытается открыть админ-функции без прав."""
    router = Router(name="admin_denied")

    @router.callback_query(F.data.startswith("ad:") | (F.data == Cb.ADMIN))
    async def denied_cb(callback: CallbackQuery) -> None:
        await callback.answer("⛔ Нет доступа", show_alert=True)

    @router.message(Command("admin"))
    async def denied_msg(message: Message) -> None:
        await message.answer("⛔ Эта команда доступна только администраторам.")

    return router
