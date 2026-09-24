"""Persistent sessions and login throttling keyed by an HMAC of the chat ID."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from socrat.config import Settings, get_settings
from socrat.contracts import Role, SessionInfo

from .models import LoginAttemptRow, SessionRow
from .security import keyed_lookup, secret_bytes


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SessionStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings | None = None) -> None:
        self._sessions = sessions
        self._settings = settings or get_settings()
        self._secret = secret_bytes(self._settings)

    def _chat_key(self, chat_id: int) -> str:
        return keyed_lookup(self._secret, str(chat_id))

    async def get(self, chat_id: int) -> SessionInfo | None:
        async with self._sessions.begin() as session:
            row = await session.get(SessionRow, self._chat_key(chat_id))
            if row is None:
                return None
            if _as_utc(row.expires_at) <= datetime.now(UTC):
                await session.delete(row)
                return None
            return SessionInfo(
                role=Role(row.role),
                teacher_id=row.teacher_id,
                nick=row.nick,
                expires_at=_as_utc(row.expires_at),
            )

    async def create(self, chat_id: int, info: SessionInfo) -> None:
        async with self._sessions.begin() as session:
            key = self._chat_key(chat_id)
            row = await session.get(SessionRow, key)
            if row is None:
                row = SessionRow(chat_key=key)
                session.add(row)
            row.role = info.role.value
            row.teacher_id = info.teacher_id
            row.nick = info.nick
            row.expires_at = info.expires_at

    async def delete(self, chat_id: int) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(SessionRow, self._chat_key(chat_id))
            if row is not None:
                await session.delete(row)

    async def is_locked(self, chat_id: int) -> bool:
        key = self._chat_key(chat_id)
        cutoff = datetime.now(UTC) - timedelta(minutes=self._settings.login_lockout_minutes)
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(LoginAttemptRow)
                .where(
                    LoginAttemptRow.chat_key == key,
                    LoginAttemptRow.ts >= cutoff,
                )
            )
        return bool(count and count >= self._settings.login_max_attempts)

    async def register_failed_attempt(self, chat_id: int) -> bool:
        key = self._chat_key(chat_id)
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=self._settings.login_lockout_minutes)
        async with self._sessions.begin() as session:
            await session.execute(
                delete(LoginAttemptRow).where(
                    LoginAttemptRow.chat_key == key,
                    LoginAttemptRow.ts < cutoff,
                )
            )
            session.add(LoginAttemptRow(chat_key=key, ts=now))
            await session.flush()
            count = await session.scalar(
                select(func.count())
                .select_from(LoginAttemptRow)
                .where(
                    LoginAttemptRow.chat_key == key,
                    LoginAttemptRow.ts >= cutoff,
                )
            )
        return bool(count and count >= self._settings.login_max_attempts)

    async def clear_failed_attempts(self, chat_id: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                delete(LoginAttemptRow).where(LoginAttemptRow.chat_key == self._chat_key(chat_id))
            )
