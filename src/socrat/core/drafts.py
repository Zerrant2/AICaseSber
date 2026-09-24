"""Черновые модели — то, что возвращает LLM. Намеренно проще контракта:
LLM НЕ заполняет страницы, разделы, URL, время и проверки — это делает код.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StepDraft(BaseModel):
    text: str = Field(..., description="Шаг решения словами, как записал бы ученик")
    expression: str | None = Field(None, description="Арифметическое выражение шага, напр. '7*8' или '56-9'")
    result: str | None = Field(None, description="Результат шага, напр. '56'")


class UUDDraft(BaseModel):
    group: Literal["cognitive", "regulatory", "communicative"]
    outcome_id: str = Field(..., description="ID из списка результатов соответствующего типа")
    trigger: str = Field(
        ..., description="ДОСЛОВНАЯ цитата из student_text или support, делающая действие видимым"
    )
    action: str = Field(..., description="Какое действие наблюдаем")
    evidence: str = Field(..., description="Что в записи ученика считать проявлением действия")


class ErrorDraft(BaseModel):
    description: str
    layer: Literal["operational", "conceptual", "regulatory", "communicative"]
    interpretation: str = Field(..., description="Возможное объяснение (гипотеза)")
    teacher_move: str


class TaskDraft(BaseModel):
    number: int
    student_text: str = Field(..., description="Формулировка для ребёнка. Без ответа и без решения")
    support: str | None = Field(None, description="Опора/подсказка для ребёнка (лёгкий уровень) или null")
    subject_goal: str
    meta_goal: str
    expected_answer: str
    solution_steps: list[StepDraft] = Field(default_factory=list)
    alternative_solutions: list[str] = Field(default_factory=list)
    subject_outcome_ids: list[str] = Field(default_factory=list)
    uud: list[UUDDraft] = Field(default_factory=list)
    personal_orientation: str | None = None
    typical_errors: list[ErrorDraft] = Field(default_factory=list)
    oral_questions: list[str] = Field(default_factory=list)
    technique_ids: list[str] = Field(default_factory=list)
    conducting_note: str = ""


class VariantDraft(BaseModel):
    title: str = Field(..., description="Короткое название работы для шапки листа (тема)")
    tasks: list[TaskDraft]
    reflection_questions: list[str] = Field(default_factory=list)
    level_rationale: str = Field("", description="Чем этот уровень отличается: опоры, самостоятельность")


class HypothesisDraft(BaseModel):
    layer: Literal["operational", "conceptual", "regulatory", "communicative"]
    uud_group: Literal["cognitive", "regulatory", "communicative"] | None = None
    statement: str
    confidence: Literal["low", "medium", "high"] = "medium"
    signs_to_check: list[str] = Field(default_factory=list)


class TechniqueDraft(BaseModel):
    technique_id: str
    how_to_apply: str
    expected_shift: str


class QuickCheckDraft(BaseModel):
    student_text: str
    expected_answer: str
    what_it_checks: str


class AnalysisDraft(BaseModel):
    summary: str
    hypotheses: list[HypothesisDraft]
    outcome_ids: list[str] = Field(default_factory=list)
    techniques: list[TechniqueDraft]
    quick_checks: list[QuickCheckDraft] = Field(default_factory=list)
    teacher_reflection_question: str = ""


def compact_schema(model: type[BaseModel]) -> dict:
    """JSON-схема без title/description-шума (короче промпт и совместимее с провайдерами)."""
    schema = model.model_json_schema()

    def strip(node):
        if isinstance(node, dict):
            node.pop("title", None)
            for v in node.values():
                strip(v)
        elif isinstance(node, list):
            for v in node:
                strip(v)

    strip(schema)
    return schema
