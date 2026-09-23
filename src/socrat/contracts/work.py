"""Диагностическая работа — главный объект системы.

Производитель: core.generator (Claude). Потребители: export (Codex), bot (Codex), storage (Codex).

Правила, которые ОБЯЗАН соблюдать экспорт (Codex):
  * В ученический документ идут ТОЛЬКО поля, помеченные [STUDENT] в описании.
  * Всё остальное — только в методичку учителя.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import (
    ErrorLayer,
    Level,
    OutcomeType,
    PresentationMode,
    TaskKind,
    UUDGroup,
    UUDObservation,
)
from .normative import SourceDocument
from .request import GenerationRequest, GuardrailIssue

TIME_BASIS_NOTE = "Время — расчётная оценка по типу и объёму заданий, а не измерение на реальных детях."
PERSONAL_NOTE = (
    "Личностная направленность — цель проектирования задания; индивидуальный балл за неё не ставится."
)
REFLECTION_NOTE = "Нет правильных ответов о чувствах и трудностях. Не переводить рефлексию в оценку личности."


class OutcomeLink(BaseModel):
    """Связь задания с планируемым результатом (строка «карты результатов»)."""

    outcome_id: str = Field(..., description="Внутренний ID каталога. НЕ номер пункта ФГОС")
    type: OutcomeType
    text: str = Field(..., description="Формулировка результата из каталога")
    source_id: str
    source_title: str
    section: str
    page: int = Field(..., ge=1)
    source_url: str
    rationale: str = Field(..., description="Почему это задание соответствует результату (1–2 фразы)")
    verified: bool = Field(False, description="Код проверил: outcome есть в каталоге и страница совпадает")
    verification_note: str | None = None


class UUDIndicator(BaseModel):
    """Наблюдаемый признак УУД в конкретном задании. Ставится ТОЛЬКО при наличии основания."""

    group: UUDGroup
    outcome_id: str
    action: str = Field(..., description="Какое действие наблюдаем: 'контроль: проверка обратным действием'")
    trigger: str = Field(
        ..., description="Фрагмент формулировки задания, который делает действие наблюдаемым"
    )
    evidence: str = Field(..., description="Что в записи ученика считать проявлением действия")
    scale_hints: dict[UUDObservation, str] = Field(
        default_factory=dict,
        description="Подсказки учителю по шкале кейса: что считать observed/partial/not_shown",
    )


class SolutionStep(BaseModel):
    text: str = Field(..., description="Шаг решения словами")
    expression: str | None = Field(
        None, description="Арифметическое выражение для проверки кодом: '3*4', '56-9', '(45-12)-15'"
    )
    result: str | None = Field(None, description="Результат шага, как записан: '12'")
    verified: bool | None = Field(None, description="None — не проверялось (нематематический шаг)")


class TypicalError(BaseModel):
    description: str = Field(..., description="Как выглядит ошибка в записи")
    layer: ErrorLayer
    interpretation: str = Field(..., description="ВОЗМОЖНОЕ объяснение (гипотеза, не вывод)")
    teacher_move: str = Field(..., description="Что сделать учителю: вопрос, приём")


class TaskChecks(BaseModel):
    """Отчёт автоматических проверок задания (заполняет core, показывается в методичке)."""

    math_verified: bool | None = Field(None, description="None — предмет/задание без арифметики")
    answer_leak_free: bool = True
    uud_grounded: bool = True
    outcomes_verified: bool = True
    level_consistent: bool = True
    notes: list[str] = Field(default_factory=list)


class Task(BaseModel):
    task_id: str = Field(..., description="'B-1', 'A-3' ... (буква уровня + номер)")
    number: int = Field(..., ge=1)
    level: Level
    kind: TaskKind

    # ---------- [STUDENT] видит ребёнок ----------
    student_text: str = Field(..., description="[STUDENT] Формулировка задания")
    support: str | None = Field(None, description="[STUDENT] Опора/подсказка (обычно только лёгкий)")
    answer_lines: int = Field(3, ge=1, le=12, description="[STUDENT] Сколько строк оставить для ответа")
    modes: dict[PresentationMode, str] = Field(
        default_factory=dict, description="[STUDENT] Альтернативные подачи (в MVP пусто)"
    )

    # ---------- только учитель ----------
    subject_goal: str = Field(..., description="Предметная цель: что ученик решает")
    meta_goal: str = Field(..., description="Метапредметная цель: какое действие можно увидеть")
    expected_answer: str
    solution_steps: list[SolutionStep] = Field(default_factory=list)
    alternative_solutions: list[str] = Field(
        default_factory=list, description="Другие верные способы — их НЕ отвергать"
    )
    outcome_links: list[OutcomeLink] = Field(default_factory=list)
    uud_indicators: list[UUDIndicator] = Field(default_factory=list)
    personal_orientation: str | None = Field(
        None, description="Практический смысл задачи / связь с рефлексией. Без балла."
    )
    typical_errors: list[TypicalError] = Field(default_factory=list)
    oral_questions: list[str] = Field(
        default_factory=list, description="Что учитель может спросить устно для углубления"
    )
    technique_ids: list[str] = Field(default_factory=list, description="ID из data/techniques.json")
    conducting_note: str = Field(
        "", description="Как проводить: допустимые подсказки, какие реплики исказят диагностику"
    )
    estimated_minutes: float = Field(..., gt=0, description="Считает КОД (core.timing), не LLM")
    checks: TaskChecks = Field(default_factory=TaskChecks)


class Reflection(BaseModel):
    questions: list[str] = Field(..., min_length=1, description="[STUDENT] Вопросы ребёнку")
    minutes: float = 3
    reading_guide: str = Field(
        "", description="Для учителя: как читать ответы (нейтральная фиксация, без оценки личности)"
    )
    note: str = REFLECTION_NOTE


class TimePlan(BaseModel):
    reading_minutes: float
    tasks_minutes: float
    reflection_minutes: float
    total_minutes: float
    minutes_min: int = 15
    minutes_max: int = 20
    within_range: bool
    basis: str = TIME_BASIS_NOTE


class Variant(BaseModel):
    level: Level
    student_label: str = Field(..., description="[STUDENT] 'Вариант А/Б/В' — без ярлыка способностей")
    instruction: str = Field(..., description="[STUDENT] Инструкция ребёнку")
    tasks: list[Task] = Field(..., min_length=1)
    reflection: Reflection
    time_plan: TimePlan
    level_rationale: str = Field(
        ..., description="Для учителя: чем уровень отличается (опоры, самостоятельность) — T09"
    )


class CoverageSummary(BaseModel):
    """Какие группы УУД и результаты представлены во всей работе (проверяет core)."""

    uud_groups: list[UUDGroup] = Field(default_factory=list)
    outcome_ids: list[str] = Field(default_factory=list)
    missing_groups: list[UUDGroup] = Field(default_factory=list)


class GenerationMeta(BaseModel):
    generator_version: str = "0.1.0"
    prompt_version: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    duration_s: float = 0.0
    llm_calls: int = 0
    repairs: int = 0
    is_fixture: bool = False


class DiagnosticWork(BaseModel):
    work_id: str = Field(default_factory=lambda: uuid4().hex)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request: GenerationRequest
    title: str = Field(..., description="[STUDENT] Тема работы для шапки")
    variants: list[Variant] = Field(..., min_length=1)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    limitations: list[str] = Field(
        default_factory=list,
        description="Границы: выбранные результаты ≠ весь ФГОС; внутренние коды ≠ номера пунктов; время расчётное",
    )
    sources: list[SourceDocument] = Field(default_factory=list, description="Использованные источники")
    warnings: list[GuardrailIssue] = Field(default_factory=list)
    meta: GenerationMeta = Field(default_factory=GenerationMeta)

    def variant(self, level: Level) -> Variant | None:
        return next((v for v in self.variants if v.level == level), None)

    def find_task(self, task_id: str) -> Task | None:
        for v in self.variants:
            for t in v.tasks:
                if t.task_id == task_id:
                    return t
        return None
