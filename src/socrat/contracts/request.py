"""Запрос учителя на генерацию и результат проверки ограничителей (guardrails)."""

from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import GuardrailCode, LevelChoice, PresentationMode, Role, Severity, UUDFocus

MAX_TASKS_DEFAULT = 8


class GenerationRequest(BaseModel):
    """Всё, что учитель выбрал в боте. Собирается FSM бота, потребляется core."""

    request_id: str = Field(default_factory=lambda: uuid4().hex)
    grade: int = Field(..., ge=1, le=11)
    subject_id: str = Field(..., min_length=1)
    subject_name: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=2, max_length=500, description="Свободный текст учителя")
    level: LevelChoice
    uud_focus: UUDFocus = UUDFocus.BALANCED
    task_count: int = Field(4, ge=1, le=MAX_TASKS_DEFAULT)
    presentation_mode: PresentationMode = PresentationMode.TEXT
    minutes_min: int = 15
    minutes_max: int = 20
    requested_minutes: int | None = Field(
        None, description="Если учитель явно попросил длительность (напр. '8 минут') — для T08"
    )
    author_role: Role = Role.TEACHER
    teacher_note: str | None = Field(None, max_length=1000, description="Доп. пожелания, без ПДн")

    @field_validator("topic")
    @classmethod
    def _strip(cls, v: str) -> str:
        return " ".join(v.split())


class GuardrailIssue(BaseModel):
    code: GuardrailCode
    severity: Severity
    message_ru: str = Field(..., description="Готовый текст для учителя (бот показывает как есть)")
    suggestions: list[str] = Field(
        default_factory=list, description="Варианты для кнопок: темы, число заданий и т.п."
    )


class GuardrailResult(BaseModel):
    """Результат предварительной проверки запроса.

    Бот: если blocked — показать issues и НЕ генерировать; если есть WARN — показать и
    дать кнопки «Продолжить» / «Изменить».
    """

    issues: list[GuardrailIssue] = Field(default_factory=list)
    estimated_minutes: float | None = Field(None, description="Расчёт кодом по числу/уровню заданий")

    @property
    def blocked(self) -> bool:
        return any(i.severity == Severity.BLOCK for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == Severity.WARN for i in self.issues)
