"""FSM for analyzing a typical, anonymized classroom error."""

from __future__ import annotations

import html
import logging
import time
from contextlib import suppress

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from socrat.contracts import (
    ERROR_LAYER_LABELS,
    LABELS_RU,
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    GuardrailCode,
    Role,
    Services,
    SessionInfo,
    UsageRecord,
)

from . import texts
from .work_flow import _keyboard, _show

logger = logging.getLogger(__name__)
router = Router(name="analysis_flow")


class ErrorFlow(StatesGroup):
    grade = State()
    subject = State()
    topic = State()
    topic_choice = State()
    description = State()
    warning = State()
    result = State()


async def show_grade(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ErrorFlow.grade)
    rows = [
        [(str(grade), f"ea:g:{grade}") for grade in range(first, min(first + 4, 12))] for first in (1, 5, 9)
    ]
    rows.append([(texts.ERROR_BACK, "ea:back")])
    await _show(target, texts.ERROR_GRADE, rows)


async def show_subject(target: Message | CallbackQuery, state: FSMContext, services: Services) -> None:
    grade = (await state.get_data())["grade"]
    subjects = services.knowledge.list_subjects(grade)
    await state.update_data(subjects=[(subject.subject_id, subject.name) for subject in subjects])
    await state.set_state(ErrorFlow.subject)
    if not subjects:
        await _show(target, texts.WORK_NO_SUBJECTS, [[(texts.ERROR_BACK, "ea:back")]])
        return
    rows = [[(subject.name, f"ea:s:{index}")] for index, subject in enumerate(subjects)]
    rows.append([(texts.ERROR_BACK, "ea:back")])
    await _show(target, texts.ERROR_SUBJECT, rows)


async def show_topic(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ErrorFlow.topic)
    await _show(target, texts.ERROR_TOPIC, [[(texts.ERROR_BACK, "ea:back")]])


async def show_description(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ErrorFlow.description)
    await _show(target, texts.ERROR_DESCRIPTION, [[(texts.ERROR_BACK, "ea:back")]])


def _request(data: dict, description: str, role: Role) -> ErrorAnalysisRequest:
    return ErrorAnalysisRequest(
        grade=data["grade"],
        subject_id=data["subject_id"],
        subject_name=data["subject_name"],
        topic=data["topic"],
        description=description,
        author_role=role,
    )


def _escaped_parts(value: str) -> list[str]:
    return [html.escape(value[index : index + 600]) for index in range(0, len(value), 600)] or [""]


