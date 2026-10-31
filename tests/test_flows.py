"""Сквозные сценарии через настоящий Dispatcher (сеть Telegram подменена)."""
import json
from dataclasses import replace

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select

from bot.app import build_dispatcher
from bot.models import Build, ErrorLog, FpsBenchmark
from bot.services import build_service, importers, user_service
from tests.conftest import callback_update, document_update, message_update, part_id, photo_update

USER, ADMIN = 42, 1


class Chat:
    """Удобная обёртка: отправляет апдейты и читает последний ответ бота."""

    def __init__(self, dp, bot, fake, db, user_id=USER):
        self.dp, self.bot, self.fake, self.db, self.user_id, self.n = dp, bot, fake, db, user_id, 0

    async def say(self, text):
        self.n += 1
        await self.dp.feed_raw_update(self.bot, message_update(self.n + self.user_id * 1000, self.user_id, text))
        return self.text

    async def click(self, data):
        self.n += 1
        await self.dp.feed_raw_update(self.bot, callback_update(self.n + self.user_id * 1000, self.user_id, data))
        return self.text

    @property
    def text(self):
        for m in reversed(self.fake.requests):
            if isinstance(m, (SendMessage, EditMessageText)):
                return m.text
        return ""

    @property
    def markup(self):
        for m in reversed(self.fake.requests):
            if isinstance(m, (SendMessage, EditMessageText)):
                return m.reply_markup
        return None

    @property
    def buttons(self):
        return [b for row in (self.markup.inline_keyboard if self.markup else []) for b in row]

    def has_button(self, text_part):
        return any(text_part in b.text for b in self.buttons)

    def alerts(self):
        return [m.text for m in self.fake.requests if isinstance(m, AnswerCallbackQuery) and m.text]

    async def no_errors(self):
        async with self.db.session_factory() as s:
            rows = (await s.execute(select(ErrorLog))).scalars().all()
        assert not rows, [(r.error_type, r.message, r.traceback[-600:]) for r in rows]


@pytest.fixture
def chat(seeded, settings, bot, fake_session):
    dp = build_dispatcher(settings, seeded)
    return Chat(dp, bot, fake_session, seeded)


@pytest.fixture
def admin_chat(chat):
    return Chat(chat.dp, chat.bot, chat.fake, chat.db, ADMIN)


async def test_compat_flow_detects_incompatibility(chat):
    cpu, board = await part_id(chat.db, "cpu", "7500F"), await part_id(chat.db, "motherboard", "B760M-P DDR4")
    await chat.click("menu:compat")
    await chat.click("pk:cpu:0")
    await chat.click(f"sel:{cpu}")
    assert "Ryzen 5 7500F" in "".join(b.text for b in chat.buttons)
    await chat.click("pk:motherboard:0")
    text = await chat.click(f"sel:{board}")
    assert "🔴" in text and "не подходит" in text
    await chat.no_errors()


async def test_compat_flow_green_and_reset_and_search(chat):
    cpu, board = await part_id(chat.db, "cpu", "7500F"), await part_id(chat.db, "motherboard", "Gigabyte B650M")
    await chat.click("menu:compat")
    await chat.click("pk:cpu:0")
    await chat.click(f"sel:{cpu}")
    await chat.click("pk:motherboard:0")
    await chat.click("srch:motherboard")
    await chat.say("b650m aorus")
    assert chat.has_button("B650M Aorus")
    text = await chat.click(f"sel:{board}")
    assert text.startswith("⚙️") and "🟢" in text
    text = await chat.click("cp:reset")
    assert "минимум две" not in text and "✅" not in "".join(b.text for b in chat.buttons)
    await chat.no_errors()


async def test_picker_search_no_results_and_stale_selection(chat):
    await chat.click("menu:compat")
    await chat.click("srch:gpu")
    assert "не нашлось" in await chat.say("несуществующая_модель")
    await chat.click("pk:gpu:0")
    await chat.click("sel:999999")
    assert any("недоступна" in a for a in chat.alerts())
    await chat.no_errors()


