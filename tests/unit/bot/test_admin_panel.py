"""Administrator controls use the real SQL repository in fixture mode."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from socrat.app import build_services, close_services
from socrat.bot import admin_panel, texts
from socrat.config import get_settings
from socrat.contracts import Role, SessionInfo


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET", "test-admin-panel-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("USE_FAKES", "true")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def context() -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=123, user_id=123))


def admin_session() -> SessionInfo:
    return SessionInfo(
        role=Role.ADMIN,
        nick="Администратор",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def callback(data: str, sent=None) -> MagicMock:
    item = MagicMock(spec=CallbackQuery)
    item.data = data
    item.answer = AsyncMock()
    item.message = MagicMock(spec=Message)
    item.message.edit_text = AsyncMock()
    item.message.answer = AsyncMock(return_value=sent)
    return item


async def test_non_admin_cannot_open_panel() -> None:
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    teacher = SessionInfo(
        role=Role.TEACHER,
        nick="Учитель",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await admin_panel.open_admin(message, context(), teacher)
    message.answer.assert_awaited_once_with(texts.ADMIN_FORBIDDEN)


async def test_add_teacher_password_is_removed(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    services = await build_services(settings)
    bot = SimpleNamespace(delete_message=AsyncMock())
    sent = SimpleNamespace(bot=bot, chat=SimpleNamespace(id=123), message_id=50)
    item = callback("adm:newrole:teacher", sent)
    state = context()
    await state.set_state(admin_panel.AdminFlow.role)
    await state.update_data(nick="Учитель")
    monkeypatch.setattr(admin_panel, "PASSWORD_MESSAGE_TTL_SECONDS", 0)
    try:
        await admin_panel.admin_callback(item, state, services, admin_session())
        text = item.message.answer.await_args.args[0]
        password = re.search(r"Пароль: (\w+)", text).group(1)
        assert (await services.teachers.authenticate(password)).nick == "Учитель"
        assert await state.get_state() is None
        await asyncio.sleep(0.01)
        bot.delete_message.assert_awaited_once_with(123, 50)
    finally:
        await close_services(services)


async def test_knowledge_status_usage_and_school_upload(settings) -> None:
    services = await build_services(settings)
    state = context()
    try:
        status = callback("adm:status")
        await admin_panel.admin_callback(status, state, services, admin_session())
        status_text = status.message.edit_text.await_args.args[0]
        assert "Документы:" in status_text and "Результаты:" in status_text

        usage = callback("adm:usage")
        await admin_panel.admin_callback(usage, state, services, admin_session())
        assert "За 30 дней" in usage.message.edit_text.await_args.args[0]

        update = callback("adm:update", SimpleNamespace(edit_text=AsyncMock()))
        await admin_panel.admin_callback(update, state, services, admin_session())
        assert "Обновление завершено" in update.message.answer.return_value.edit_text.await_args.args[0]

        await state.set_state(admin_panel.AdminFlow.upload)
        message = MagicMock(spec=Message)
        message.document = SimpleNamespace(file_name="school.pdf", file_size=4)
        message.bot = SimpleNamespace(download=AsyncMock(return_value=BytesIO(b"%PDF")))
        result_message = SimpleNamespace(edit_text=AsyncMock())
        message.answer = AsyncMock(return_value=result_message)
        await admin_panel.upload_material(message, state, services, admin_session())
        assert await state.get_state() is None
        assert "ненормативный контекст" in result_message.edit_text.await_args.args[0]
    finally:
        await close_services(services)


async def test_teacher_list_and_controls(settings) -> None:
    services = await build_services(settings)
    state = context()
    try:
        teacher = (await services.teachers.create("Тестовый педагог", Role.TEACHER)).account
        listing = callback("adm:list:0")
        await admin_panel.admin_callback(listing, state, services, admin_session())
        markup = listing.message.edit_text.await_args.kwargs["reply_markup"]
        assert "Педагог" in markup.inline_keyboard[0][0].text
        assert "активен" in markup.inline_keyboard[0][0].text

        await admin_panel.admin_callback(
            callback(f"adm:active:{teacher.teacher_id}"), state, services, admin_session()
        )
        assert not (await services.teachers.get(teacher.teacher_id)).active

        await admin_panel.admin_callback(
            callback(f"adm:setrole:{teacher.teacher_id}:methodist"),
            state,
            services,
            admin_session(),
        )
        assert (await services.teachers.get(teacher.teacher_id)).role == Role.METHODIST
    finally:
        await close_services(services)
