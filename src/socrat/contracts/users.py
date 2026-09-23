"""Пользователи, сессии, обратная связь, учёт расходов.

Персональные данные: храним ТОЛЬКО ник педагога, роль и хэш пароля (решение E3).
Telegram ID в БД НЕ хранится; сессия привязана к хэшу chat_id и живёт ограниченное время.
Данные детей не храним никогда.

Производитель/владелец реализации: storage + bot (Codex).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from .enums import Rating, Role


def _now() -> datetime:
    return datetime.now(UTC)


class TeacherAccount(BaseModel):
    teacher_id: int
    nick: str = Field(..., min_length=2, max_length=64, description="Ник/подпись, не обязательно ФИО")
    role: Role
    active: bool = True
    created_at: datetime = Field(default_factory=_now)


class NewTeacherCredentials(BaseModel):
    """Возвращается админу ОДИН раз при создании/сбросе. Пароль в открытом виде больше нигде не хранится."""

    account: TeacherAccount
    plain_password: str


class SessionInfo(BaseModel):
    role: Role
    teacher_id: int | None = Field(None, description="None для администратора (пароль из .env)")
    nick: str
    expires_at: datetime


class Feedback(BaseModel):
    work_id: str
    task_id: str
    rating: Rating
    comment: str | None = Field(None, max_length=1000)
    teacher_id: int | None = None
    created_at: datetime = Field(default_factory=_now)


class UsageRecord(BaseModel):
    kind: str = Field(..., description="'generation' | 'analysis' | 'observation' | 'embedding'")
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    teacher_id: int | None = None
    created_at: datetime = Field(default_factory=_now)


class UsageSummary(BaseModel):
    period_days: int
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    by_kind: dict[str, int] = Field(default_factory=dict)
