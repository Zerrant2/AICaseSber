"""Ограничители: честные границы прототипа ДО генерации (тесты кейса T05, T08, T10, T12).

Все проверки детерминированные (регулярки + морфология), без LLM: быстро, предсказуемо,
одинаково в тестах и в демо.
"""

from __future__ import annotations

import re

from socrat.config import Settings
from socrat.contracts import (
    ErrorAnalysisRequest,
    GenerationRequest,
    GuardrailCode,
    GuardrailIssue,
    GuardrailResult,
    KnowledgeBase,
    Level,
    Outcome,
    Severity,
)

from .specs import estimate_work_minutes

NORM_REF_RE = re.compile(r"(пункт\w*|п\.\s*\d|§|стать\w*\s+\d|раздел\w*\s+\d|\b\d+\.\d+(?:\.\d+)+\b)", re.I)
FGOS_WORD_RE = re.compile(r"фгос|стандарт|фоп|фрп|приказ", re.I)
GUARANTEE_RE = re.compile(
    r"(гарантир\w*|гарантию|полн\w*\s+соответств\w*|всему\s+фгос|весь\s+фгос|всех?\s+требовани\w*\s+фгос|сертифик\w*)",
    re.I,
)
DIAGNOSIS_RE = re.compile(
    r"(диагноз\w*|сдвг|дислекс\w*|дисграф\w*|дискальк\w*|\bзпр\b|аутизм\w*|\biq\b|уровень\s+интеллект\w*|"
    r"оцен\w*\s+личност\w*|черт\w*\s+характер\w*|выяви\w*\s+отстающ\w*|психиатр\w*)",
    re.I,
)
MINUTES_RE = re.compile(r"(\d{1,3})\s*(?:мин\w*|’|')", re.I)


def _outcome_suggestions(outcomes: list[Outcome], k: int = 3) -> list[str]:
    out = []
    for o in outcomes[:k]:
        text = o.text if len(o.text) < 90 else o.text[:87] + "…"
        out.append(f"{o.outcome_id}: {text} (стр. {o.page})")
    return out


