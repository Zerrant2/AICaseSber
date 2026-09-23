"""Async SQLAlchemy implementations of the storage contracts."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from socrat.config import Settings, get_settings
from socrat.contracts import (
    DiagnosticWork,
    Feedback,
    NewTeacherCredentials,
    Rating,
    Role,
    Task,
    TeacherAccount,
    UsageRecord,
    UsageSummary,
)

from .models import FeedbackRow, TeacherRow, UsageRow, WorkRow
from .security import keyed_lookup, secret_bytes

PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _account(row: TeacherRow) -> TeacherAccount:
    return TeacherAccount(
        teacher_id=row.id,
        nick=row.nick,
        role=Role(row.role),
        active=row.active,
        created_at=_as_utc(row.created_at),
    )


class SqlTeacherRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings | None = None) -> None:
        self._sessions = sessions
        self._settings = settings or get_settings()
        self._secret = secret_bytes(self._settings)
        self._hasher = PasswordHasher()

    def _new_password(self) -> str:
        length = self._settings.generated_password_length
        if length < 1:
            raise ValueError("GENERATED_PASSWORD_LENGTH must be positive")
        return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))

    async def create(self, nick: str, role: Role) -> NewTeacherCredentials:
        nick = nick.strip()
        if not 2 <= len(nick) <= 64:
            raise ValueError("Ник педагога должен содержать от 2 до 64 символов")
        if role == Role.ADMIN:
            raise ValueError("Администратор входит только по ADMIN_PASSWORD")
        password = self._new_password()
        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        async with self._sessions.begin() as session:
            row = TeacherRow(
                nick=nick,
                role=role.value,
                password_lookup=keyed_lookup(self._secret, password),
                password_hash=password_hash,
                active=True,
                created_by="admin",
            )
            session.add(row)
            await session.flush()
            account = _account(row)
        return NewTeacherCredentials(account=account, plain_password=password)

    async def authenticate(self, password: str) -> TeacherAccount | None:
        lookup = keyed_lookup(self._secret, password)
        async with self._sessions() as session:
            row = await session.scalar(select(TeacherRow).where(TeacherRow.password_lookup == lookup))
            if row is None:
                return None
            try:
                verified = await asyncio.to_thread(self._hasher.verify, row.password_hash, password)
            except (VerificationError, InvalidHashError):
                return None
            return _account(row) if verified and row.active else None

    async def list(self, include_inactive: bool = False) -> list[TeacherAccount]:
        query = select(TeacherRow).order_by(TeacherRow.id)
        if not include_inactive:
            query = query.where(TeacherRow.active.is_(True))
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()
            return [_account(row) for row in rows]

    async def get(self, teacher_id: int) -> TeacherAccount | None:
        async with self._sessions() as session:
            row = await session.get(TeacherRow, teacher_id)
            return _account(row) if row is not None else None

    async def reset_password(self, teacher_id: int) -> NewTeacherCredentials:
        password = self._new_password()
        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        async with self._sessions.begin() as session:
            row = await session.get(TeacherRow, teacher_id, with_for_update=True)
            if row is None:
                raise KeyError(teacher_id)
            row.password_lookup = keyed_lookup(self._secret, password)
            row.password_hash = password_hash
            await session.flush()
            account = _account(row)
        return NewTeacherCredentials(account=account, plain_password=password)

    async def set_active(self, teacher_id: int, active: bool) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(TeacherRow, teacher_id, with_for_update=True)
            if row is None:
                raise KeyError(teacher_id)
            row.active = active

    async def set_role(self, teacher_id: int, role: Role) -> None:
        if role == Role.ADMIN:
            raise ValueError("Администратор входит только по ADMIN_PASSWORD")
        async with self._sessions.begin() as session:
            row = await session.get(TeacherRow, teacher_id, with_for_update=True)
            if row is None:
                raise KeyError(teacher_id)
            row.role = role.value


class SqlWorkRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def save(self, work: DiagnosticWork, teacher_id: int | None) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(WorkRow, work.work_id)
            if row is None:
                row = WorkRow(work_id=work.work_id)
                session.add(row)
            row.teacher_id = teacher_id
            row.created_at = work.created_at
            row.grade = work.request.grade
            row.subject_id = work.request.subject_id
            row.work_json = work.model_dump_json()

    async def get(self, work_id: str) -> DiagnosticWork | None:
        async with self._sessions() as session:
            row = await session.get(WorkRow, work_id)
            return DiagnosticWork.model_validate_json(row.work_json) if row is not None else None


class SqlFeedbackRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, feedback: Feedback) -> None:
        async with self._sessions.begin() as session:
            session.add(
                FeedbackRow(
                    work_id=feedback.work_id,
                    task_id=feedback.task_id,
                    rating=feedback.rating.value,
                    comment=feedback.comment,
                    teacher_id=feedback.teacher_id,
                    created_at=feedback.created_at,
                )
            )

    async def top_examples(self, grade: int, subject_id: str, k: int = 3) -> list[Task]:
        if k <= 0:
            return []
        down = aliased(FeedbackRow)
        down_exists = (
            select(down.id)
            .where(
                down.work_id == FeedbackRow.work_id,
                down.task_id == FeedbackRow.task_id,
                down.rating == Rating.DOWN.value,
            )
            .exists()
        )
        query = (
            select(FeedbackRow, WorkRow.work_json)
            .join(WorkRow, WorkRow.work_id == FeedbackRow.work_id)
            .where(
                WorkRow.grade == grade,
                WorkRow.subject_id == subject_id,
                FeedbackRow.rating == Rating.UP.value,
                ~down_exists,
            )
            .order_by(FeedbackRow.created_at.desc(), FeedbackRow.id.desc())
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        examples: list[Task] = []
        seen: set[tuple[str, str]] = set()
        for feedback, work_json in rows:
            key = (feedback.work_id, feedback.task_id)
            if key in seen:
                continue
            seen.add(key)
            task = DiagnosticWork.model_validate_json(work_json).find_task(feedback.task_id)
            if task is not None:
                examples.append(task)
                if len(examples) == k:
                    break
        return examples

    async def stats(self) -> dict[Rating, int]:
        async with self._sessions() as session:
            rows = (
                await session.execute(select(FeedbackRow.rating, func.count()).group_by(FeedbackRow.rating))
            ).all()
        return {Rating(rating): count for rating, count in rows}


class SqlUsageRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, record: UsageRecord) -> None:
        async with self._sessions.begin() as session:
            session.add(
                UsageRow(
                    kind=record.kind,
                    model=record.model,
                    tokens_in=record.tokens_in,
                    tokens_out=record.tokens_out,
                    cost_usd=record.cost_usd,
                    teacher_id=record.teacher_id,
                    created_at=record.created_at,
                )
            )

    async def summary(self, days: int = 30) -> UsageSummary:
        if days < 1:
            raise ValueError("days must be positive")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        query = (
            select(
                UsageRow.kind,
                func.count(),
                func.sum(UsageRow.tokens_in),
                func.sum(UsageRow.tokens_out),
                func.sum(func.coalesce(UsageRow.cost_usd, 0.0)),
            )
            .where(UsageRow.created_at >= cutoff)
            .group_by(UsageRow.kind)
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        summary = UsageSummary(period_days=days)
        for kind, requests, tokens_in, tokens_out, cost_usd in rows:
            summary.requests += requests
            summary.tokens_in += tokens_in
            summary.tokens_out += tokens_out
            summary.cost_usd += cost_usd
            summary.by_kind[kind] = requests
        return summary
