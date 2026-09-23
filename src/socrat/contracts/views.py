"""Проекции DiagnosticWork для разных читателей.

ПРАВИЛО ЭКСПОРТА: ученический документ строится ТОЛЬКО из `student_view(work)`.
Так ответы физически не могут попасть к ребёнку (тест T02 проверяет это автоматически).
"""

from __future__ import annotations

from pydantic import BaseModel

from .work import DiagnosticWork


class StudentTask(BaseModel):
    number: int
    text: str
    support: str | None = None
    answer_lines: int = 3


class StudentVariant(BaseModel):
    label: str  # «Вариант А» — без ярлыка способностей
    instruction: str
    tasks: list[StudentTask]
    reflection_questions: list[str]


class StudentView(BaseModel):
    title: str
    grade: int
    subject_name: str
    variants: list[StudentVariant]


def student_view(work: DiagnosticWork) -> StudentView:
    return StudentView(
        title=work.title,
        grade=work.request.grade,
        subject_name=work.request.subject_name,
        variants=[
            StudentVariant(
                label=v.student_label,
                instruction=v.instruction,
                tasks=[
                    StudentTask(
                        number=t.number,
                        text=t.modes.get(work.request.presentation_mode, t.student_text),
                        support=t.support,
                        answer_lines=t.answer_lines,
                    )
                    for t in v.tasks
                ],
                reflection_questions=list(v.reflection.questions),
            )
            for v in work.variants
        ],
    )
