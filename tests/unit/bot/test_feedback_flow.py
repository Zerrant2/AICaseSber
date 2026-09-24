"""Follow-up actions exercise the real repositories with fake core services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Message

from socrat.app import build_services, close_services
from socrat.bot import feedback_flow, texts
from socrat.config import get_settings
from socrat.contracts import (
    DiagnosticWork,
    GenerationRequest,
    GuardrailCode,
    GuardrailIssue,
    LevelChoice,
    Rating,
    Role,
    SessionInfo,
    Severity,
)
from socrat.core import RuleResponseObserver, build_core
from socrat.testing.scripted import ScriptedLLM

FIXTURE = Path(__file__).resolve().parents[3] / "data/fixtures/work_math3_all.json"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET", "test-feedback-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("USE_FAKES", "true")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def state() -> FSMContext:
    return FSMContext(MemoryStorage(), StorageKey(bot_id=123, chat_id=123, user_id=123))


def session() -> SessionInfo:
    return SessionInfo(
        role=Role.TEACHER,
        teacher_id=7,
        nick="Педагог",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def callback(data: str, sent=None) -> MagicMock:
    item = MagicMock(spec=CallbackQuery)
    item.data = data
    item.answer = AsyncMock()
    item.message = MagicMock(spec=Message)
    item.message.chat = SimpleNamespace(id=123)
    item.message.bot = MagicMock()
    item.message.edit_text = AsyncMock()
    item.message.answer = AsyncMock(return_value=sent)
    item.message.answer_document = AsyncMock()
    return item


def fixture_work() -> DiagnosticWork:
    work = DiagnosticWork.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    work.work_id = "work-x7"
    return work


def assert_observation_actions(message: MagicMock, work_id: str) -> None:
    call = message.answer.await_args
    assert call.args == (texts.FOLLOWUP_NEXT_ACTION,)
    buttons = [button for row in call.kwargs["reply_markup"].inline_keyboard for button in row]
    assert [(button.text, button.callback_data) for button in buttons] == [
        (texts.FOLLOWUP_OBSERVE_AGAIN, f"observe:{work_id}"),
        (texts.FOLLOWUP_REGENERATE_AGAIN, f"regen:{work_id}"),
        (texts.FOLLOWUP_FEEDBACK_ACTION, f"fbmenu:{work_id}"),
    ]
    assert all(len(button.callback_data.encode()) <= 64 for button in buttons)


async def test_feedback_buttons_and_optional_down_comment(settings) -> None:
    services = await build_services(settings)
    work = fixture_work()
    await services.works.save(work, 7)
    flow_state = state()
    try:
        menu = callback(f"fbmenu:{work.work_id}")
        await feedback_flow.feedback_menu(menu, services)
        assert menu.message.answer.await_count == len(work.variants)
        for call in menu.message.answer.await_args_list:
            markup = call.kwargs["reply_markup"]
            assert all(
                len(button.callback_data.encode()) <= 64 for row in markup.inline_keyboard for button in row
            )

        task_id = work.variants[0].tasks[0].task_id
        await feedback_flow.feedback_callback(
            callback(f"fb:{work.work_id}:{task_id}:up"), flow_state, services, session()
        )
        assert (await services.feedback.stats())[Rating.UP] == 1

        await feedback_flow.feedback_callback(
            callback(f"fb:{work.work_id}:{task_id}:down"), flow_state, services, session()
        )
        message = MagicMock(spec=Message)
        message.text = "Условие двусмысленно"
        message.answer = AsyncMock()
        await feedback_flow.feedback_comment(message, flow_state, services, session())
        assert (await services.feedback.stats())[Rating.DOWN] == 1
        assert await flow_state.get_state() is None
    finally:
        await close_services(services)


async def test_response_and_reflection_are_observed_without_storing_text(settings) -> None:
    services = await build_services(settings)
    work = fixture_work()
    await services.works.save(work, 7)
    flow_state = state()
    try:
        task_id = work.variants[0].tasks[0].task_id
        await feedback_flow.observation_task(
            callback(f"obstask:{work.work_id}:{task_id}"), flow_state, services
        )
        answer = MagicMock(spec=Message)
        answer.text = "15"
        answer.delete = AsyncMock()
        answer.answer = AsyncMock()
        await feedback_flow.response_input(answer, flow_state, services, session())
        answer.delete.assert_awaited_once()
        rendered = "\n".join(call.args[0] for call in answer.answer.await_args_list)
        assert "Предметный результат:" in rendered
        assert "УУД" in rendered
        assert_observation_actions(answer, work.work_id)
        assert await flow_state.get_state() is None

        await feedback_flow.observation_reflection(
            callback(f"obsref:{work.work_id}:0:0"), flow_state, services
        )
        reflection = MagicMock(spec=Message)
        reflection.text = "Было трудно, но помогла схема"
        reflection.delete = AsyncMock()
        reflection.answer = AsyncMock()
        await feedback_flow.reflection_input(reflection, flow_state, services, session())
        reflection.delete.assert_awaited_once()
        reflection_text = "\n".join(call.args[0] for call in reflection.answer.await_args_list)
        assert "Это сведения для беседы" in reflection_text
        assert_observation_actions(reflection, work.work_id)
        assert (await services.usage.summary(30)).by_kind["observation"] == 2
    finally:
        await close_services(services)


def test_teacher_question_is_directed_without_uud_lines() -> None:
    task = fixture_work().find_task("A-1")
    result = RuleResponseObserver().observe(task, "Ученик не справился с умножением, как ему помочь")

    assert result.uud == []
    lines = feedback_flow._observation_lines(result)
    assert "Анализ типичной ошибки" in lines[0]
    assert not any("УУД" in line for line in lines)


async def test_regeneration_sends_revised_documents(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    services = await build_services(settings)
    work = fixture_work()
    await services.works.save(work, 7)
    flow_state = state()

    @asynccontextmanager
    async def no_chat_action(**_kwargs):
        yield

    monkeypatch.setattr(feedback_flow.ChatActionSender, "upload_document", no_chat_action)
    try:
        task_id = work.variants[0].tasks[0].task_id
        await feedback_flow.regeneration_task(
            callback(f"regentask:{work.work_id}:{task_id}"), flow_state, services
        )
        status = MagicMock(spec=Message)
        status.edit_text = AsyncMock()
        item = callback("regenskip", status)
        controller = feedback_flow.GenerationController(1)
        await feedback_flow.regeneration_skip(item, flow_state, services, session(), controller, settings)
        assert item.message.answer_document.await_count == 2
        status.edit_text.assert_awaited_with(texts.FOLLOWUP_REGENERATED)
        assert await flow_state.get_state() is None
        assert await services.works.get(work.work_id) is not None
    finally:
        await close_services(services)


async def test_regeneration_shows_only_new_warnings(settings, monkeypatch: pytest.MonkeyPatch) -> None:
    services = await build_services(settings)
    generator, _, _ = build_core(settings, services.knowledge, llm=ScriptedLLM())
    services.generator = generator
    work = await generator.generate(
        GenerationRequest(
            grade=3,
            subject_id="math",
            subject_name="Математика",
            topic="Внетабличное умножение и деление",
            level=LevelChoice.BASIC,
            task_count=4,
        )
    )
    work.warnings.append(
        GuardrailIssue(code=GuardrailCode.OK, severity=Severity.WARN, message_ru="Старое замечание")
    )
    await services.works.save(work, 7)
    flow_state = state()

    @asynccontextmanager
    async def no_chat_action(**_kwargs):
        yield

    monkeypatch.setattr(feedback_flow.ChatActionSender, "upload_document", no_chat_action)
    try:
        task_id = work.variants[0].tasks[0].task_id
        await feedback_flow.regeneration_task(
            callback(f"regentask:{work.work_id}:{task_id}"), flow_state, services
        )
        status = MagicMock(spec=Message)
        status.edit_text = AsyncMock()
        item = MagicMock(spec=Message)
        item.text = "Замени на вычитание и сложение"
        item.chat = SimpleNamespace(id=123)
        item.bot = MagicMock()
        item.answer = AsyncMock(return_value=status)
        item.answer_document = AsyncMock()
        controller = feedback_flow.GenerationController(1)

        await feedback_flow.regeneration_wish(item, flow_state, services, session(), controller, settings)

        assert item.answer_document.await_count == 2
        final_message = status.edit_text.await_args.args[0]
        assert "Задание заменено, но есть замечание:" in final_message
        assert "выполнить не удалось" in final_message
        assert "Старое замечание" not in final_message
        assert "просмотрите задание перед использованием" in final_message
        assert await flow_state.get_state() is None
    finally:
        await close_services(services)