async def test_full_build_flow_to_saved_build_and_sharing(chat, settings, bot, fake_session, seeded):
    await chat.click("menu:build")
    assert "Шаг 1/" in chat.text
    await chat.click("bd:b:120000")
    await chat.click("bd:p:gaming")
    await chat.click("bd:g:0")                      # CS2
    await chat.click("bd:gdone")
    await chat.click("bd:r:1080p")
    await chat.click("bd:pr:balanced")
    await chat.click("bd:cv:any")
    await chat.click("bd:gv:any")
    text = await chat.click("bd:c:new")
    assert "Проверь параметры" in text and "CS2" in text and "120 000 ₽" in text
    text = await chat.click("bd:go")
    assert "Сборки готовы" in text and "Рекомендуемая" in text
    text = await chat.click("bd:o:0")
    for title in ("Процессор", "Видеокарта", "Материнская плата", "Оперативная память", "SSD",
                  "Блок питания", "Корпус", "Охлаждение"):
        assert title in text, title
    assert "Итого" in text and "💡" in text and "Совместимость" in text
    total = int(text.split("Итого:")[1].split("₽")[0].replace(" ", "").replace(" ", "").replace("</b>", "").strip().rstrip("<").strip() or 0) \
        if False else None

    await chat.click("bd:save:0")
    assert "Как назвать" in chat.text
    await chat.say("x" * 80)
    assert "слишком длинное" in chat.text
    text = await chat.say("Игровой <b>ПК</b>")
    assert "сохранена" in text and "&lt;b&gt;" in text            # HTML в имени экранируется

    async with seeded.session_factory() as s:
        build = (await s.execute(select(Build))).scalar_one()
        assert build.name == "Игровой <b>ПК</b>"
    text = await chat.click("menu:builds")
    assert "Мои сборки" in text and chat.has_button("Игровой")
    text = await chat.click(f"mb:view:{build.id}")
    assert "Итого" in text and build.public_id in text and "Видна только вам" in text

    text = await chat.click(f"mb:share:{build.id}")
    assert f"start=b_{build.public_id}" in text and "pc_builder_test_bot" in text

    friend = Chat(chat.dp, chat.bot, chat.fake, chat.db, 555)
    text = await friend.say(f"/start b_{build.public_id}")
    assert "Сборка от друга" in text and "Итого" in text
    text = await friend.click(f"sh:copy:{build.public_id}")
    assert "Копия сохранена" in text
    # чужая сборка недоступна по прямому callback
    await friend.click(f"mb:del:{build.id}")
    assert any("не найдена" in a for a in friend.alerts())

    await chat.click(f"mb:unshare:{build.id}")
    assert "не найдена" in await Chat(chat.dp, chat.bot, chat.fake, chat.db, 556).say(f"/start b_{build.public_id}")
    await chat.no_errors()


async def test_my_builds_edit_compare_rename_delete(chat, seeded):
    await chat.click("menu:build")
    for cb in ("bd:b:120000", "bd:p:work", "bd:pr:balanced", "bd:cv:any", "bd:gv:any", "bd:c:new", "bd:go", "bd:o:0"):
        await chat.click(cb)
    await chat.click("bd:save:0"); await chat.click("bd:savedef")
    await chat.click("bd:o:0"); await chat.click("bd:save:0"); await chat.say("Вторая")
    async with seeded.session_factory() as s:
        builds = (await s.execute(select(Build).order_by(Build.id))).scalars().all()
    first, second = builds
    assert "Работа ПК" in first.name

    other_gpu = await part_id(seeded, "gpu", "RTX 5070 12GB")
    await chat.click(f"mb:edit:{second.id}")
    await chat.click("pk:gpu:0")
    text = await chat.click(f"sel:{other_gpu}")
    assert "Редактирование" in text
    async with seeded.session_factory() as s:
        parts = await build_service.load_parts(s, await s.get(Build, second.id))
    assert parts["gpu"].id == other_gpu
    await chat.click("pk:cooler:0"); await chat.click("clr:cooler")
    async with seeded.session_factory() as s:
        assert "cooler" not in await build_service.load_parts(s, await s.get(Build, second.id))

    text = await chat.click(f"mb:cmpto:{first.id}:{second.id}")
    assert "A:" in text and "B:" in text and "Итого" not in text and "💵" in text

    await chat.click(f"mb:rename:{second.id}")
    assert "Новое название" in chat.text
    assert "Переименовано" in await chat.say("Новое имя")
    await chat.click(f"mb:del:{second.id}")
    assert "необратимо" in chat.text
    await chat.click(f"mb:delok:{second.id}")
    async with seeded.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Build)) == 1
    await chat.no_errors()


