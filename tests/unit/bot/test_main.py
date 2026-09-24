"""Startup credential checks for the Telegram bot."""

import logging

import pytest

from socrat.bot import main as bot_main
from socrat.config import Settings


@pytest.mark.parametrize(
    ("admin_password", "app_secret", "missing_setting"),
    [
        ("short", "a" * 32, "ADMIN_PASSWORD"),
        ("long-enough", "short", "APP_SECRET"),
    ],
)
async def test_real_bot_rejects_short_credentials_at_startup(
    monkeypatch: pytest.MonkeyPatch, admin_password: str, app_secret: str, missing_setting: str
) -> None:
    settings = Settings(
        _env_file=None,
        use_fakes=False,
        admin_password=admin_password,
        app_secret=app_secret,
        telegram_bot_token="123:test",
    )
    monkeypatch.setattr(bot_main, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match=missing_setting):
        await bot_main.main()


def test_fake_bot_warns_about_both_short_credentials(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(_env_file=None, use_fakes=True, admin_password="short", app_secret="short")

    with caplog.at_level(logging.WARNING):
        bot_main._validate_startup_secrets(settings)

    assert "ADMIN_PASSWORD" in caplog.text
    assert "APP_SECRET" in caplog.text
    assert "short" not in caplog.text


def test_valid_credentials_have_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(_env_file=None, use_fakes=False, admin_password="long-enough", app_secret="a" * 32)

    with caplog.at_level(logging.WARNING):
        bot_main._validate_startup_secrets(settings)

    assert not caplog.records
