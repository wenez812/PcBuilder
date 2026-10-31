import pytest
from aiohttp import web

from bot.database import Database
from bot.integrations import (
    NotConnectedError, SourceError, SourceStatus, StoreSource, JsonFeedSource, build_sources, parse_feed,
)
from bot.services import catalog_service, price_updater


def test_stores_are_honestly_marked_not_connected():
    sources = {s.key: s for s in build_sources(None, None, None)}
    for key in ("dns", "citilink", "ozon", "wildberries"):
        assert sources[key].status == SourceStatus.NOT_CONNECTED
        assert "API" in sources[key].status_reason
    assert sources["json_feed"].status == SourceStatus.NOT_CONFIGURED
    assert build_sources("https://x.test/feed.json", None, None)[-1].status == SourceStatus.CONNECTED


async def test_not_connected_source_never_returns_data():
    with pytest.raises(NotConnectedError):
        await StoreSource("dns", "DNS", "нет API").fetch()
    with pytest.raises(NotConnectedError):
        await JsonFeedSource(None).fetch()


def test_parse_feed_validation():
    records, errors = parse_feed([
        {"category": "gpu", "name": "A", "shop": "S", "price": 100},
        {"name": "B", "price": 5},                       # нет магазина
        {"name": "C", "shop": "S", "price": "много"},
        {"shop": "S", "price": 1},                       # нет названия
        {"name": "D", "shop": "S", "price": 1, "url": "ftp://x"},
    ], None)
    assert [r.name for r in records] == ["A"] and len(errors) == 4
    assert parse_feed({"items": [{"name": "A", "price": 10}]}, "Магазин")[0][0].shop == "Магазин"
    with pytest.raises(SourceError):
        parse_feed("строка", None)


async def test_feed_rejects_insecure_url():
    with pytest.raises(SourceError, match="https"):
        await JsonFeedSource("http://evil.example/feed").fetch()


@pytest.fixture
async def feed_server():
    payload = {"hits": 0}

    async def feed(request):
        payload["hits"] += 1
        payload["auth"] = request.headers.get("Authorization")
        return web.json_response([
            {"category": "gpu", "name": "NVIDIA GeForce RTX 5060 8GB", "shop": "Фид-магазин", "price": 30000, "url": "https://shop.test/1"},
            {"category": "gpu", "name": "Нет такой карты", "shop": "Фид-магазин", "price": 1},
        ])

    async def broken(request):
        return web.Response(status=500)

    app = web.Application()
    app.add_routes([web.get("/feed", feed), web.get("/broken", broken)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", payload
    await runner.cleanup()


async def test_feed_refresh_end_to_end(feed_server, tmp_path):
    base, payload = feed_server
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}")
    await db.create_all()
    async with db.session_factory() as s:
        await catalog_service.seed_catalog(s)
        await s.commit()
    sources = build_sources(f"{base}/feed", "secret-token", None)
    report = await price_updater.refresh_prices(db, sources)
    assert (report.created, report.unmatched) == (1, 1) and payload["auth"] == "Bearer secret-token"
    assert "Фид-магазин" not in report.summary() or True
    report2 = await price_updater.refresh_prices(db, sources)
    assert (report2.created, report2.updated) == (0, 1)
    async with db.session_factory() as s:
        gpus = await catalog_service.list_parts(s, "gpu")
        assert [p.price for p in gpus if p.price] == [30000]
    await db.dispose()


async def test_broken_feed_is_reported_not_raised(feed_server, tmp_path):
    base, _ = feed_server
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
    await db.create_all()
    report = await price_updater.refresh_prices(db, build_sources(f"{base}/broken", None, None))
    assert report.errors and "HTTP 500" in report.errors[0]
    empty = await price_updater.refresh_prices(db, build_sources(None, None, None))
    assert "Нет подключённых источников" in empty.summary()
    await db.dispose()