async def test_build_without_prices_is_honest(settings, bot, fake_session, db):
    from bot.services import catalog_service
    async with db.session_factory() as s:
        await catalog_service.seed_catalog(s)
        await s.commit()
    chat = Chat(build_dispatcher(settings, db), bot, fake_session, db)
    for cb in ("menu:build", "bd:b:120000", "bd:p:work", "bd:pr:balanced", "bd:cv:any", "bd:gv:any", "bd:c:new"):
        await chat.click(cb)
    text = await chat.click("bd:go")
    assert "Не могу подобрать" in text and "цены я не придумываю" in text and "Сборки готовы" not in text
    await chat.no_errors()


async def test_over_budget_and_custom_budget_validation(chat):
    await chat.click("menu:build")
    await chat.click("bd:b:custom")
    assert "Минимальный бюджет" in await chat.say("5000")
    assert "Не понял сумму" in await chat.say("много денег")
    await chat.say("20к")
    for cb in ("bd:p:work", "bd:pr:price", "bd:cv:any", "bd:gv:any"):
        await chat.click(cb)
    await chat.click("bd:c:new")
    text = await chat.click("bd:go")
    assert "В бюджет не укладываюсь" in text and "не удаётся" in text
    text = await chat.click("bd:o:0")
    assert "дороже бюджета" in text
    await chat.no_errors()


async def test_back_navigation_in_questionnaire(chat):
    await chat.click("menu:build")
    await chat.click("bd:b:80000")
    await chat.click("bd:p:gaming")
    assert "Шаг 3/" in chat.text
    await chat.click("bd:back")
    assert "Для чего нужен компьютер" in chat.text
    await chat.click("bd:back")
    assert "бюджет" in chat.text.lower()
    assert chat.has_button("Главное меню")


async def test_phrase_flow(chat):
    await chat.click("menu:build")
    await chat.click("bd:phrase")
    assert "Не нашёл бюджет" in await chat.say("хочу игровой пк")
    text = await chat.say("Хочу белый компьютер за 100к, чтобы CS2 шла в 300 FPS, желательно NVIDIA и 32 ГБ оперативки")
    assert "100 000 ₽" in text and "NVIDIA" in text and "32 ГБ" in text
    assert "Не учитываю" in text and "цвет" in text and "гарантировать не могу" in text
    await chat.click("bd:go")
    text = await chat.click("bd:o:0")
    assert "Цель: 300 FPS" in text and "нет замеров" in text         # цель FPS без бенчмарков не обещается
    for o in range(1):
        assert "NVIDIA" in text
    await chat.no_errors()


async def test_upgrade_flow(chat):
    ids = {"cpu": await part_id(chat.db, "cpu", "Ryzen 5 5600"), "gpu": await part_id(chat.db, "gpu", "RX 7600"),
           "motherboard": await part_id(chat.db, "motherboard", "B550M DS3H"),
           "psu": await part_id(chat.db, "psu", "PK550D")}
    await chat.click("menu:upgrade")
    assert "📸" in "".join(b.text for b in chat.buttons)
    assert "недоступен" in await chat.click("up:photo")
    await chat.click("up:home")
    for cat, pid in ids.items():
        await chat.click(f"pk:{cat}:0")
        await chat.click(f"sel:{pid}")
    await chat.click("up:next")
    await chat.click("up:b:custom")
    assert "Минимальный" in await chat.say("100")
    await chat.say("60к")
    await chat.click("up:g:fps")
    text = await chat.click("up:r:1440p")
    assert "Модернизация" in text and "оценка по классам" in text or "Прирост" in text
    assert "зависит" in text or "может" in text              # осторожная формулировка про баланс
    opt = await chat.click("up:o:0")
    assert "Ожидаемый эффект" in opt and "Совместимость новой конфигурации" in opt
    text = await chat.click("menu:profile")
    await chat.click("profile:history")
    assert "История подборов" in chat.text and "Апгрейд" in chat.text
    await chat.no_errors()


