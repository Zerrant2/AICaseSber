"""Модуль «Анализ типичных ошибок» (основной сценарий №2) и разбор обезличенного ответа.

Анализ ошибок: учитель СЛОВАМИ описывает типичную ошибку группы детей (без имён) →
агент раскладывает её по слоям (операциональный / концептуальный / регулятивный /
коммуникативный), связывает с УУД и выдаёт 2–3 приёма коррекции из библиотеки.

Разбор ответа: учитель вводит обезличенный текст ответа на задание из сгенерированной
работы → раздельная фиксация предметной правильности и наблюдаемости УУД (тесты T06, T07).

Производитель: core (Claude). Потребители: bot, export (Codex).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import ErrorLayer, Role, SubjectObservation, UUDGroup, UUDObservation
from .request import GuardrailIssue
from .work import GenerationMeta, OutcomeLink

ANALYSIS_LIMITATIONS_DEFAULT = [
    "Это гипотезы о возможных причинах, а не заключение о конкретном ребёнке.",
    "Агент не ставит диагнозов и не оценивает личность, мотивацию или интеллект.",
    "Решение о коррекционной работе принимает педагог.",
]


class ErrorAnalysisRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid4().hex)
    grade: int = Field(..., ge=1, le=11)
    subject_id: str
    subject_name: str
    topic: str = Field(..., min_length=2, max_length=500)
    description: str = Field(
        ..., min_length=10, max_length=3000, description="Описание наблюдаемой ошибки, БЕЗ ФИО детей"
    )
    author_role: Role = Role.TEACHER


class ErrorHypothesis(BaseModel):
    layer: ErrorLayer
    uud_group: UUDGroup | None = None
    statement: str = Field(..., description="Формулировка гипотезы причины")
    confidence: Literal["low", "medium", "high"] = "medium"
    signs_to_check: list[str] = Field(
        default_factory=list, description="Что посмотреть/спросить, чтобы подтвердить или отвергнуть"
    )


class TechniqueRecommendation(BaseModel):
    technique_id: str = Field(..., description="ID из data/techniques.json (проверяется кодом)")
    name: str
    how_to_apply: str = Field(..., description="Как применить именно на этой теме на следующем уроке")
    expected_shift: str = Field(..., description="Ожидаемый сдвиг в действиях детей")


class QuickCheckTask(BaseModel):
    """Мини-задание, чтобы проверить гипотезу на следующем уроке."""

    student_text: str
    expected_answer: str
    what_it_checks: str


class ErrorAnalysisResult(BaseModel):
    analysis_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: ErrorAnalysisRequest
    summary: str = Field(..., description="2–3 предложения: что, вероятно, происходит")
    hypotheses: list[ErrorHypothesis] = Field(..., min_length=1, description="Основная — первой")
    outcome_links: list[OutcomeLink] = Field(default_factory=list)
    techniques: list[TechniqueRecommendation] = Field(..., min_length=1, max_length=3)
    quick_checks: list[QuickCheckTask] = Field(default_factory=list, max_length=2)
    teacher_reflection_question: str = ""
    limitations: list[str] = Field(default_factory=lambda: list(ANALYSIS_LIMITATIONS_DEFAULT))
    warnings: list[GuardrailIssue] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)


# ----------------------------- разбор ответа ------------------------------------------


class UUDObservationItem(BaseModel):
    group: UUDGroup
    outcome_id: str
    status: UUDObservation
    basis: str = Field(..., description="Что именно в тексте ответа стало основанием")
    alternatives: list[str] = Field(
        default_factory=list, description="Альтернативные объяснения (не приписывать скрытый ход мысли)"
    )


class ResponseObservation(BaseModel):
    task_id: str
    response_text: str
    subject_status: SubjectObservation
    subject_basis: str
    extracted_answer: str | None = None
    uud: list[UUDObservationItem] = Field(default_factory=list)
    summary_ru: str = Field(..., description="Готовый текст для учителя")
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Наблюдение по конкретному ответу, не заключение об устойчивых способностях ребёнка.",
            "Числовой ответ сам по себе не подтверждает планирование или самоконтроль.",
        ]
    )


class ReflectionObservation(BaseModel):
    """Нейтральная фиксация ответа ребёнка на рефлексивный вопрос (T07)."""

    question: str
    answer_text: str
    neutral_note: str = Field(..., description="Нейтральная фиксация без оценки личности и диагноза")
    suggested_teacher_action: str | None = None
