"""Administrator tools for teacher accounts, knowledge and usage."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import IntegrityError

from socrat.contracts import LABELS_RU, NewTeacherCredentials, Role, Services, SessionInfo

from . import texts
from .work_flow import _show

logger = logging.getLogger(__name__)
router = Router(name="admin_panel")
MAX_SCHOOL_FILE_BYTES = 20 * 1024 * 1024
PASSWORD_MESSAGE_TTL_SECONDS = 300
_pending_deletions: set[asyncio.Task[None]] = set()


class AdminFlow(StatesGroup):
    nick = State()
    role = State()
    upload = State()


def _menu_rows() -> list[list[tuple[str, str]]]:
    return [
        [(texts.ADMIN_ADD, "adm:add")],
        [(texts.ADMIN_TEACHERS, "adm:list:0")],
        [(texts.ADMIN_KNOWLEDGE, "adm:knowledge")],
        [(texts.ADMIN_USAGE, "adm:usage")],
        [(texts.ADMIN_PAYMENT, "adm:payment")],
    ]


async def _menu(target: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(target, texts.ADMIN_TITLE, _menu_rows())


async def _delete_password_later(bot, chat_id: int, message_id: int, delay: int = 300) -> None:
    await asyncio.sleep(delay)
    with suppress(Exception):
        await bot.delete_message(chat_id, message_id)


async def _show_credentials(message: Message, credentials: NewTeacherCredentials, *, reset: bool) -> None:
    template = texts.ADMIN_PASSWORD_RESET if reset else texts.ADMIN_CREATED
    sent = await message.answer(
        template.format(nick=credentials.account.nick, password=credentials.plain_password)
    )
    task = asyncio.create_task(
        _delete_password_later(sent.bot, sent.chat.id, sent.message_id, PASSWORD_MESSAGE_TTL_SECONDS)
    )
    _pending_deletions.add(task)
    task.add_done_callback(_pending_deletions.discard)


async def _teachers_page(target: Message | CallbackQuery, services: Services, page: int) -> None:
    teachers = await services.teachers.list(include_inactive=True)
    if not teachers:
        await _show(target, texts.ADMIN_NO_TEACHERS, [[(texts.ADMIN_BACK, "adm:menu")]])
        return
    page_size = 10
    page = max(0, min(page, (len(teachers) - 1) // page_size))
    selected = teachers[page * page_size : (page + 1) * page_size]
    rows = [
        [
            (
                f"{teacher.nick[:32]} · {LABELS_RU[teacher.role.value]} · "
                f"{texts.ADMIN_ACTIVE if teacher.active else texts.ADMIN_INACTIVE}",
                f"adm:teacher:{teacher.teacher_id}",
            )
        ]
        for teacher in selected
    ]
    navigation = []
    if page:
        navigation.append((texts.ADMIN_PREVIOUS, f"adm:list:{page - 1}"))
    if (page + 1) * page_size < len(teachers):
        navigation.append((texts.ADMIN_NEXT, f"adm:list:{page + 1}"))
    if navigation:
        rows.append(navigation)
    rows.append([(texts.ADMIN_BACK, "adm:menu")])
    await _show(target, texts.ADMIN_TEACHERS, rows)


async def _teacher_detail(target: Message | CallbackQuery, services: Services, teacher_id: int) -> None:
    teacher = await services.teachers.get(teacher_id)
    if teacher is None:
        await _show(target, texts.ADMIN_NOT_FOUND, [[(texts.ADMIN_BACK, "adm:list:0")]])
        return
    rows = [
        [(texts.ADMIN_RESET, f"adm:reset:{teacher_id}")],
        [
            (
                texts.ADMIN_DISABLE if teacher.active else texts.ADMIN_ENABLE,
                f"adm:active:{teacher_id}",
            )
        ],
        [(texts.ADMIN_CHANGE_ROLE, f"adm:role:{teacher_id}")],
        [(texts.ADMIN_BACK, "adm:list:0")],
    ]
    await _show(
        target,
        texts.ADMIN_TEACHER_DETAIL.format(
            nick=teacher.nick,
            role=LABELS_RU[teacher.role.value],
            status=texts.ADMIN_ACTIVE if teacher.active else texts.ADMIN_INACTIVE,
        ),
        rows,
    )


def _role_rows(prefix: str) -> list[list[tuple[str, str]]]:
    return [
        [(LABELS_RU[Role.TEACHER.value], f"{prefix}:{Role.TEACHER.value}")],
        [(LABELS_RU[Role.METHODIST.value], f"{prefix}:{Role.METHODIST.value}")],
        [(LABELS_RU[Role.PSYCHOLOGIST.value], f"{prefix}:{Role.PSYCHOLOGIST.value}")],
        [(texts.ADMIN_BACK, "adm:menu")],
    ]


async def _knowledge_menu(target: Message | CallbackQuery) -> None:
    await _show(
        target,
        texts.ADMIN_KNOWLEDGE_INFO,
        [
            [(texts.ADMIN_KNOWLEDGE_STATUS, "adm:status")],
            [(texts.ADMIN_KNOWLEDGE_UPDATE, "adm:update")],
            [(texts.ADMIN_KNOWLEDGE_UPLOAD, "adm:upload")],
            [(texts.ADMIN_BACK, "adm:menu")],
        ],
    )


def _progress_editor(status: Message):
    last_update = 0.0

    async def progress(description: str) -> None:
        nonlocal last_update
        now = time.monotonic()
        if now - last_update < 1.5:
            return
        last_update = now
        with suppress(TelegramBadRequest):
            await status.edit_text(texts.ADMIN_PROGRESS.format(description=description[:3900]))

    return progress


@router.message(F.text == texts.ADMIN_PANEL)
async def open_admin(message: Message, state: FSMContext, session: SessionInfo) -> None:
    if session.role != Role.ADMIN:
        await message.answer(texts.ADMIN_FORBIDDEN)
        return
    await _menu(message, state)


@router.message(AdminFlow.nick, F.text)
async def nick_input(message: Message, state: FSMContext, session: SessionInfo) -> None:
    if session.role != Role.ADMIN:
        await message.answer(texts.ADMIN_FORBIDDEN)
        return
    nick = (message.text or "").strip()
    if not 2 <= len(nick) <= 64:
        await message.answer(texts.ADMIN_NICK_INVALID)
        return
    await state.update_data(nick=nick)
    await state.set_state(AdminFlow.role)
    await _show(message, texts.ADMIN_ROLE_PROMPT, _role_rows("adm:newrole"))


@router.message(AdminFlow.upload)
async def upload_material(
    message: Message, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    if session.role != Role.ADMIN:
        await message.answer(texts.ADMIN_FORBIDDEN)
        return
    document = message.document
    if document is None or not document.file_name:
        await message.answer(texts.ADMIN_UPLOAD_INVALID)
        return
    filename = Path(document.file_name).name
    if (
        Path(filename).suffix.lower() not in (".pdf", ".docx")
        or not document.file_size
        or document.file_size > MAX_SCHOOL_FILE_BYTES
    ):
        await message.answer(texts.ADMIN_UPLOAD_INVALID)
        return
    status = await message.answer(texts.ADMIN_UPLOAD_RUNNING)
    try:
        downloaded = await asyncio.wait_for(message.bot.download(document), timeout=60)
        content = downloaded.getvalue()
        if len(content) > MAX_SCHOOL_FILE_BYTES:
            await status.edit_text(texts.ADMIN_UPLOAD_INVALID)
            return
        source = await asyncio.wait_for(
            services.knowledge.ingest_school_material(filename, content, _progress_editor(status)),
            timeout=180,
        )
        await status.edit_text(texts.ADMIN_UPLOAD_DONE.format(title=source.title))
        await state.clear()
    except Exception:
        logger.exception("School material upload failed")
        await status.edit_text(texts.ADMIN_UPLOAD_FAILED)


@router.callback_query(F.data.startswith("adm:"))
async def admin_callback(
    callback: CallbackQuery, state: FSMContext, services: Services, session: SessionInfo
) -> None:
    if session.role != Role.ADMIN:
        await callback.answer(texts.ADMIN_FORBIDDEN, show_alert=True)
        return
    await callback.answer()
    action = (callback.data or "").split(":")
    verb = action[1]
    if verb == "menu":
        await _menu(callback, state)
    elif verb == "add":
        await state.set_state(AdminFlow.nick)
        await _show(callback, texts.ADMIN_NICK_PROMPT, [[(texts.ADMIN_BACK, "adm:menu")]])
    elif verb == "newrole" and isinstance(callback.message, Message):
        role = Role(action[2])
        nick = (await state.get_data()).get("nick", "")
        if not nick or await state.get_state() != AdminFlow.role.state:
            await _menu(callback, state)
            return
        try:
            credentials = await services.teachers.create(nick, role)
        except IntegrityError:
            await state.set_state(AdminFlow.nick)
            await _show(callback, texts.ADMIN_NICK_EXISTS, [[(texts.ADMIN_BACK, "adm:menu")]])
            return
        await state.clear()
        await _show_credentials(callback.message, credentials, reset=False)
        await _menu(callback, state)
    elif verb == "list":
        await _teachers_page(callback, services, int(action[2]))
    elif verb == "teacher":
        await _teacher_detail(callback, services, int(action[2]))
    elif verb == "reset" and isinstance(callback.message, Message):
        teacher_id = int(action[2])
        try:
            credentials = await services.teachers.reset_password(teacher_id)
        except KeyError:
            await _show(callback, texts.ADMIN_NOT_FOUND, [[(texts.ADMIN_BACK, "adm:list:0")]])
            return
        await _show_credentials(callback.message, credentials, reset=True)
        await _teacher_detail(callback, services, teacher_id)
    elif verb == "active":
        teacher_id = int(action[2])
        teacher = await services.teachers.get(teacher_id)
        if teacher is None:
            await _show(callback, texts.ADMIN_NOT_FOUND, [[(texts.ADMIN_BACK, "adm:list:0")]])
            return
        await services.teachers.set_active(teacher_id, not teacher.active)
        await _teacher_detail(callback, services, teacher_id)
    elif verb == "role":
        await _show(callback, texts.ADMIN_ROLE_PROMPT, _role_rows(f"adm:setrole:{action[2]}"))
    elif verb == "setrole":
        teacher_id = int(action[2])
        try:
            await services.teachers.set_role(teacher_id, Role(action[3]))
        except KeyError:
            await _show(callback, texts.ADMIN_NOT_FOUND, [[(texts.ADMIN_BACK, "adm:list:0")]])
            return
        await _teacher_detail(callback, services, teacher_id)
    elif verb == "knowledge":
        await _knowledge_menu(callback)
    elif verb == "status":
        report = services.knowledge.status()
        titles = ", ".join(source.title for source in report.documents[:10])
        documents = f"{len(report.documents)} ({titles})" if titles else "0"
        await _show(
            callback,
            texts.ADMIN_KNOWLEDGE_REPORT.format(
                documents=documents,
                chunks=report.chunks_total,
                outcomes=report.outcomes_total,
                ready=texts.ADMIN_READY_YES if report.ready else texts.ADMIN_READY_NO,
                problems="; ".join(report.problems)[:2000] or texts.ADMIN_NO_PROBLEMS,
            ),
            [[(texts.ADMIN_BACK, "adm:knowledge")]],
        )
    elif verb == "update" and isinstance(callback.message, Message):
        status = await callback.message.answer(texts.ADMIN_UPDATE_RUNNING)
        try:
            report = await asyncio.wait_for(services.knowledge.update(_progress_editor(status)), timeout=600)
            await status.edit_text(
                texts.ADMIN_UPDATE_DONE.format(
                    downloaded=len(report.downloaded),
                    unchanged=len(report.unchanged),
                    failed=len(report.failed),
                )
            )
        except Exception:
            logger.exception("Knowledge update failed")
            await status.edit_text(texts.ADMIN_UPDATE_FAILED)
    elif verb == "upload":
        await state.set_state(AdminFlow.upload)
        await _show(callback, texts.ADMIN_UPLOAD_PROMPT, [[(texts.ADMIN_BACK, "adm:knowledge")]])
    elif verb == "usage":
        summary = await services.usage.summary(30)
        await _show(
            callback,
            texts.ADMIN_USAGE_REPORT.format(
                days=summary.period_days,
                requests=summary.requests,
                tokens_in=summary.tokens_in,
                tokens_out=summary.tokens_out,
                cost=summary.cost_usd,
            ),
            [[(texts.ADMIN_BACK, "adm:menu")]],
        )
    elif verb == "payment":
        await _show(callback, texts.ADMIN_SOON, [[(texts.ADMIN_BACK, "adm:menu")]])
