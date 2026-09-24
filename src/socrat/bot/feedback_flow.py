"""Feedback, task regeneration and anonymous response observation."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from aiogram.utils.chat_action import ChatActionSender

from socrat.config import Settings
from socrat.contracts import LABELS_RU, Feedback, Rating, Services, SessionInfo, UsageRecord
from socrat.storage.security import keyed_lookup, secret_bytes

from . import texts
from .work_flow import GenerationController, _keyboard, _show

logger = logging.getLogger(__name__)
router = Router(name="feedback_flow")


class FollowupFlow(StatesGroup):
    feedback_comment = State()
    regeneration_wish = State()
    response = State()
    reflection = State()


def _anonymous(value: str) -> bool:
    # Проверка «два слова с заглавной» убрана 24.09: срабатывала на «Законы Ньютона».
    # Педагога просят не вводить данные детей в подсказках бота; ответ ученика удаляется после разбора.
    return True


async def _work(services: Services, work_id: str):
    return await services.works.get(work_id)


def _task_rows(work, prefix: str) -> list[list[tuple[str, str]]]:
    return [
        [(f"{variant.student_label} · {task.number}", f"{prefix}:{work.work_id}:{task.task_id}")]
        for variant in work.variants
        for task in variant.tasks
    ]


async def _feedback_menu(callback: CallbackQuery, services: Services, work_id: str) -> None:
    work = await _work(services, work_id)
    if not isinstance(callback.message, Message):
        return
    if work is None:
        await callback.message.answer(texts.FOLLOWUP_NO_WORK)
        return
    for variant in work.variants:
        rows = [
            [
                (f"{task.task_id}: 👍", f"fb:{work_id}:{task.task_id}:up"),
                ("👎", f"fb:{work_id}:{task.task_id}:down"),
            ]
            for task in variant.tasks
        ]
        await callback.message.answer(variant.student_label, reply_markup=_keyboard(rows))


async def _save_feedback(
    target: Message | CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    comment: str | None,
) -> None:
    data = await state.get_data()
    work = await _work(services, data["work_id"])
    if work is None or work.find_task(data["task_id"]) is None:
        await _show(target, texts.FOLLOWUP_NO_WORK, [])
        await state.clear()
        return
    await services.feedback.add(
        Feedback(
            work_id=work.work_id,
            task_id=data["task_id"],
            rating=Rating.DOWN,
            comment=comment,
            teacher_id=session.teacher_id,
        )
    )
    await state.clear()
    await _show(target, texts.FOLLOWUP_THANKS, [])


async def _regenerate(
    target: Message | CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    controller: GenerationController,
    settings: Settings,
    wish: str | None,
) -> None:
    data = await state.get_data()
    work = await _work(services, data["work_id"])
    task = work.find_task(data["task_id"]) if work else None
    if work is None or task is None:
        await _show(target, texts.FOLLOWUP_NO_WORK, [])
        await state.clear()
        return
    message = target.message if isinstance(target, CallbackQuery) else target
    if not isinstance(message, Message):
        return
    chat_key = keyed_lookup(secret_bytes(settings), str(message.chat.id))
    if not await controller.start(chat_key):
        await message.answer(texts.WORK_IN_PROGRESS)
        return
    status = await message.answer(texts.FOLLOWUP_REGENERATING)
    last_update = 0.0

    async def progress(description: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.5:
            return
        last_update = now
        with suppress(TelegramBadRequest):
            await status.edit_text(texts.FOLLOWUP_PROGRESS.format(description=description[:3900]))

    try:
        async with controller.semaphore:
            async with ChatActionSender.upload_document(chat_id=message.chat.id, bot=message.bot):
                revised = await asyncio.wait_for(
                    services.generator.regenerate_task(work, task.level, task.number, wish, progress),
                    timeout=600,
                )
                await services.works.save(revised, session.teacher_id)
                student_name, teacher_name = services.exporter.filenames(revised)
                await message.answer_document(
                    BufferedInputFile(services.exporter.student_docx(revised), filename=student_name)
                )
                await message.answer_document(
                    BufferedInputFile(services.exporter.teacher_docx(revised), filename=teacher_name)
                )
        new_warnings = revised.warnings[len(work.warnings) :]
        result_text = (
            texts.FOLLOWUP_REGENERATED_WITH_WARNINGS.format(
                warnings="\n".join(issue.message_ru for issue in new_warnings)
            )
            if new_warnings
            else texts.FOLLOWUP_REGENERATED
        )
        await status.edit_text(result_text)
        await message.answer(
            texts.FOLLOWUP_NEXT_ACTION,
            reply_markup=_keyboard(
                [
                    [(texts.WORK_FEEDBACK, f"fbmenu:{revised.work_id}")],
                    [(texts.WORK_REGENERATE, f"regen:{revised.work_id}")],
                    [(texts.WORK_OBSERVE, f"observe:{revised.work_id}")],
                ]
            ),
        )
        await state.clear()
    except Exception:
        logger.exception("Task regeneration failed for work_id=%s task_id=%s", work.work_id, task.task_id)
        await status.edit_text(texts.FOLLOWUP_REGENERATE_FAILED)
    finally:
        await controller.finish(chat_key)


def _observation_lines(result) -> list[str]:
    lines = [
        result.summary_ru,
        f"Предметный результат: {LABELS_RU[result.subject_status.value]}. {result.subject_basis}",
    ]
    for item in result.uud:
        lines.append(f"{LABELS_RU[item.group.value]} УУД — {LABELS_RU[item.status.value]}. {item.basis}")
        lines.extend(f"Возможное иное объяснение: {alternative}" for alternative in item.alternatives)
    lines.extend(f"Ограничение: {limitation}" for limitation in result.limitations)
    return lines


async def _send_lines(message: Message, lines: list[str]) -> None:
    current = ""
    for line in lines:
        for part in (line[index : index + 1000] for index in range(0, len(line), 1000)):
            candidate = f"{current}\n{part}" if current else part
            if len(candidate) > 4000:
                await message.answer(current)
                current = part
            else:
                current = candidate
    if current:
        await message.answer(current)


async def _offer_observation_actions(message: Message, work_id: str) -> None:
    await message.answer(
        texts.FOLLOWUP_NEXT_ACTION,
        reply_markup=_keyboard(
            [
                [(texts.FOLLOWUP_OBSERVE_AGAIN, f"observe:{work_id}")],
                [(texts.FOLLOWUP_REGENERATE_AGAIN, f"regen:{work_id}")],
                [(texts.FOLLOWUP_FEEDBACK_ACTION, f"fbmenu:{work_id}")],
            ]
        ),
    )


@router.callback_query(F.data.startswith("fbmenu:"))
async def feedback_menu(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    await _feedback_menu(callback, services, (callback.data or "").split(":", 1)[1])


@router.callback_query(F.data.startswith("fb:"))
async def feedback_callback(
    callback: CallbackQuery, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    await callback.answer()
    _, work_id, task_id, rating = (callback.data or "").split(":", 3)
    work = await _work(services, work_id)
    if work is None or work.find_task(task_id) is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    if rating == Rating.UP:
        await services.feedback.add(
            Feedback(work_id=work_id, task_id=task_id, rating=Rating.UP, teacher_id=session.teacher_id)
        )
        await _show(callback, texts.FOLLOWUP_THANKS, [])
    elif rating == Rating.DOWN:
        await state.update_data(work_id=work_id, task_id=task_id)
        await state.set_state(FollowupFlow.feedback_comment)
        await _show(
            callback,
            texts.FOLLOWUP_COMMENT_PROMPT,
            [[(texts.FOLLOWUP_SKIP, "fbskip")]],
        )


@router.callback_query(F.data == "fbskip")
async def feedback_skip(
    callback: CallbackQuery, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    await callback.answer()
    if await state.get_state() == FollowupFlow.feedback_comment.state:
        await _save_feedback(callback, state, services, session, None)


@router.message(FollowupFlow.feedback_comment, F.text)
async def feedback_comment(
    message: Message, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    comment = (message.text or "").strip()
    if len(comment) > 1000:
        await message.answer(texts.FOLLOWUP_COMMENT_LONG)
    elif not _anonymous(comment):
        await message.answer(texts.FOLLOWUP_PERSONAL_DATA)
    else:
        await _save_feedback(message, state, services, session, comment or None)


@router.callback_query(F.data.startswith("regen:"))
async def regeneration_menu(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    work = await _work(services, (callback.data or "").split(":", 1)[1])
    if work is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    await _show(callback, texts.FOLLOWUP_CHOOSE_TASK, _task_rows(work, "regentask"))


@router.callback_query(F.data.startswith("regentask:"))
async def regeneration_task(callback: CallbackQuery, state: FSMContext, services: Services) -> None:
    await callback.answer()
    _, work_id, task_id = (callback.data or "").split(":", 2)
    work = await _work(services, work_id)
    if work is None or work.find_task(task_id) is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    await state.update_data(work_id=work_id, task_id=task_id)
    await state.set_state(FollowupFlow.regeneration_wish)
    await _show(callback, texts.FOLLOWUP_WISH_PROMPT, [[(texts.FOLLOWUP_SKIP, "regenskip")]])


@router.callback_query(F.data == "regenskip")
async def regeneration_skip(
    callback: CallbackQuery,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    generation_controller: GenerationController,
    settings: Settings,
) -> None:
    await callback.answer()
    if await state.get_state() == FollowupFlow.regeneration_wish.state:
        await _regenerate(callback, state, services, session, generation_controller, settings, None)


@router.message(FollowupFlow.regeneration_wish, F.text)
async def regeneration_wish(
    message: Message,
    state: FSMContext,
    services: Services,
    session: SessionInfo,
    generation_controller: GenerationController,
    settings: Settings,
) -> None:
    wish = (message.text or "").strip()
    if len(wish) > 500:
        await message.answer(texts.FOLLOWUP_WISH_LONG)
    elif not _anonymous(wish):
        await message.answer(texts.FOLLOWUP_PERSONAL_DATA)
    else:
        await _regenerate(message, state, services, session, generation_controller, settings, wish or None)


@router.callback_query(F.data.startswith("observe:"))
async def observation_menu(callback: CallbackQuery, services: Services) -> None:
    await callback.answer()
    work = await _work(services, (callback.data or "").split(":", 1)[1])
    if work is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    rows = _task_rows(work, "obstask")
    rows.extend(
        [
            [
                (
                    f"{variant.student_label} · рефлексия {index + 1}",
                    f"obsref:{work.work_id}:{variant_index}:{index}",
                )
            ]
            for variant_index, variant in enumerate(work.variants)
            for index, _ in enumerate(variant.reflection.questions)
        ]
    )
    await _show(callback, texts.FOLLOWUP_CHOOSE_OBSERVATION, rows)


@router.callback_query(F.data.startswith("obstask:"))
async def observation_task(callback: CallbackQuery, state: FSMContext, services: Services) -> None:
    await callback.answer()
    _, work_id, task_id = (callback.data or "").split(":", 2)
    work = await _work(services, work_id)
    if work is None or work.find_task(task_id) is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    await state.update_data(work_id=work_id, task_id=task_id)
    await state.set_state(FollowupFlow.response)
    await _show(callback, texts.FOLLOWUP_RESPONSE_PROMPT, [])


@router.callback_query(F.data.startswith("obsref:"))
async def observation_reflection(callback: CallbackQuery, state: FSMContext, services: Services) -> None:
    await callback.answer()
    _, work_id, variant_index, question_index = (callback.data or "").split(":", 3)
    work = await _work(services, work_id)
    if work is None:
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    try:
        question = work.variants[int(variant_index)].reflection.questions[int(question_index)]
    except (IndexError, ValueError):
        await _show(callback, texts.FOLLOWUP_NO_WORK, [])
        return
    await state.update_data(work_id=work_id, question=question)
    await state.set_state(FollowupFlow.reflection)
    await _show(callback, texts.FOLLOWUP_REFLECTION_PROMPT.format(question=question), [])


@router.message(FollowupFlow.response, F.text)
async def response_input(
    message: Message, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    answer = (message.text or "").strip()
    if not 1 <= len(answer) <= 3000:
        await message.answer(texts.FOLLOWUP_RESPONSE_LENGTH)
        return
    if not _anonymous(answer):
        await message.answer(texts.FOLLOWUP_PERSONAL_DATA)
        return
    data = await state.get_data()
    work = await _work(services, data["work_id"])
    task = work.find_task(data["task_id"]) if work else None
    if task is None:
        await state.clear()
        await message.answer(texts.FOLLOWUP_NO_WORK)
        return
    with suppress(Exception):
        await message.delete()
    try:
        result = services.observer.observe(task, answer)
        await services.usage.add(
            UsageRecord(kind="observation", model="deterministic", teacher_id=session.teacher_id)
        )
        await _send_lines(message, _observation_lines(result))
        await state.clear()
    except Exception:
        logger.exception("Response observation failed for task_id=%s", task.task_id)
        await message.answer(texts.FOLLOWUP_OBSERVATION_FAILED)
        return
    await _offer_observation_actions(message, work.work_id)


@router.message(FollowupFlow.reflection, F.text)
async def reflection_input(
    message: Message, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    answer = (message.text or "").strip()
    if not 1 <= len(answer) <= 3000:
        await message.answer(texts.FOLLOWUP_RESPONSE_LENGTH)
        return
    if not _anonymous(answer):
        await message.answer(texts.FOLLOWUP_PERSONAL_DATA)
        return
    data = await state.get_data()
    question = data.get("question", "")
    if not question:
        await state.clear()
        await message.answer(texts.FOLLOWUP_NO_WORK)
        return
    with suppress(Exception):
        await message.delete()
    try:
        result = services.observer.observe_reflection(question, answer)
        await services.usage.add(
            UsageRecord(kind="observation", model="deterministic", teacher_id=session.teacher_id)
        )
        await _send_lines(
            message,
            [result.neutral_note, result.suggested_teacher_action or ""],
        )
        await state.clear()
    except Exception:
        logger.exception("Reflection observation failed")
        await message.answer(texts.FOLLOWUP_OBSERVATION_FAILED)
        return
    if work_id := data.get("work_id"):
        await _offer_observation_actions(message, work_id)
