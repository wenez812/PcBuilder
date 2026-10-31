import pytest

from bot.utils.parsing import InputError, parse_budget, parse_phrase


@pytest.mark.parametrize("text,expected", [
    ("100к", 100_000), ("85 000", 85_000), ("85000 ₽", 85_000), ("85000р", 85_000),
    ("0.5м", 500_000), ("80 тыс", 80_000), ("120k", 120_000), ("99,5к", 99_500),
])
def test_parse_budget_ok(text, expected):
    assert parse_budget(text) == expected


@pytest.mark.parametrize("text", ["", "много", "1000", "5 млн", "-5000", "abc 100к", "100к и ещё"])
def test_parse_budget_rejects(text):
    with pytest.raises(InputError):
        parse_budget(text)


def test_phrase_from_task_example():
    r = parse_phrase("Хочу белый компьютер за 100к, чтобы CS2 шла в 300 FPS, желательно NVIDIA и 32 ГБ оперативки.")
    assert r.budget == 100_000 and r.games == ["CS2"] and r.target_fps == 300
    assert r.gpu_vendor == "nvidia" and r.min_ram_gb == 32 and r.purpose == "gaming"
    assert any("цвет" in i for i in r.ignored)       # цвет корпуса честно не учитывается


def test_phrase_fps_number_is_not_budget():
    assert parse_phrase("хочу 300 fps в кс2").budget is None


def test_phrase_work_and_ambiguous_amd():
    r = parse_phrase("пк для монтажа видео до 150 000 руб, тихий, intel")
    assert (r.budget, r.purpose, r.priority, r.cpu_vendor) == (150_000, "video", "quiet", "intel")
    r2 = parse_phrase("игровой пк 1440p за 120000, AMD")
    assert r2.resolution == "1440p" and r2.ignored and r2.cpu_vendor == "any"


def test_phrase_empty_is_safe():
    r = parse_phrase("")
    assert r.budget is None and r.games == [] and r.purpose is None