async def test_fps_flow_empty_then_real_data(chat, seeded):
    assert "База замеров пока пуста" in await chat.click("menu:fps")
    cpu, gpu = await part_id(seeded, "cpu", "7500F"), await part_id(seeded, "gpu", "5060 8GB")
    async with seeded.session_factory() as s:
        s.add(FpsBenchmark(cpu_id=cpu, gpu_id=gpu, game="CS2", resolution="1080p", preset="Low", avg_fps=280,
                           source_name="Тестовая лаборатория", test_date=None))
        await s.commit()
    await chat.click("menu:fps")
    await chat.click("pk:cpu:0"); await chat.click(f"sel:{cpu}")
    await chat.click("pk:gpu:0"); await chat.click(f"sel:{gpu}")
    await chat.click("fp:games"); await chat.click("fp:g:0"); await chat.click("fp:r:0")
    text = await chat.click("fp:p:0")
    assert "280" in text and "Тестовая лаборатория" in text and "недостаточно данных" in text
    # другая связка — честное «нет данных»
    await chat.click("fp:home")
    await chat.click("pk:gpu:0"); await chat.click(f"sel:{await part_id(seeded, 'gpu', '5090')}")
    await chat.click("fp:games"); await chat.click("fp:g:0"); await chat.click("fp:r:0")
    text = await chat.click("fp:p:0")
    assert "Нет данных" in text and "280" not in text.split("Замеры")[0]
    await chat.no_errors()


async def test_prices_flow(chat, seeded):
    gpu = await part_id(seeded, "gpu", "5060 8GB")
    await chat.click("menu:prices")
    await chat.click("pk:gpu:0")
    text = await chat.click(f"sel:{gpu}")
    assert "ТестМагазин" in text and "Лучшая цена" in text and "https://example.test/item" in text
    text = await chat.click("pr:sources")
    for shop in ("DNS", "Ситилинк", "Ozon", "Wildberries"):
        assert shop in text
    assert "не подключён" in text and "не настроен" in text
    # деталь без предложений
    async with seeded.session_factory() as s:
        from sqlalchemy import delete
        from bot.models import PriceOffer
        await s.execute(delete(PriceOffer).where(PriceOffer.component_id == gpu))
        await s.commit()
    await chat.click("pr:home"); await chat.click("pk:gpu:0")
    text = await chat.click(f"sel:{gpu}")
    assert "Нет данных о ценах" in text and "Придумывать цены я не буду" in text
    await chat.no_errors()


async def test_admin_access_control(chat, admin_chat):
    assert "⛔" in await chat.say("/admin")
    await chat.click("ad:stats")
    assert any("Нет доступа" in a for a in chat.alerts())
    assert not chat.has_button("Админ-панель")
    await chat.say("/start")
    assert not chat.has_button("Админ-панель")
    await admin_chat.say("/start")
    assert admin_chat.has_button("Админ-панель")
    assert "Админ-панель" in await admin_chat.say("/admin")


async def test_admin_stats_errors_catalog_search(admin_chat, seeded):
    await admin_chat.say("/start")
    text = await admin_chat.click("ad:stats")
    assert "Пользователей" in text and "Использование функций" in text
    assert "Ошибок нет" in await admin_chat.click("ad:errors")
    async with seeded.session_factory() as s:
        await user_service.save_error(s, "ValueError", "<boom>", "tb", 1)
        await s.commit()
    text = await admin_chat.click("ad:errors")
    assert "ValueError" in text and "&lt;boom&gt;" in text
    assert "Каталог" in await admin_chat.click("ad:catalog")
    await admin_chat.click("ad:search")
    await admin_chat.say("5090")
    pid = await part_id(seeded, "gpu", "5090")
    assert "активна" in await admin_chat.click(f"ad:part:{pid}")
    assert "отключена" in await admin_chat.click(f"ad:toggle:{pid}")
    await admin_chat.click("menu:prices")
    await admin_chat.click("pk:gpu:0")
    assert not any("5090" in b.text for b in admin_chat.buttons)        # отключённая деталь пропала из списков


