"""Password sign-in, sessions and the main Telegram menu."""

from __future__ import annotations

import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from socrat.config import Settings
from socrat.contracts import Role, Services, SessionInfo, TeacherRepository
from socrat.storage import SessionStore

from . import texts
from .middleware import Unauthenticated

router = Router(name="auth")


def main_menu(role: Role) -> ReplyKeyboardMarkup:
    labels = [texts.CREATE_WORK, texts.ANALYZE_ERROR, texts.BOUNDARIES]
    if role == Role.ADMIN:
        labels.append(texts.ADMIN_PANEL)
    labels.append(texts.LOGOUT)
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label)] for label in labels],
        resize_keyboard=True,
    )


async def authenticate_password(
    password: str, teachers: TeacherRepository, settings: Settings
) -> SessionInfo | None:
    admin_password = settings.admin_password.get_secret_value()
    expires_at = datetime.now(UTC) + timedelta(hours=settings.session_ttl_hours)
    if admin_password and secrets.compare_digest(password, admin_password):
        return SessionInfo(role=Role.ADMIN, teacher_id=None, nick="Администратор", expires_at=expires_at)
    teacher = await teachers.authenticate(password)
    if teacher is None:
        return None
    return SessionInfo(
        role=teacher.role,
        teacher_id=teacher.teacher_id,
        nick=teacher.nick,
        expires_at=expires_at,
    )


@router.message(CommandStart())
async def start(message: Message, session: SessionInfo | None) -> None:
    if session is not None:
        await message.answer(
            texts.ALREADY_LOGGED_IN.format(nick=session.nick), reply_markup=main_menu(session.role)
        )
        return
    await message.answer(texts.START)


@router.message(Command("logout"))
@router.message(F.text == texts.LOGOUT)
async def logout(message: Message, session_store: SessionStore) -> None:
    await session_store.delete(message.chat.id)
    await message.answer(texts.LOGGED_OUT)


@router.message(Unauthenticated(), F.text)
async def password_input(
    message: Message,
    session_store: SessionStore,
    services: Services,
    settings: Settings,
) -> None:
    with suppress(Exception):
        await message.delete()
    if await session_store.is_locked(message.chat.id):
        await message.answer(texts.LOGIN_LOCKED)
        return
    session = await authenticate_password(message.text or "", services.teachers, settings)
    if session is None:
        locked = await session_store.register_failed_attempt(message.chat.id)
        await message.answer(texts.LOGIN_LOCKED if locked else texts.PASSWORD_WRONG)
        return
    await session_store.create(message.chat.id, session)
    await session_store.clear_failed_attempts(message.chat.id)
    await message.answer(texts.LOGIN_SUCCESS.format(nick=session.nick), reply_markup=main_menu(session.role))


@router.message(F.text == texts.BOUNDARIES)
async def boundaries(message: Message) -> None:
    await message.answer(texts.BOUNDARIES_INFO)
