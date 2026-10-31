from dataclasses import replace

import pytest

from bot.services import catalog_service
from bot.services.builder import BuildRequest, performance, select_builds
from bot.services.compatibility import Severity, check_compatibility
from bot.services.parts import CATEGORIES
from bot.services.builder import coverage_problems


@pytest.fixture
async def parts(seeded):
    async with seeded.session_factory() as s:
        return await catalog_service.list_parts(s)


def test_no_prices_means_no_data_not_invented_prices():
    async def run():
        from bot.database import Database
        db = Database("sqlite+aiosqlite:///:memory:")
        await db.create_all()
        async with db.session_factory() as s:
            await catalog_service.seed_catalog(s)
            return await catalog_service.list_parts(s)
    import asyncio
    parts = asyncio.run(run())
    result = select_builds(parts, BuildRequest(budget=100_000))
    assert result.status == "no_data" and result.options == []
    assert len(result.missing_data) == len(CATEGORIES)


async def test_build_respects_budget_and_is_fully_compatible(parts):
    for budget in (80_000, 100_000, 150_000, 250_000):
        result = select_builds(parts, BuildRequest(budget=budget))
        assert result.status in {"ok", "relaxed"}, budget
        for option in result.options:
            assert option.total <= budget
            assert set(option.parts) == set(CATEGORIES)          # все 8 компонентов
            report = check_compatibility(option.parts)
            assert report.status in (Severity.OK, Severity.WARN)  # никаких 🔴 и ⚪
            assert not report.by_severity(Severity.BAD) and not report.by_severity(Severity.UNKNOWN)
            assert all(r for r in option.reasons.values())        # причина выбора для каждой детали
            assert option.total == sum(p.price for p in option.parts.values())


async def test_more_budget_never_gives_weaker_recommended_build(parts):
    perfs = [select_builds(parts, BuildRequest(budget=b, priority="fps")).options[0].perf
             for b in (90_000, 130_000, 200_000)]
    assert perfs == sorted(perfs)


async def test_too_small_budget_is_explained_and_not_exceeded_silently(parts):
    result = select_builds(parts, BuildRequest(budget=20_000))
    assert result.status == "over_budget"
    assert result.min_total and result.min_total > 20_000
    assert any("не удаётся" in n for n in result.notes)
    assert result.options[0].total == result.min_total


async def test_vendor_preferences_are_respected(parts):
    result = select_builds(parts, BuildRequest(budget=130_000, cpu_vendor="intel", gpu_vendor="nvidia"))
    assert result.status == "ok"
    for o in result.options:
        assert o.parts["cpu"].brand == "Intel" and o.parts["gpu"].brand == "NVIDIA"


async def test_impossible_preference_is_relaxed_within_budget(parts):
    # у Intel в каталоге нет платформы дешевле ~этого бюджета при требовании 4K — ослабляем предпочтения
    result = select_builds(parts, BuildRequest(budget=75_000, gpu_vendor="nvidia", resolution="4K"))
    if result.status == "relaxed":
        assert all(o.total <= 75_000 for o in result.options)
        assert any("ослабил" in n for n in result.notes)
    else:
        assert result.status in {"ok", "over_budget"}


async def test_used_parts_only_with_permission(parts):
    used = [replace(p, id=10_000 + p.id, name=p.name + " (б/у)", condition="used", price=1000)
            for p in parts if p.category == "gpu"]
    without = select_builds([*parts, *used], BuildRequest(budget=100_000))
    assert all(o.parts["gpu"].condition == "new" for o in without.options)
    allowed = select_builds([*parts, *used], BuildRequest(budget=100_000, allow_used=True))
    assert any(o.parts["gpu"].condition == "used" for o in allowed.options)


async def test_stale_prices_are_ignored(seeded):
    from datetime import timedelta

    from sqlalchemy import update

    from bot.models import PriceOffer
    from bot.models.base import utcnow

    async with seeded.session_factory() as s:
        await s.execute(update(PriceOffer).values(updated_at=utcnow() - timedelta(days=30)))
        await s.commit()
        parts = await catalog_service.list_parts(s)
    assert all(p.price is None and p.price_stale for p in parts)
    assert select_builds(parts, BuildRequest(budget=200_000)).status == "no_data"


async def test_purpose_changes_weighting(parts):
    cpu_heavy = [p for p in parts if p.category == "cpu"]
    gpu = next(p for p in parts if p.category == "gpu" and "5070 12GB" in p.name)
    weak, strong = min(cpu_heavy, key=lambda c: c.spec("gaming")), max(cpu_heavy, key=lambda c: c.spec("gaming"))
    cs2 = performance(strong, gpu, "gaming", ("CS2",), "1080p") / performance(weak, gpu, "gaming", ("CS2",), "1080p")
    cyber = performance(strong, gpu, "gaming", ("Cyberpunk 2077",), "4K") / performance(weak, gpu, "gaming", ("Cyberpunk 2077",), "4K")
    assert cs2 > cyber    # CS2 на 1080p сильнее зависит от процессора, чем Cyberpunk на 4K


async def test_4k_requires_enough_vram(parts):
    result = select_builds(parts, BuildRequest(budget=250_000, resolution="4K"))
    assert all((o.parts["gpu"].spec("vram_gb") or 0) >= 12 for o in result.options)


async def test_options_are_distinct(parts):
    result = select_builds(parts, BuildRequest(budget=120_000, games=("CS2",)))
    signatures = [tuple(sorted(o.part_ids.items())) for o in result.options]
    assert len(signatures) == len(set(signatures))


async def test_request_roundtrip():
    req = BuildRequest(budget=90_000, purpose="video", games=("CS2",), allow_used=True, min_ram_gb=32)
    assert BuildRequest.from_params(req.to_params()) == req


def test_coverage_problems_lists_missing_categories():
    assert len(coverage_problems([])) == len(CATEGORIES)