class Guardrails:
    def __init__(self, settings: Settings, knowledge: KnowledgeBase) -> None:
        self.settings = settings
        self.knowledge = knowledge

    # ------------------------------------------------------------ generation request

    def check_request(self, req: GenerationRequest) -> GuardrailResult:
        issues: list[GuardrailIssue] = []
        free_text = f"{req.topic}\n{req.teacher_note or ''}"

        # T10: предмет/класс вне базы
        subjects = {s.subject_id: s for s in self.knowledge.list_subjects(req.grade)}
        if subjects and req.subject_id not in subjects:
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.SUBJECT_UNKNOWN,
                    severity=Severity.BLOCK,
                    message_ru=(
                        f"Предмета «{req.subject_name}» для {req.grade} класса нет в нормативной базе, поэтому "
                        "привязать задания к ФГОС честно не получится. Выберите предмет из списка."
                    ),
                    suggestions=[s.name for s in list(subjects.values())[:6]],
                )
            )

        # Слишком много заданий
        if req.task_count > self.settings.max_tasks:
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.TOO_MANY_TASKS,
                    severity=Severity.BLOCK,
                    message_ru=f"Можно не больше {self.settings.max_tasks} заданий в одной работе.",
                    suggestions=[str(n) for n in (4, 5, 6)],
                )
            )

        # T05: просьба о конкретном нормативном пункте
        if NORM_REF_RE.search(free_text) and FGOS_WORD_RE.search(free_text):
            num = re.search(
                r"\d+(?:\.\d+)+|(?:пункт\w*|п\.|§|стать\w*|раздел\w*)\s*(\d+(?:\.\d+)*)", free_text, re.I
            )
            ref = (num.group(1) or num.group(0)) if num else NORM_REF_RE.search(free_text).group(0)
            outs = self.knowledge.get_outcomes(req.grade, req.subject_id, query=req.topic, k=5)
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.UNKNOWN_NORM_REFERENCE,
                    severity=Severity.WARN,
                    message_ru=(
                        f"Пункта «{ref}» нет в фиксированном справочнике системы — выдумывать его я не буду. "
                        "Задания будут привязаны к планируемым результатам, которые есть в базе "
                        "(с источником, разделом и страницей). Внутренние коды результатов — не номера пунктов ФГОС."
                    ),
                    suggestions=_outcome_suggestions(outs),
                )
            )

        # T12: «гарантируй полное соответствие ФГОС»
        if GUARANTEE_RE.search(free_text):
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.FULL_FGOS_GUARANTEE,
                    severity=Severity.WARN,
                    message_ru=(
                        "Одна короткая работа не может охватить весь ФГОС, и гарантировать полное соответствие "
                        "я не могу. В методичке будет проверяемая карта: какие выбранные планируемые результаты "
                        "проверяет каждое задание, с источником, разделом и страницей."
                    ),
                )
            )

        # Диагнозы / оценка личности
        if DIAGNOSIS_RE.search(free_text):
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.DIAGNOSIS_REQUEST,
                    severity=Severity.BLOCK,
                    message_ru=(
                        "Система не ставит диагнозов и не оценивает личность, интеллект или мотивацию ребёнка. "
                        "Она помогает наблюдать учебные действия. Уберите эту просьбу из темы."
                    ),
                )
            )

        # T08: время
        requested = req.requested_minutes
        if requested is None:
            m = MINUTES_RE.search(free_text)
            if m:
                requested = int(m.group(1))
        lo, hi = req.minutes_min, req.minutes_max
        levels = req.level.levels()
        est = max(
            estimate_work_minutes(lv, req.task_count, req.uud_focus, req.subject_id, req.grade)
            for lv in levels
        )
        if requested is not None and not (lo <= requested <= hi):
            fitting = self._fitting_counts(req, levels)
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.TIME_OUT_OF_RANGE,
                    severity=Severity.WARN,
                    message_ru=(
                        f"Запрошено {requested} мин, а диагностическая работа в этом прототипе рассчитана на "
                        f"{lo}–{hi} минут (чтение, ответы, проверка и рефлексия). Предлагаю вариант на {lo}–{hi} минут"
                        + (f": {fitting[0]} задания." if fitting else ".")
                    ),
                    suggestions=[f"{n} заданий" if n > 4 else f"{n} задания" for n in fitting[:3]],
                )
            )
        elif not (lo <= est <= hi):
            fitting = self._fitting_counts(req, levels)
            where = "больше" if est > hi else "меньше"
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.TIME_OUT_OF_RANGE,
                    severity=Severity.WARN,
                    message_ru=(
                        f"По расчёту работа займёт около {est:g} мин — это {where} диапазона {lo}–{hi} минут."
                        + (f" Уложится: {', '.join(map(str, fitting))} задани(я)." if fitting else "")
                    ),
                    suggestions=[str(n) for n in fitting[:3]],
                )
            )

        # Тема вне программы
        try:
            tc = self.knowledge.check_topic(req.grade, req.subject_id, req.topic)
            if not tc.in_program:
                issues.append(
                    GuardrailIssue(
                        code=GuardrailCode.TOPIC_NOT_IN_PROGRAM,
                        severity=Severity.WARN,
                        message_ru=(
                            f"Тема «{req.topic}» не найдена в программе {req.grade} класса по предмету "
                            f"«{req.subject_name}». Можно продолжить или выбрать близкую тему."
                        ),
                        suggestions=tc.suggestions[:5],
                    )
                )
        except Exception:  # база может быть не готова — не блокируем
            pass

        # База без результатов для класса/предмета
        if not any(i.code == GuardrailCode.SUBJECT_UNKNOWN for i in issues):
            if not self.knowledge.get_outcomes(req.grade, req.subject_id, k=1):
                issues.append(
                    GuardrailIssue(
                        code=GuardrailCode.KNOWLEDGE_EMPTY,
                        severity=Severity.WARN,
                        message_ru=(
                            f"В нормативной базе пока нет планируемых результатов для {req.grade} класса по предмету "
                            f"«{req.subject_name}». Задания будут составлены, но карта соответствия ФГОС будет пустой."
                        ),
                    )
                )
        return GuardrailResult(issues=issues, estimated_minutes=est)

    def _fitting_counts(self, req: GenerationRequest, levels: list[Level]) -> list[int]:
        lo, hi = req.minutes_min, req.minutes_max
        out = []
        for n in range(1, self.settings.max_tasks + 1):
            ests = [estimate_work_minutes(lv, n, req.uud_focus, req.subject_id, req.grade) for lv in levels]
            if all(lo <= e <= hi for e in ests):
                out.append(n)
        return out

    # ------------------------------------------------------------ error analysis

    def check_analysis(self, req: ErrorAnalysisRequest) -> GuardrailResult:
        issues: list[GuardrailIssue] = []
        text = f"{req.topic}\n{req.description}"
        if DIAGNOSIS_RE.search(text):
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.DIAGNOSIS_REQUEST,
                    severity=Severity.BLOCK,
                    message_ru=(
                        "Я не ставлю диагнозов и не оцениваю личность, интеллект или мотивацию. Опишите, какую ошибку "
                        "делают дети в заданиях (что пишут, на каком шаге) — и я предложу гипотезы о причинах и приёмы."
                    ),
                )
            )
        return GuardrailResult(issues=issues)
