import pytest
from sqlalchemy import func, select

from bot.models import Build, BuildItem, FpsBenchmark
from bot.services import build_service, catalog_service, fps_service, user_service
from bot.services.build_service import BuildError
from bot.services.builder import BuildRequest, select_builds
from tests.conftest import part_id


async def _add_bench(db, session, **kw):
    cpu, gpu = await part_id(db, "cpu", "7500F"), await part_id(db, "gpu", "5060 8GB")
    base = dict(cpu_id=cpu, gpu_id=gpu, game="CS2", resolution="1080p", preset="Low", avg_fps=280,
                low1_fps=None, source_name="Источник А")
    base.update(kw)
    session.add(FpsBenchmark(**base))
    await session.flush()


async def test_fps_only_real_rows_and_no_mixing(seeded):
    async with seeded.session_factory() as s:
        assert await fps_service.benchmark_count(s) == 0
        await _add_bench(seeded, s, avg_fps=280, source_name="Источник А", game_version="1.0")
        await _add_bench(seeded, s, avg_fps=240, source_name="Источник Б", game_version="2.0")
        await _add_bench(seeded, s, preset="High", avg_fps=150)
        cpu, gpu = await part_id(seeded, "cpu", "7500F"), await part_id(seeded, "gpu", "5060 8GB")
        rows = await fps_service.find_benchmarks(s, cpu, gpu, "CS2", "1080p", "Low")
        assert sorted(r.avg_fps for r in rows) == [240, 280]
        text = fps_service.render_benchmarks("CPU", "GPU", "CS2", "1080p", "Low", rows, [])
        assert "Источник А" in text and "Источник Б" in text and "не усреднялись" in text
        assert "недостаточно данных" in text                       # 1% Low не выдумывается


async def test_fps_missing_data_is_reported_honestly(seeded):
    async with seeded.session_factory() as s:
        await _add_bench(seeded, s)
        cpu = await part_id(seeded, "cpu", "9800X3D")
        gpu = await part_id(seeded, "gpu", "5090")
        assert await fps_service.find_benchmarks(s, cpu, gpu, "CS2", "1080p", "Low") == []
        others = await fps_service.other_measurements(s, "CS2", "1080p", "Low")
        text = fps_service.render_benchmarks("A", "B", "CS2", "1080p", "Low", [], others)
        assert "Нет данных" in text and "не выдумываю" in text and "напрямую не сопоставимы" in text
        assert await fps_service.available_games(s) == ["CS2"]


@pytest.fixture
async def user(db):
    async with db.session_factory() as s:
        u, _ = await user_service.get_or_create_user(s, 77, "u", "U")
        await s.commit()
        return u


async def _parts(db):
    async with db.session_factory() as s:
        return select_builds(await catalog_service.list_parts(s), BuildRequest(budget=120_000)).options[0].parts


async def test_build_lifecycle(seeded, user):
    parts = await _parts(seeded)
    async with seeded.session_factory() as s:
        b = await build_service.create_build(s, user, "  Моя   сборка ", parts, {"budget": 120000})
        assert b.name == "Моя сборка" and len(b.public_id) == 8
        loaded = await build_service.load_parts(s, b)
        assert {c: p.id for c, p in loaded.items()} == {c: p.id for c, p in parts.items()}

        new_gpu = next(p for p in await catalog_service.list_parts(s, "gpu") if p.id != parts["gpu"].id)
        await build_service.replace_item(s, b, "gpu", new_gpu)
        await build_service.remove_item(s, b, "cooler")
        reloaded = await build_service.load_parts(s, b)
        assert reloaded["gpu"].id == new_gpu.id and "cooler" not in reloaded
        with pytest.raises(BuildError):
            await build_service.replace_item(s, b, "cpu", new_gpu)       # деталь не той категории

        await build_service.rename_build(s, b, "Другое имя")
        assert b.name == "Другое имя"
        await build_service.delete_build(s, b)
        assert await s.scalar(select(func.count()).select_from(Build)) == 0
        assert await s.scalar(select(func.count()).select_from(BuildItem)) == 0


async def test_build_ids_unique_and_validation(seeded, user):
    parts = await _parts(seeded)
    async with seeded.session_factory() as s:
        ids = {(await build_service.create_build(s, user, f"b{i}", parts)).public_id for i in range(5)}
        assert len(ids) == 5
        for bad in ("", "   ", "x" * 65):
            with pytest.raises(BuildError):
                build_service.validate_build_name(bad)
        with pytest.raises(BuildError):
            await build_service.create_build(s, user, "пустая", {})
    assert build_service.validate_build_name("a\x00b\nc") == "a b c"


async def test_build_limit(seeded, user, monkeypatch):
    monkeypatch.setattr(build_service, "MAX_BUILDS_PER_USER", 2)
    parts = await _parts(seeded)
    async with seeded.session_factory() as s:
        await build_service.create_build(s, user, "1", parts)
        await build_service.create_build(s, user, "2", parts)
        with pytest.raises(BuildError, match="лимит"):
            await build_service.create_build(s, user, "3", parts)


async def test_ownership_and_sharing_rules(seeded, user):
    parts = await _parts(seeded)
    async with seeded.session_factory() as s:
        other, _ = await user_service.get_or_create_user(s, 88)
        b = await build_service.create_build(s, user, "Приватная", parts)
        assert await build_service.get_user_build(s, other, b.id) is None          # чужую сборку не открыть
        assert await build_service.get_shared_build(s, b.public_id) is None       # пока не расшарена
        await build_service.set_shared(s, b, True)
        assert (await build_service.get_shared_build(s, b.public_id)).id == b.id
        assert await build_service.get_shared_build(s, "../../etc") is None       # невалидный ID
        copy = await build_service.copy_build(s, b, other)
        assert copy.user_id == other.id and copy.public_id != b.public_id and "(копия)" in copy.name
        await build_service.set_shared(s, b, False)
        assert await build_service.get_shared_build(s, b.public_id) is None


async def test_compare_builds(seeded, user):
    parts = await _parts(seeded)
    async with seeded.session_factory() as s:
        other = dict(parts)
        other["gpu"] = next(p for p in await catalog_service.list_parts(s, "gpu") if p.id != parts["gpu"].id)
        rows = {r["category"]: r for r in build_service.compare_builds(parts, other)}
        assert rows["cpu"]["same"] and not rows["gpu"]["same"]
        assert rows["gpu"]["price_diff"] == other["gpu"].price - parts["gpu"].price


async def test_history_is_limited_and_deleted_with_user(seeded, user):
    async with seeded.session_factory() as s:
        for i in range(55):
            await build_service.add_history(s, user, "build", f"запрос {i}", None, 1000 + i)
        history = await build_service.list_history(s, user, 100)
        assert len(history) == 50 and history[0].summary == "запрос 54"
        await build_service.create_build(s, user, "x", await _parts(seeded))
        assert await user_service.delete_user_data(s, user.telegram_id)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(Build)) == 0
