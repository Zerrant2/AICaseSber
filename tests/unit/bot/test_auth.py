"""The login path uses real session semantics without contacting Telegram."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.types import Chat, Message, Update

from socrat.bot import auth, texts
from socrat.bot.middleware import AuthMiddleware
from socrat.config import get_settings
from socrat.contracts import Role, SessionInfo
from socrat.testing.fakes import InMemoryTeacherRepository


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-test-password")
    monkeypatch.setenv("APP_SECRET", "bot-test-secret")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def test_admin_and_teacher_passwords(settings) -> None:
    teachers = InMemoryTeacherRepository()
    credentials = await teachers.create("Педагог", Role.TEACHER)
    admin = await auth.authenticate_password("admin-test-password", teachers, settings)
    assert admin is not None and admin.role == Role.ADMIN and admin.teacher_id is None
    teacher = await auth.authenticate_password(credentials.plain_password, teachers, settings)
    assert teacher is not None and teacher.nick == "Педагог"
    assert await auth.authenticate_password("wrong", teachers, settings) is None
    assert texts.ADMIN_PANEL in [button.text for row in auth.main_menu(Role.ADMIN).keyboard for button in row]
    assert texts.ADMIN_PANEL not in [
        button.text for row in auth.main_menu(Role.TEACHER).keyboard for button in row
    ]


async def test_password_message_is_deleted_and_session_created(settings) -> None:
    teachers = InMemoryTeacherRepository()
    credentials = await teachers.create("Педагог", Role.TEACHER)
    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(id=123)
    message.text = credentials.plain_password
    message.delete = AsyncMock()
    message.answer = AsyncMock()
    store = SimpleNamespace(
        is_locked=AsyncMock(return_value=False),
        create=AsyncMock(),
        clear_failed_attempts=AsyncMock(),
        register_failed_attempt=AsyncMock(),
    )
    services = SimpleNamespace(teachers=teachers)
    await auth.password_input(message, store, services, settings)
    message.delete.assert_awaited_once()
    assert store.create.await_args.args[0] == 123
    assert isinstance(store.create.await_args.args[1], SessionInfo)
    store.clear_failed_attempts.assert_awaited_once_with(123)
    store.register_failed_attempt.assert_not_awaited()


async def test_middleware_blocks_unauthenticated_commands(settings) -> None:
    store = SimpleNamespace(get=AsyncMock(return_value=None))
    middleware = AuthMiddleware(store)
    handler = AsyncMock()
    message = MagicMock(spec=Message)
    message.chat = SimpleNamespace(id=123)
    message.answer = AsyncMock()
    message.text = "/logout"
    await middleware(handler, message, {})
    handler.assert_not_awaited()
    message.answer.assert_awaited_once_with(texts.LOGIN_REQUIRED)

    message.text = texts.BOUNDARIES
    await middleware(handler, message, {})
    handler.assert_not_awaited()

    message.text = "/start"
    data = {}
    await middleware(handler, message, data)
    handler.assert_awaited_once()
    assert data["session"] is None
    assert data["session_store"] is store


async def test_dispatcher_injects_session_before_auth_filter(settings) -> None:
    class RecordingBot(Bot):
        def __init__(self) -> None:
            super().__init__(token="123:test")
            self.methods: list[object] = []

        async def __call__(self, method, request_timeout=None):
            self.methods.append(method)
            return None

    store = SimpleNamespace(get=AsyncMock(return_value=None))
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(AuthMiddleware(store))
    dispatcher.include_router(auth.router)
    bot = RecordingBot()
    try:
        update = Update(
            update_id=1,
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=Chat(id=123, type="private"),
                text="/start",
            ),
        )
        await dispatcher.feed_update(bot, update)
        assert any(getattr(method, "text", None) == texts.START for method in bot.methods)
    finally:
        await bot.session.close()
