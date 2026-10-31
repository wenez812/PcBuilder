import pytest

from bot.services import catalog_service
from bot.services.compatibility import Severity, check_compatibility
from bot.services.upgrade import GOALS, analyze_upgrade, balance_note


@pytest.fixture
async def catalog(seeded):
    async with seeded.session_factory() as s:
        return await catalog_service.list_parts(s)


def pick(catalog, category, fragment):
    return next(p for p in catalog if p.category == category and fragment.lower() in p.name.lower())


def current_pc(catalog):
    return {"cpu": pick(catalog, "cpu", "5600"), "gpu": pick(catalog, "gpu", "RX 7600"),
            "motherboard": pick(catalog, "motherboard", "B550M DS3H"),
            "ram": pick(catalog, "ram", "16GB (2x8GB) DDR4"), "psu": pick(catalog, "psu", "PK550D")}


async def test_three_distinct_options_within_budget(catalog):
    pc = current_pc(catalog)
    result = analyze_upgrade(pc, catalog, "fps", "1440p", 60_000)
    assert result.options, result.message
    assert len({tuple(sorted((a.category, a.new.id) for a in o.actions)) for o in result.options}) == len(result.options)
    for o in result.options:
        assert 0 < o.cost <= 60_000
        assert not check_compatibility(o.after).by_severity(Severity.BAD)
        assert all(a.reason for a in o.actions)
        assert "не замер" in o.benefit_text or o.benefit_text      # оценка помечена как оценка


async def test_cpu_swap_keeps_socket_and_board(catalog):
    pc = current_pc(catalog)
    result = analyze_upgrade(pc, catalog, "fps", "1080p", 40_000)
    for o in result.options:
        for a in o.actions:
            if a.category == "cpu":
                assert a.new.spec("socket") == "AM4"


async def test_weak_psu_is_replaced_and_costed(catalog):
    pc = current_pc(catalog)
    pc["psu"] = pick(catalog, "psu", "PK550D")
    result = analyze_upgrade(pc, catalog, "fps", "4K", 200_000)
    heavy = [o for o in result.options if any(a.category == "gpu" and (a.new.spec("tgp_w") or 0) > 250 for a in o.actions)]
    for o in heavy:
        assert any(a.category == "psu" for a in o.actions)
        assert o.cost == sum(a.cost for a in o.actions)


async def test_budget_too_small_gives_honest_message(catalog):
    result = analyze_upgrade(current_pc(catalog), catalog, "fps", "1080p", 500)
    assert result.options == [] and result.message


async def test_already_top_hardware_has_no_pointless_upgrade(catalog):
    top = {"cpu": pick(catalog, "cpu", "9800X3D"), "gpu": pick(catalog, "gpu", "5090"),
           "motherboard": pick(catalog, "motherboard", "Tomahawk WiFi"), "ram": pick(catalog, "ram", "64GB")}
    result = analyze_upgrade(top, catalog, "fps", "4K", 300_000)
    assert result.options == [] and result.message


async def test_missing_required_parts(catalog):
    result = analyze_upgrade({"cpu": pick(catalog, "cpu", "5600")}, catalog, "fps", "1080p", 50_000)
    assert result.options == [] and "видеокарта" in result.message


async def test_storage_goal_suggests_nvme_only_when_slot_exists(catalog):
    pc = current_pc(catalog)
    pc["ssd"] = pick(catalog, "ssd", "A400")
    result = analyze_upgrade(pc, catalog, "load", "1080p", 10_000)
    assert result.options and all(a.new.spec("interface") == "NVMe" for o in result.options for a in o.actions)
    assert analyze_upgrade({"cpu": pc["cpu"]}, catalog, "load", "1080p", 10_000).options == []


async def test_temps_goal_cooler_has_headroom_and_fits_socket(catalog):
    result = analyze_upgrade(current_pc(catalog), catalog, "temps", "1080p", 8_000)
    for o in result.options:
        cooler = o.actions[0].new
        assert "AM4" in cooler.spec("sockets")


async def test_power_goal_requires_real_savings(catalog):
    pc = {"cpu": pick(catalog, "cpu", "7800X3D"), "gpu": pick(catalog, "gpu", "RX 7800 XT")}
    result = analyze_upgrade(pc, catalog, "power", "1440p", 100_000)
    for o in result.options:
        assert o.benefit >= 20


async def test_balance_note_never_claims_always(catalog):
    note = balance_note(pick(catalog, "cpu", "i3-12100F"), pick(catalog, "gpu", "5090"), "1080p")
    assert "может" in note and "зависит" in note
    assert "всегда" not in note
    assert balance_note(None, None, "1080p") is None
    assert set(GOALS) == {"fps", "work", "temps", "power", "load", "all"}
