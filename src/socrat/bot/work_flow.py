"""FSM for collecting a diagnostic work request and delivering both files."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.chat_action import ChatActionSender

from socrat.config import Settings
from socrat.contracts import (
    LABELS_RU,
    GenerationError,
    GenerationRequest,
    GuardrailCode,
    LevelChoice,
    Services,
    SessionInfo,
    UsageRecord,
    UUDFocus,
)
from socrat.core.specs import estimate_work_minutes
from socrat.storage.security import keyed_lookup, secret_bytes

from . import texts

logger = logging.getLogger(__name__)
router = Router(name="work_flow")
GENERATION_TIMEOUT_SECONDS = 600


class WorkFlow(StatesGroup):
    grade = State()
    subject = State()
    topic = State()
    topic_choice = State()
    level = State()
    uud = State()
    count = State()
    confirm = State()
    warning = State()
    blocked = State()
    generating = State()


class GenerationController:
    def __init__(self, max_concurrent: int) -> None:
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._active: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(self, chat_key: str) -> bool:
        async with self._lock:
            if chat_key in self._active:
                return False
            self._active.add(chat_key)
            return True

    async def finish(self, chat_key: str) -> None:
        async with self._lock:
            self._active.discard(chat_key)


def _keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows
        ]
    )


async def _show(target: Message | CallbackQuery, text: str, rows: list[list[tuple[str, str]]]) -> None:
    markup = _keyboard(rows)
    if isinstance(target, CallbackQuery) and isinstance(target.message, Message):
        await target.message.edit_text(text, reply_markup=markup)
    elif isinstance(target, Message):
        await target.answer(text, reply_markup=markup)


async def show_grade(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkFlow.grade)
    rows = [
        [(str(grade), f"cw:g:{grade}") for grade in range(first, min(first + 4, 12))] for first in (1, 5, 9)
    ]
    rows.append([(texts.WORK_BACK, "cw:back")])
    await _show(target, texts.WORK_GRADE, rows)


async def show_subject(target: Message | CallbackQuery, state: FSMContext, services: Services) -> None:
    data = await state.get_data()
    subjects = services.knowledge.list_subjects(data["grade"])
    if not subjects:
        await _show(target, texts.WORK_NO_SUBJECTS, [[(texts.WORK_BACK, "cw:back")]])
        await state.set_state(WorkFlow.subject)
        return
    await state.update_data(subjects=[(subject.subject_id, subject.name) for subject in subjects])
    await state.set_state(WorkFlow.subject)
    rows = [[(subject.name, f"cw:s:{index}")] for index, subject in enumerate(subjects)]
    rows.append([(texts.WORK_BACK, "cw:back")])
    await _show(target, texts.WORK_SUBJECT, rows)


async def show_topic(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkFlow.topic)
    await _show(target, texts.WORK_TOPIC, [[(texts.WORK_BACK, "cw:back")]])


async def show_level(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkFlow.level)
    rows = [
        [(LABELS_RU[level.value], f"cw:l:{level.value}")]
        for level in (LevelChoice.EASY, LevelChoice.BASIC, LevelChoice.ADVANCED, LevelChoice.ALL)
    ]
    rows.append([(texts.WORK_BACK, "cw:back")])
    await _show(target, texts.WORK_LEVEL, rows)


async def show_uud(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.set_state(WorkFlow.uud)
    rows = [
        [(LABELS_RU[focus.value], f"cw:u:{focus.value}")]
        for focus in (UUDFocus.COGNITIVE, UUDFocus.REGULATORY, UUDFocus.COMMUNICATIVE, UUDFocus.BALANCED)
    ]
    rows.append([(texts.WORK_BACK, "cw:back")])
    await _show(target, texts.WORK_UUD, rows)


async def show_count(target: Message | CallbackQuery, state: FSMContext, settings: Settings) -> None:
    data = await state.get_data()
    levels = LevelChoice(data["level"]).levels()
    focus = UUDFocus(data["uud_focus"])

    def label(count: int) -> str:
        minutes = max(
            estimate_work_minutes(level, count, focus, data["subject_id"], data["grade"]) for level in levels
        )
        return f"{count} · {minutes:g} мин"

    await state.set_state(WorkFlow.count)
    maximum = min(8, settings.max_tasks)
    rows = [
        [(label(count), f"cw:n:{count}") for count in range(first, min(first + 2, maximum + 1))]
        for first in range(1, maximum + 1, 2)
    ]
    rows.append([(texts.WORK_BACK, "cw:back")])
    await _show(target, texts.WORK_COUNT, rows)


async def show_confirm(target: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(WorkFlow.confirm)
    summary = texts.WORK_CONFIRM.format(
        grade=data["grade"],
        subject=data["subject_name"],
        topic=data["topic"],
        level=LABELS_RU[data["level"]],
        uud=LABELS_RU[data["uud_focus"]],
        count=data["task_count"],
    )
    await _show(
        target,
        summary,
        [[(texts.WORK_GENERATE, "cw:gen"), (texts.WORK_EDIT, "cw:edit")], [(texts.WORK_BACK, "cw:back")]],
    )


def _request(data: dict, session: SessionInfo, settings: Settings) -> GenerationRequest:
    return GenerationRequest(
        grade=data["grade"],
        subject_id=data["subject_id"],
        subject_name=data["subject_name"],
        topic=data["topic"],
        level=LevelChoice(data["level"]),
        uud_focus=UUDFocus(data["uud_focus"]),
        task_count=data["task_count"],
        minutes_min=settings.work_minutes_min,
        minutes_max=settings.work_minutes_max,
        author_role=session.role,
    )


def _blocked_step(code: GuardrailCode) -> str:
    if code == GuardrailCode.GRADE_OUT_OF_RANGE:
        return "grade"
    if code in (GuardrailCode.SUBJECT_UNKNOWN, GuardrailCode.KNOWLEDGE_EMPTY):
        return "subject"
    if code in (GuardrailCode.TIME_OUT_OF_RANGE, GuardrailCode.TOO_MANY_TASKS):
        return "count"
    return "topic"


async def _show_step(
    step: str, target: Message | CallbackQuery, state: FSMContext, services: Services, settings: Settings
) -> None:
    if step == "grade":
        await show_grade(target, state)
    elif step == "subject":
        await show_subject(target, state, services)
    elif step == "topic":
        await show_topic(target, state)
    elif step == "level":
        await show_level(target, state)
    elif step == "uud":
        await show_uud(target, state)
    elif step == "count":
        await show_count(target, state, settings)
    else:
        await show_confirm(target, state)


async def _check_request(
    callback: CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    settings: Settings,
    controller: GenerationController,
) -> None:
    data = await state.get_data()
    request = _request(data, session, settings)
    result = await services.generator.check_request(request)
    if result.blocked:
        issue = next(item for item in result.issues if item.severity.value == "block")
        suggestions = issue.suggestions[:5]
        await state.update_data(blocked_step=_blocked_step(issue.code), blocked_suggestions=suggestions)
        await state.set_state(WorkFlow.blocked)
        rows = [[(suggestion[:45], f"cw:sug:{index}")] for index, suggestion in enumerate(suggestions)]
        rows.append([(texts.WORK_EDIT, "cw:edit")])
        await _show(callback, "\n".join(item.message_ru for item in result.issues), rows)
    elif result.has_warnings:
        await state.set_state(WorkFlow.warning)
        await _show(
            callback,
            "\n".join(item.message_ru for item in result.issues),
            [[(texts.WORK_CONTINUE, "cw:continue"), (texts.WORK_EDIT, "cw:edit")]],
        )
    else:
        await _generate(callback, state, services, session, settings, controller)


async def _generate(
    callback: CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    settings: Settings,
    controller: GenerationController,
) -> None:
    if not isinstance(callback.message, Message):
        return
    chat_id = callback.message.chat.id
    chat_key = keyed_lookup(secret_bytes(settings), str(chat_id))
    if not await controller.start(chat_key):
        await callback.message.answer(texts.WORK_IN_PROGRESS)
        return
    request = _request(await state.get_data(), session, settings)
    await state.set_state(WorkFlow.generating)
    status = await callback.message.answer(texts.WORK_PREPARING)
    last_update = 0.0

    async def progress(description: str) -> None:
        nonlocal last_update
        current = time.monotonic()
        if current - last_update < 1.5:
            return
        last_update = current
        with suppress(TelegramBadRequest):
            await status.edit_text(f"⏳ {description}")

    try:
        async with controller.semaphore:
            async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
                async with ChatActionSender.upload_document(chat_id=chat_id, bot=callback.bot):
                    work = await services.generator.generate(request, progress)
                    await services.works.save(work, session.teacher_id)
                    await services.usage.add(
                        UsageRecord(
                            kind="generation",
                            model=work.meta.model,
                            tokens_in=work.meta.tokens_in,
                            tokens_out=work.meta.tokens_out,
                            cost_usd=work.meta.cost_usd,
                            teacher_id=session.teacher_id,
                        )
                    )
                    student_name, teacher_name = services.exporter.filenames(work)
                    await callback.message.answer_document(
                        BufferedInputFile(services.exporter.student_docx(work), filename=student_name)
                    )
                    await callback.message.answer_document(
                        BufferedInputFile(services.exporter.teacher_docx(work), filename=teacher_name)
                    )
        times = "\n".join(
            f"{variant.student_label}: {variant.time_plan.total_minutes:g} мин" for variant in work.variants
        )
        groups = ", ".join(LABELS_RU[group.value] for group in work.coverage.uud_groups)
        warnings = (
            texts.WORK_WARNINGS.format(items="\n".join(issue.message_ru for issue in work.warnings))
            if work.warnings
            else ""
        )
        await status.edit_text(texts.WORK_RESULT.format(times=times, groups=groups, warnings=warnings))
        await callback.message.answer(
            texts.WORK_FEEDBACK,
            reply_markup=_keyboard(
                [
                    [(texts.WORK_FEEDBACK, f"fbmenu:{work.work_id}")],
                    [(texts.WORK_REGENERATE, f"regen:{work.work_id}")],
                    [(texts.WORK_OBSERVE, f"observe:{work.work_id}")],
                ]
            ),
        )
        await state.clear()
    except TimeoutError:
        logger.warning("Generation timed out for grade=%s subject=%s", request.grade, request.subject_id)
        await status.edit_text(
            texts.WORK_TIMED_OUT, reply_markup=_keyboard([[(texts.WORK_RETRY, "cw:retry")]])
        )
        await state.set_state(WorkFlow.confirm)
    except GenerationError as error:
        logger.warning("Generation failed for grade=%s subject=%s", request.grade, request.subject_id)
        await status.edit_text(error.message_ru, reply_markup=_keyboard([[(texts.WORK_RETRY, "cw:retry")]]))
        await state.set_state(WorkFlow.confirm)
    except Exception:
        logger.exception("Generation failed for grade=%s subject=%s", request.grade, request.subject_id)
        await status.edit_text(texts.WORK_FAILED, reply_markup=_keyboard([[(texts.WORK_RETRY, "cw:retry")]]))
        await state.set_state(WorkFlow.confirm)
    finally:
        await controller.finish(chat_key)


@router.message(F.text == texts.CREATE_WORK)
async def start_work(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_grade(message, state)


@router.message(WorkFlow.topic, F.text)
async def topic_input(message: Message, state: FSMContext, services: Services) -> None:
    topic = " ".join((message.text or "").split())
    if not 2 <= len(topic) <= 500:
        await message.answer(texts.WORK_TOPIC)
        return
    data = await state.get_data()
    await state.update_data(topic=topic)
    check = services.knowledge.check_topic(data["grade"], data["subject_id"], topic)
    if not check.in_program:
        suggestions = check.suggestions[:5]
        await state.update_data(topic_suggestions=suggestions)
        await state.set_state(WorkFlow.topic_choice)
        rows = [[(suggestion[:45], f"cw:ts:{index}")] for index, suggestion in enumerate(suggestions)]
        rows.extend([[(texts.WORK_KEEP_TOPIC, "cw:keep")], [(texts.WORK_BACK, "cw:back")]])
        await _show(message, texts.WORK_TOPIC_NOT_FOUND.format(grade=data["grade"]), rows)
    else:
        await show_level(message, state)


@router.callback_query(F.data.startswith("cw:"))
async def work_callback(
    callback: CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    settings: Settings,
    generation_controller: GenerationController,
) -> None:
    await callback.answer()
    action = (callback.data or "").split(":")
    data = await state.get_data()
    current = await state.get_state()
    if action[1] == "back":
        previous = {
            WorkFlow.grade.state: "cancel",
            WorkFlow.subject.state: "grade",
            WorkFlow.topic.state: "subject",
            WorkFlow.topic_choice.state: "topic",
            WorkFlow.level.state: "topic",
            WorkFlow.uud.state: "level",
            WorkFlow.count.state: "uud",
            WorkFlow.confirm.state: "count",
            WorkFlow.warning.state: "confirm",
            WorkFlow.blocked.state: data.get("blocked_step", "confirm"),
        }.get(current, "confirm")
        if previous == "cancel":
            await state.clear()
            await _show(callback, texts.WORK_CANCELLED, [])
        else:
            await _show_step(previous, callback, state, services, settings)
    elif action[1] == "g":
        await state.update_data(grade=int(action[2]))
        await show_subject(callback, state, services)
    elif action[1] == "s":
        subject_id, subject_name = data["subjects"][int(action[2])]
        await state.update_data(subject_id=subject_id, subject_name=subject_name)
        await show_topic(callback, state)
    elif action[1] == "ts":
        await state.update_data(topic=data["topic_suggestions"][int(action[2])])
        await show_level(callback, state)
    elif action[1] == "keep":
        await show_level(callback, state)
    elif action[1] == "l":
        await state.update_data(level=LevelChoice(action[2]).value)
        await show_uud(callback, state)
    elif action[1] == "u":
        await state.update_data(uud_focus=UUDFocus(action[2]).value)
        await show_count(callback, state, settings)
    elif action[1] == "n":
        await state.update_data(task_count=int(action[2]))
        await show_confirm(callback, state)
    elif action[1] in ("gen", "retry"):
        await _check_request(callback, state, services, session, settings, generation_controller)
    elif action[1] == "continue":
        await _generate(callback, state, services, session, settings, generation_controller)
    elif action[1] == "edit":
        step = data.get("blocked_step", "grade") if current == WorkFlow.blocked.state else "grade"
        await _show_step(step, callback, state, services, settings)
    elif action[1] == "sug":
        suggestion = data["blocked_suggestions"][int(action[2])]
        step = data.get("blocked_step", "topic")
        if step == "count" and suggestion.isdigit():
            await state.update_data(task_count=int(suggestion))
            await show_confirm(callback, state)
        elif step == "topic":
            await state.update_data(topic=suggestion)
            await show_confirm(callback, state)
        else:
            await _show_step(step, callback, state, services, settings)
