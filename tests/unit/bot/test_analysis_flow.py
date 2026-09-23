"""Typical-error analysis in fixture mode, with no Telegram network calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from socrat.app import build_services, close_services
from socrat.bot import analysis_flow, texts
from socrat.bot.middleware import AuthMiddleware
from socrat.config import get_settings
from socrat.contracts import (
    ErrorAnalysisResult,
    GuardrailCode,
    GuardrailIssue,
    GuardrailResult,
    Role,
    SessionInfo,
    Severity,
)

ROOT = Path(__file__).resolve().parents[3]


class RecordingBot(Bot):
    def __init__(self) -> None:
        super().__init__(token="123:test")
        self.methods: list[object] = []
        self.next_message_id = 20

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
    monkeypatch.setenv("APP_SECRET", "test-analysis-flow-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("USE_FAKES", "true")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_format_analysis_escapes_html_and_splits_long_text() -> None:
    result = ErrorAnalysisResult.model_validate_json(
        (ROOT / "data" / "fixtures" / "error_analysis_math3.json").read_text(encoding="utf-8")
    )
    result.summary = "<script>" + "&" * 2000
    chunks = analysis_flow.format_analysis(result)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "<script>" not in "".join(chunks)
    assert "&lt;script&gt;" in "".join(chunks)


async def test_personal_data_is_rejected_before_state_save(settings) -> None:
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=123, user_id=123))
    await state.update_data(grade=3, subject_id="math", subject_name="Математика", topic="Умножение")
    await state.set_state(analysis_flow.ErrorFlow.description)
    message = MagicMock(spec=Message)
    message.text = "Иван Иванов сделал ошибку в задаче"
    message.answer = AsyncMock()
    analyzer = SimpleNamespace(
        check=AsyncMock(
            return_value=GuardrailResult(
                issues=[
                    GuardrailIssue(
                        code=GuardrailCode.PERSONAL_DATA,
                        severity=Severity.WARN,
                        message_ru="Уберите имя.",
                    )
                ]
            )
        ),
        analyze=AsyncMock(),
    )
    services = SimpleNamespace(analyzer=analyzer)
    session = SessionInfo(
        role=Role.TEACHER, nick="Учитель", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    await analysis_flow.description_input(message, state, services, session)
    assert "description" not in await state.get_data()
    analyzer.analyze.assert_not_awaited()
    message.answer.assert_awaited_once_with(texts.ERROR_PERSONAL_DATA)


async def test_analysis_flow_sends_html_and_docx(settings) -> None:
    services = await build_services(settings)
    bot = RecordingBot()
    session = SessionInfo(
        role=Role.TEACHER, nick="Учитель", expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    session_store = SimpleNamespace(get=AsyncMock(return_value=session))
    dispatcher = Dispatcher()
    dispatcher.message.outer_middleware(AuthMiddleware(session_store))
    dispatcher.callback_query.outer_middleware(AuthMiddleware(session_store))
    dispatcher.include_router(analysis_flow.router)
    dispatcher["services"] = services
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
                    message=Message(message_id=20, date=datetime.now(UTC), chat=chat, text="step"),
                ),
            ),
        )
        update_id += 1

    try:
        await send_text(texts.ANALYZE_ERROR)
        await click("ea:g:3")
        await click("ea:s:2")
        await send_text("Умножение и деление")
        await send_text("Ученики путают умножение и сложение при решении задач")
        html_messages = [
            method
            for method in bot.methods
            if isinstance(method, SendMessage) and method.parse_mode == ParseMode.HTML
        ]
        assert html_messages
        assert all(len(method.text) <= 4000 for method in html_messages)
        assert (await services.usage.summary(1)).by_kind["analysis"] == 1
        state = dispatcher.fsm.get_context(bot=bot, chat_id=123, user_id=123)
        analysis_id = (await state.get_data())["analysis_id"]
        await click(f"ea:docx:{analysis_id}")
        documents = [method for method in bot.methods if isinstance(method, SendDocument)]
        assert len(documents) == 1
        assert documents[0].document.filename == "Анализ_ошибки_3кл.docx"
    finally:
        await bot.session.close()
        await close_services(services)