def format_analysis(result: ErrorAnalysisResult) -> list[str]:
    parts = ["<b>🔍 Вероятная картина</b>", *_escaped_parts(result.summary), "<b>Гипотезы</b>"]
    confidence = {"low": "низкая", "medium": "средняя", "high": "высокая"}
    for index, hypothesis in enumerate(result.hypotheses, start=1):
        layer = ERROR_LAYER_LABELS[hypothesis.layer]
        group = f" · {LABELS_RU[hypothesis.uud_group.value]} УУД" if hypothesis.uud_group else ""
        parts.append(f"{index}) [{html.escape(layer + group)}]")
        parts.extend(_escaped_parts(hypothesis.statement))
        parts.append(f"Уверенность: {confidence[hypothesis.confidence]}")
        for sign in hypothesis.signs_to_check:
            parts.append("Проверить:")
            parts.extend(_escaped_parts(sign))
    parts.append("<b>Приёмы на следующий урок</b>")
    for technique in result.techniques:
        parts.extend(_escaped_parts(f"• {technique.name} — {technique.how_to_apply}"))
    if result.quick_checks:
        parts.append("<b>Мини-проверка</b>")
        for task in result.quick_checks:
            parts.extend(_escaped_parts(f"• {task.student_text} Что проверяет: {task.what_it_checks}"))
    if result.teacher_reflection_question:
        parts.append("<b>Вопрос для вас</b>")
        parts.extend(_escaped_parts(result.teacher_reflection_question))
    parts.append("<b>⚠️ Ограничения</b>")
    for limitation in result.limitations:
        parts.extend(_escaped_parts(f"• {limitation}"))

    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}\n{part}" if current else part
        if len(candidate) > 4000 and current:
            chunks.append(current)
            current = part
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def _analyze(message: Message, state: FSMContext, services: Services, session: SessionInfo) -> None:
    data = await state.get_data()
    request = _request(data, data["description"], session.role)
    status = await message.answer(texts.ERROR_WORKING)
    last_update = 0.0

    async def progress(description: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.5:
            return
        last_update = now
        with suppress(TelegramBadRequest):
            await status.edit_text(f"⏳ {description}")

    try:
        async with ChatActionSender.typing(chat_id=message.chat.id, bot=message.bot):
            result = await services.analyzer.analyze(request, progress)
        await services.usage.add(
            UsageRecord(
                kind="analysis",
                model=result.meta.model,
                tokens_in=result.meta.tokens_in,
                tokens_out=result.meta.tokens_out,
                cost_usd=result.meta.cost_usd,
                teacher_id=session.teacher_id,
            )
        )
        await state.update_data(result_json=result.model_dump_json(), analysis_id=result.analysis_id)
        await state.set_state(ErrorFlow.result)
        await status.edit_text(texts.ERROR_DONE)
        chunks = format_analysis(result)
        for index, chunk in enumerate(chunks):
            markup = (
                _keyboard([[(texts.ERROR_DOCX, f"ea:docx:{result.analysis_id}")]])
                if index == len(chunks) - 1
                else None
            )
            await message.answer(chunk, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        logger.exception("Error analysis failed for grade=%s subject=%s", request.grade, request.subject_id)
        with suppress(Exception):
            await status.edit_text(texts.ERROR_FAILED)
        await state.set_state(ErrorFlow.description)


@router.message(F.text == texts.ANALYZE_ERROR)
async def start_analysis(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_grade(message, state)


@router.message(ErrorFlow.topic, F.text)
async def topic_input(message: Message, state: FSMContext, services: Services) -> None:
    topic = " ".join((message.text or "").split())
    if not 2 <= len(topic) <= 500:
        await message.answer(texts.ERROR_TOPIC)
        return
    data = await state.get_data()
    await state.update_data(topic=topic)
    check = services.knowledge.check_topic(data["grade"], data["subject_id"], topic)
    if check.in_program:
        await show_description(message, state)
        return
    suggestions = check.suggestions[:5]
    await state.update_data(topic_suggestions=suggestions)
    await state.set_state(ErrorFlow.topic_choice)
    rows = [[(suggestion[:45], f"ea:ts:{index}")] for index, suggestion in enumerate(suggestions)]
    rows.extend([[(texts.ERROR_CONTINUE_TOPIC, "ea:keep")], [(texts.ERROR_BACK, "ea:back")]])
    await _show(message, texts.ERROR_TOPIC_SUGGESTIONS, rows)


@router.message(ErrorFlow.description, F.text)
async def description_input(
    message: Message, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    description = " ".join((message.text or "").split())
    if not 10 <= len(description) <= 3000:
        await message.answer(texts.ERROR_DESCRIPTION_SHORT)
        return
    data = await state.get_data()
    request = _request(data, description, session.role)
    guard = await services.analyzer.check(request)
    if any(issue.code == GuardrailCode.PERSONAL_DATA for issue in guard.issues):
        await message.answer(texts.ERROR_PERSONAL_DATA)
        return
    if guard.blocked:
        await message.answer("\n".join(issue.message_ru for issue in guard.issues))
        return
    await state.update_data(description=description)
    if guard.has_warnings:
        await state.set_state(ErrorFlow.warning)
        await _show(
            message,
            "\n".join(issue.message_ru for issue in guard.issues),
            [[(texts.ERROR_CONTINUE, "ea:continue"), (texts.ERROR_EDIT, "ea:edit")]],
        )
        return
    await _analyze(message, state, services, session)


@router.callback_query(F.data.startswith("ea:"))
async def error_callback(
    callback: CallbackQuery, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    await callback.answer()
    action = (callback.data or "").split(":")
    data = await state.get_data()
    current = await state.get_state()
    if action[1] == "back":
        previous = {
            ErrorFlow.grade.state: "cancel",
            ErrorFlow.subject.state: "grade",
            ErrorFlow.topic.state: "subject",
            ErrorFlow.topic_choice.state: "topic",
            ErrorFlow.description.state: "topic",
            ErrorFlow.warning.state: "description",
        }.get(current, "grade")
        if previous == "cancel":
            await state.clear()
            await _show(callback, texts.ERROR_CANCELLED, [])
        elif previous == "grade":
            await show_grade(callback, state)
        elif previous == "subject":
            await show_subject(callback, state, services)
        elif previous == "topic":
            await show_topic(callback, state)
        else:
            await show_description(callback, state)
    elif action[1] == "g":
        await state.update_data(grade=int(action[2]))
        await show_subject(callback, state, services)
    elif action[1] == "s":
        subject_id, subject_name = data["subjects"][int(action[2])]
        await state.update_data(subject_id=subject_id, subject_name=subject_name)
        await show_topic(callback, state)
    elif action[1] == "ts":
        await state.update_data(topic=data["topic_suggestions"][int(action[2])])
        await show_description(callback, state)
    elif action[1] == "keep":
        await show_description(callback, state)
    elif action[1] == "edit":
        await show_description(callback, state)
    elif action[1] == "continue" and isinstance(callback.message, Message):
        await _analyze(callback.message, state, services, session)
    elif action[1] == "docx" and isinstance(callback.message, Message):
        if data.get("analysis_id") != action[2] or not data.get("result_json"):
            await callback.message.answer(texts.ERROR_NO_RESULT)
            return
        result = ErrorAnalysisResult.model_validate_json(data["result_json"])
        await callback.message.answer_document(
            BufferedInputFile(
                services.exporter.analysis_docx(result),
                filename=f"Анализ_ошибки_{result.request.grade}кл.docx",
            )
        )
