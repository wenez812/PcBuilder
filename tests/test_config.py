import pytest

import config
from config import ConfigError, load_settings, parse_admin_ids

VALID_TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in (
        "BOT_TOKEN", "ADMIN_IDS", "DATABASE_URL", "LOG_LEVEL",
        "THROTTLE_MAX_ACTIONS", "THROTTLE_WINDOW", "THROTTLE_COOLDOWN",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    # пустой .env, чтобы настоящий файл разработчика не влиял на тесты
    return tmp_path / "empty.env"


def test_parse_admin_ids():
    assert parse_admin_ids("1, 2;3  4") == frozenset({1, 2, 3, 4})
    assert parse_admin_ids("") == frozenset()
    assert parse_admin_ids(None) == frozenset()


def test_parse_admin_ids_rejects_garbage():
    with pytest.raises(ConfigError):
        parse_admin_ids("12, abc")


def test_missing_token_raises(clean_env):
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings(clean_env)


def test_malformed_token_raises(monkeypatch, clean_env):
    monkeypatch.setenv("BOT_TOKEN", "not-a-token")
    with pytest.raises(ConfigError, match="формат"):
        load_settings(clean_env)


def test_valid_settings(monkeypatch, clean_env):
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("ADMIN_IDS", "10,20")
    s = load_settings(clean_env)
    assert s.bot_token == VALID_TOKEN
    assert s.is_admin(10) and not s.is_admin(11)
    assert s.database_url.startswith("sqlite+aiosqlite:///")
    assert s.throttle_max_actions == 8


def test_token_not_in_repr(monkeypatch, clean_env):
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    assert VALID_TOKEN not in repr(load_settings(clean_env))


def test_invalid_throttle_value(monkeypatch, clean_env):
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("THROTTLE_WINDOW", "-1")
    with pytest.raises(ConfigError):
        load_settings(clean_env)


def test_invalid_log_level(monkeypatch, clean_env):
    monkeypatch.setenv("BOT_TOKEN", VALID_TOKEN)
    monkeypatch.setenv("LOG_LEVEL", "LOUD")
    with pytest.raises(ConfigError):
        load_settings(clean_env)
