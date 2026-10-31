import time
from dataclasses import replace

import pytest

import config
from bot.services import catalog_service
from bot.services.builder import BuildRequest, select_builds, shortlist
from bot.utils.telegram import fit_text
from config import ConfigError, load_settings

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_fit_text_never_exceeds_limit_and_keeps_lines_whole():
    line = "<b>строка</b> " + "x" * 50
    text = "\n".join([line] * 200)
    fitted = fit_text(text)
    assert len(fitted) <= 4010 and fitted.endswith("…")
    assert all(l.count("<b>") == l.count("</b>") for l in fitted.splitlines()[:-1])
    assert fit_text("коротко") == "коротко"


def test_price_settings(monkeypatch, tmp_path):
    for name in ("BOT_TOKEN", "PRICE_FEED_URL", "PRICE_REFRESH_HOURS", "PRICE_FEED_TOKEN", "PRICE_FEED_SHOP"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setenv("BOT_TOKEN", TOKEN)
    s = load_settings(tmp_path / "none.env")
    assert s.price_feed_url is None and s.price_refresh_hours == 6
    monkeypatch.setenv("PRICE_FEED_URL", "https://feed.example/p.json")
    monkeypatch.setenv("PRICE_REFRESH_HOURS", "0")
    s = load_settings(tmp_path / "none.env")
    assert s.price_feed_url and s.price_refresh_hours == 0
    monkeypatch.setenv("PRICE_REFRESH_HOURS", "-1")
    with pytest.raises(ConfigError):
        load_settings(tmp_path / "none.env")
    assert TOKEN not in repr(s)


async def test_large_catalog_stays_fast_and_correct(seeded):
    async with seeded.session_factory() as s:
        parts = await catalog_service.list_parts(s)
    cpus = [p for p in parts if p.category == "cpu"]
    gpus = [p for p in parts if p.category == "gpu"]
    clones = []
    for i in range(1, 8):    # ~8x больше процессоров и видеокарт
        clones += [replace(p, id=10_000 * i + p.id, name=f"{p.name} v{i}", price=p.price + 137 * i) for p in cpus + gpus]
    big = parts + clones
    assert len(shortlist([p for p in big if p.category == "cpu"], lambda p: p.spec("gaming") + p.spec("multi"))) <= 30
    started = time.perf_counter()
    result = select_builds(big, BuildRequest(budget=150_000))
    assert time.perf_counter() - started < 5
    assert result.status == "ok" and all(o.total <= 150_000 for o in result.options)
