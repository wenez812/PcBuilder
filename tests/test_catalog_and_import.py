import json

from sqlalchemy import func, select

from bot.models import Component, FpsBenchmark, PriceOffer
from bot.services import catalog_service, importers
from bot.services.seed_data import SEED_COMPONENTS
from tests.conftest import part_id


async def test_seed_is_idempotent_and_has_no_prices(db):
    async with db.session_factory() as s:
        assert await catalog_service.seed_catalog(s) == len(SEED_COMPONENTS)
        assert await catalog_service.seed_catalog(s) == 0
        assert await s.scalar(select(func.count()).select_from(PriceOffer)) == 0   # цен в каталоге нет
        parts = await catalog_service.list_parts(s)
        assert all(p.price is None and not p.in_stock for p in parts)
        assert {p.category for p in parts} == {"cpu", "gpu", "motherboard", "ram", "ssd", "psu", "case", "cooler"}


def test_seed_data_is_internally_consistent():
    names = [(e["category"], e["name"]) for e in SEED_COMPONENTS]
    assert len(names) == len(set(names))
    for e in SEED_COMPONENTS:
        if e["category"] == "cpu":
            assert {"socket", "family", "mem_types", "gaming", "multi", "tdp_w"} <= set(e["specs"])
            assert set(e["specs"]["max_mem_mhz"]) <= set(e["specs"]["mem_types"])
        if e["category"] == "motherboard":
            assert e["specs"]["socket"] in {"AM4", "AM5", "LGA1700", "LGA1851"}
        assert "price" not in e["specs"] and "price" not in e


async def test_best_price_uses_only_fresh_in_stock(seeded):
    async with seeded.session_factory() as s:
        cid = await part_id(seeded, "gpu", "5060 8GB")
        await importers.upsert_offer(s, cid, "Дешёвый, но нет в наличии", 1, False, None)
        await s.commit()
        part = await catalog_service.get_part(s, cid)
        assert part.price > 1 and part.offers_count == 2


async def test_search_exact_fuzzy_and_empty(seeded):
    async with seeded.session_factory() as s:
        parts = await catalog_service.list_parts(s, "gpu")
    assert [p.name for p in catalog_service.search_parts(parts, "rtx 5060 ti")] == ["NVIDIA GeForce RTX 5060 Ti 16GB"]
    assert catalog_service.search_parts(parts, "geforse rtx 5070")      # опечатка находится нечётко
    assert catalog_service.search_parts(parts, "   ") == []
    assert catalog_service.search_parts(parts, "zzzzzzzz") == []


async def test_import_components_json(db):
    good = {"category": "gpu", "brand": "NVIDIA", "name": "Test GPU", "specs": {"vram_gb": 8}}
    bad = [{"category": "xxx", "name": "a", "specs": {}}, {"category": "gpu", "name": "", "specs": {}},
           {"category": "gpu", "name": "b", "specs": []}, "строка"]
    async with db.session_factory() as s:
        report = await importers.import_components_json(s, json.dumps([good, *bad]).encode())
        assert (report.added, report.skipped) == (1, 4)
        again = await importers.import_components_json(s, json.dumps([{**good, "specs": {"vram_gb": 12}}]).encode())
        assert (again.added, again.updated) == (0, 1)
        row = (await s.execute(select(Component).where(Component.name == "Test GPU"))).scalar_one()
        assert row.specs == {"vram_gb": 12}
        assert (await importers.import_components_json(s, b"not json")).errors


async def test_import_prices_csv_validation(seeded):
    csv_text = ("category;name;shop;price;in_stock;url\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;DNS;34 999;1;https://dns.example/x\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;Ozon;abc;1;\n"
                "gpu;Несуществующая карта;DNS;1000;1;\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;;1000;1;\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;Ситилинк;-5;1;\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;WB;40000;1;javascript:alert(1)\n"
                "gpu;NVIDIA GeForce RTX 5060 8GB;Пустая цена;;1;\n"
                "foo;NVIDIA GeForce RTX 5060 8GB;DNS;1000;1;\n")
    async with seeded.session_factory() as s:
        report = await importers.import_prices_csv(s, csv_text.encode("utf-8"))
        assert report.added == 1 and report.updated == 0        # принято только корректное предложение DNS
        assert len(report.errors) == 6                          # остальные отклонены; пустая цена — тихий пропуск
        assert report.skipped == 7
        part = await catalog_service.get_part(s, await part_id(seeded, "gpu", "5060 8GB"))
        offers = await catalog_service.get_offers(s, part.id)
        assert any(o.shop == "DNS" and o.price == 34999 for o in offers)
        assert not any(o.shop in {"Ozon", "WB", "Ситилинк"} for o in offers)


async def test_import_prices_requires_columns_and_handles_cp1251(db):
    async with db.session_factory() as s:
        assert (await importers.import_prices_csv(s, b"foo;bar\n1;2\n")).errors
        await catalog_service.seed_catalog(s)
        data = "name;price;shop\nAMD Ryzen 5 7500F;10000;Магазин\n".encode("cp1251")
        report = await importers.import_prices_csv(s, data)
        assert report.added == 1


def test_price_template_has_empty_prices():
    raw = importers.price_template_csv([("cpu", "A"), ("gpu", "B")]).decode("utf-8-sig")
    rows = [r.split(";") for r in raw.strip().splitlines()]
    assert rows[0] == list(importers.PRICE_COLUMNS) and rows[1][2:4] == ["", ""]


async def test_import_benchmarks_requires_source_and_valid_values(seeded):
    header = ";".join(importers.BENCH_COLUMNS)
    ok = "AMD Ryzen 5 7500F;NVIDIA GeForce RTX 5060 8GB;CS2;1080p;Low;280;190;DDR5-6000;1.0;2026-01-15;Тест-лаборатория;https://example.test/b;"
    rows = [
        ok,
        "AMD Ryzen 5 7500F;NVIDIA GeForce RTX 5060 8GB;CS2;1080p;Low;280;190;;;;;;",            # нет источника
        "AMD Ryzen 5 7500F;NVIDIA GeForce RTX 5060 8GB;CS2;1080p;Low;100;150;;;;Src;;",         # low1 > avg
        "AMD Ryzen 5 7500F;NVIDIA GeForce RTX 5060 8GB;CS2;8K;Low;100;;;;;Src;;",                # разрешение
        "AMD Ryzen 5 7500F;NVIDIA GeForce RTX 5060 8GB;CS2;1080p;Low;100;;;;15.01.2026;Src;;",   # дата
        "Неизвестный CPU;NVIDIA GeForce RTX 5060 8GB;CS2;1080p;Low;100;;;;;Src;;",
    ]
    async with seeded.session_factory() as s:
        report = await importers.import_benchmarks_csv(s, (header + "\n" + "\n".join(rows)).encode())
        assert (report.added, report.skipped) == (1, 5)
        row = (await s.execute(select(FpsBenchmark))).scalar_one()
        assert row.source_name == "Тест-лаборатория" and row.low1_fps == 190
