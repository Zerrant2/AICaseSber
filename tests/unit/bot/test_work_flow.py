"""Walk through the full fixture-mode work FSM without Telegram network calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from socrat.app import build_services, close_services
from socrat.bot import texts, work_flow
from socrat.bot.middleware import AuthMiddleware
from socrat.config import get_settings
from socrat.contracts import GuardrailCode, GuardrailIssue, GuardrailResult, Role, SessionInfo, Severity


class RecordingBot(Bot):
    def __init__(self) -> None:
        super().__init__(token="123:test")
        self.methods: list[object] = []
        self.next_message_id = 10

    async def __call__(self, method, request_timeout=None):
        self.methods.append(method)
        if isinstance(method, SendMessage):
            self.next_message_id += 1
            return Message(
                message_id=self.next_message_id,
                date=datetime.now(UTC),
                chat=Chat(id=123, type="private"),
                text=method.text,
            ).as_(self)
        return True


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET", "test-work-flow-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("USE_FAKES", "true")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def test_work_flow_delivers_two_files(settings) -> None:
    services = await build_services(settings)
    services.generator.delay_s = 0
    bot = RecordingBot()
    session = SessionInfo(
        role=Role.TEACHER,
        teacher_id=None,
        nick="Учитель",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session_store = SimpleNamespace(get=AsyncMock(return_value=session))
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(AuthMiddleware(session_store))
    dispatcher.callback_query.outer_middleware(AuthMiddleware(session_store))
    dispatcher.include_router(work_flow.router)
    dispatcher["services"] = services
    dispatcher["settings"] = settings
    dispatcher["generation_controller"] = work_flow.GenerationController(2)
    user = User(id=123, is_bot=False, first_name="Teacher")
    chat = Chat(id=123, type="private")
    update_id = 1

    async def send_text(value: str) -> None:
        nonlocal update_id
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                message=Message(
                    message_id=update_id, date=datetime.now(UTC), chat=chat, from_user=user, text=value
                ),
            ),
        )
        update_id += 1

    async def click(value: str) -> None:
        nonlocal update_id
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=f"callback-{update_id}",
                    from_user=user,
                    chat_instance="test",
                    data=value,
                    message=Message(message_id=10, date=datetime.now(UTC), chat=chat, text="step"),
                ),
            ),
        )
        update_id += 1

    try:
        await send_text(texts.CREATE_WORK)
        await click("cw:g:3")
        await click("cw:s:2")
        await send_text("Умножение и деление")
        await click("cw:l:all")
        await click("cw:u:balanced")
        await click("cw:n:4")
        await click("cw:gen")
        documents = [method for method in bot.methods if isinstance(method, SendDocument)]
        assert len(documents) == 2
        assert documents[0].document.filename.startswith("Задания_")
        assert documents[1].document.filename.startswith("Методичка_")
    finally:
        await bot.session.close()
        await close_services(services)


async def test_blocking_guardrail_never_starts_generation(settings) -> None:
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=123, user_id=123))
    await state.update_data(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Умножение",
        level="all",
        uud_focus="balanced",
        task_count=4,
    )
    callback = MagicMock(spec=CallbackQuery)
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    generator = SimpleNamespace(
        check_request=AsyncMock(
            return_value=GuardrailResult(
                issues=[
                    GuardrailIssue(
                        code=GuardrailCode.PERSONAL_DATA,
                        severity=Severity.BLOCK,
                        message_ru="Уберите личные данные.",
                        suggestions=["Умножение"],
                    )
                ]
            )
        ),
        generate=AsyncMock(),
    )
    services = SimpleNamespace(generator=generator)
    session = SessionInfo(
        role=Role.TEACHER,
        nick="Учитель",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    await work_flow._check_request(
        callback, state, services, session, settings, work_flow.GenerationController(2)
    )
    assert await state.get_state() == work_flow.WorkFlow.blocked.state
    assert (await state.get_data())["blocked_step"] == "topic"
    callback.message.edit_text.assert_awaited_once()
    generator.generate.assert_not_awaited()


async def test_generation_controller_rejects_second_job() -> None:
    controller = work_flow.GenerationController(1)
    assert await controller.start("chat-key")
    assert not await controller.start("chat-key")
    await controller.finish("chat-key")
    assert await controller.start("chat-key")
