"""Load the current session and gate unauthenticated Telegram updates."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message, TelegramObject

from socrat.contracts import SessionInfo
from socrat.storage import SessionStore

from . import texts


class AuthMiddleware(BaseMiddleware):
    def __init__(self, sessions: SessionStore) -> None:
        self._sessions = sessions

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        message = event if isinstance(event, Message) else getattr(event, "message", None)
        chat = getattr(message, "chat", None)
        if chat is None:
            return None
        session = await self._sessions.get(chat.id)
        data["session"] = session
        data["session_store"] = self._sessions
        if session is not None:
            return await handler(event, data)
        if isinstance(event, Message):
            command = (event.text or "").partition(" ")[0].partition("@")[0]
            if command == "/start" or (
                event.text
                and not event.text.startswith("/")
                and event.text not in texts.PROTECTED_MENU_LABELS
            ):
                return await handler(event, data)
            await event.answer(texts.LOGIN_REQUIRED)
            return None
        if isinstance(event, CallbackQuery):
            await event.answer(texts.LOGIN_REQUIRED, show_alert=True)
        return None


class Unauthenticated(Filter):
    async def __call__(self, message: Message, session: SessionInfo | None) -> bool:
        return session is None