async def test_admin_imports_via_documents(admin_chat, seeded, fake_session):
    await admin_chat.say("/start")
    prices = "name;shop;price\nNVIDIA GeForce RTX 5060 8GB;Новый магазин;31000\n".encode()
    fake_session.file_payload = prices
    await admin_chat.click("ad:imp_prices")
    n = admin_chat.n + 1
    await admin_chat.dp.feed_raw_update(admin_chat.bot, document_update(n + 9000, ADMIN, "p.csv"))
    assert "Импорт цен" in admin_chat.text and "Добавлено: 1" in admin_chat.text

    fake_session.file_payload = json.dumps([{"category": "ssd", "brand": "X", "name": "Test SSD",
                                              "specs": {"capacity_gb": 1, "interface": "NVMe"}}]).encode()
    await admin_chat.click("ad:imp_comp")
    await admin_chat.dp.feed_raw_update(admin_chat.bot, document_update(9500, ADMIN, "c.json"))
    assert "Импорт каталога" in admin_chat.text and "Добавлено: 1" in admin_chat.text
    text = await admin_chat.click("ad:refresh")
    assert "Нет подключённых источников" in text
    await admin_chat.no_errors()


async def test_admin_template_and_broadcast(admin_chat, chat, seeded, monkeypatch):
    from bot.handlers import admin as admin_module

    monkeypatch.setattr(admin_module, "BROADCAST_DELAY", 0)
    await chat.say("/start")
    await admin_chat.say("/start")
    # пользователь выключил уведомления — рассылка его не затрагивает
    quiet = Chat(chat.dp, chat.bot, chat.fake, chat.db, 700)
    await quiet.say("/start")
    await quiet.click("menu:profile"); await quiet.click("profile:notify")

    await admin_chat.click("ad:bc")
    assert "Получателей" in admin_chat.text and "<b>2</b>" in admin_chat.text
    await admin_chat.say("Привет всем <3")
    assert "Предпросмотр" in admin_chat.text
    before = len(chat.fake.requests)
    await admin_chat.click("ad:bc_go")
    import asyncio
    for _ in range(50):
        await asyncio.sleep(0.01)
        if "Рассылка завершена" in admin_chat.text:
            break
    sent_to = [m.chat_id for m in chat.fake.requests[before:] if isinstance(m, SendMessage) and "Привет всем" in m.text]
    assert sorted(sent_to) == sorted([USER, ADMIN]) and 700 not in sent_to
    assert "Доставлено: 2" in admin_chat.text


async def test_photo_and_unknown_messages_are_honest(chat):
    n = 5000
    await chat.dp.feed_raw_update(chat.bot, photo_update(n, USER))
    assert "Анализ по фото пока недоступен" in chat.text and "не буду угадывать" in chat.text
    assert "Не понял сообщение" in await chat.say("абракадабра")
    await chat.click("totally:unknown")
    assert any("устарела" in a for a in chat.alerts())
    await chat.no_errors()


async def test_state_is_reset_by_commands(chat):
    await chat.click("menu:build")
    await chat.click("bd:b:custom")
    await chat.say("/menu")
    assert "Не понял" in await chat.say("100000")                  # состояние сброшено, текст не уходит в бюджет
    await chat.no_errors()


async def test_delete_all_data_removes_builds(chat, seeded):
    await chat.click("menu:build")
    for cb in ("bd:b:120000", "bd:p:work", "bd:pr:balanced", "bd:cv:any", "bd:gv:any", "bd:c:new", "bd:go", "bd:o:0",
               "bd:save:0", "bd:savedef", "profile:delete", "profile:delete_yes"):
        await chat.click(cb)
    async with seeded.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Build)) == 0
    await chat.no_errors()
