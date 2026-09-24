"""Разбор обезличенного ответа ученика (T06) и нейтральная фиксация рефлексии (T07).

Детерминированно, без LLM: фиксируем только то, что ЯВНО видно в записи.
Числовой ответ сам по себе не подтверждает планирование или самоконтроль.
"""

from __future__ import annotations

import re

from socrat.contracts import (
    LABELS_RU,
    ReflectionObservation,
    ResponseObservation,
    SubjectObservation,
    Task,
    TaskKind,
    UUDGroup,
    UUDObservation,
    UUDObservationItem,
)

from .mathcheck import fmt, numbers_in, parse_number

EXPLAIN = ("потому", "так как", "т.к", "значит", "поэтому", "чтобы", "ведь", "нужно", "надо", "получается")
CHECK = ("провер", "обратн", "сверил", "сверяю", "проверка")
PLAN = ("сначала", "потом", "затем", "план", "1)", "1.", "первым действием", "вторым действием")
ERROR_WORDS = (
    "ошибк",
    "неверно",
    "неправильно",
    "не сложить",
    "не складывать",
    "нужно умнож",
    "надо умнож",
    "исправ",
)
MODEL = ("схем", "групп", "отрез", "рисун", "таблиц", "краткая запись")
DIFFICULTY = (
    "трудно",
    "сложно",
    "не понял",
    "не поняла",
    "не получилось",
    "не получается",
    "страшно",
    "не знаю",
    "запутал",
    "тяжело",
)

_EXPR_RE = re.compile(r"\d+\s*[×x*·:/+\-−]\s*\d+")


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


class RuleResponseObserver:
    """Реализует socrat.contracts.ResponseObserver."""

    def observe(self, task: Task, response_text: str) -> ResponseObservation:
        text = (response_text or "").strip()
        low = text.lower().replace("ё", "е")
        nums = numbers_in(text)
        expected = parse_number(task.expected_answer)

        # ---- предметная часть
        answer_m = re.search(r"ответ\s*[:\-—]?\s*(-?\d+(?:[.,]\d+)?)", low)
        before_check = re.split(r"провер", low, maxsplit=1)[0]
        main_nums = numbers_in(before_check) or nums
        final = parse_number(answer_m.group(1)) if answer_m else (main_nums[-1] if main_nums else None)
        if not text:
            status, basis = SubjectObservation.NO_ANSWER, "Ответ не записан."
        elif expected is None:
            status = SubjectObservation.PARTIAL
            basis = "Ответ не числовой — автоматически правильность не определить, оцените по эталону в методичке."
        elif final == expected:
            status, basis = SubjectObservation.CORRECT, f"Итоговое число {fmt(final)} совпадает с ожидаемым."
        elif expected in nums:
            status = SubjectObservation.PARTIAL
            basis = (
                f"Верное число {fmt(expected)} встречается в записи, но итоговый ответ другой ({fmt(final)})."
            )
        else:
            status = SubjectObservation.ERROR
            basis = (
                f"Итоговое число {fmt(final)} не совпадает с ожидаемым ({task.expected_answer})."
                if final is not None
                else "В записи нет числового ответа."
            )

        # ---- УУД
        has_expr = bool(_EXPR_RE.search(text))
        words = len(re.findall(r"[а-яёa-z]{2,}", low))
        number_only = bool(text) and words == 0
        items: list[UUDObservationItem] = []
        required = {i.group: i for i in task.uud_indicators}
        for group in UUDGroup:
            ind = required.get(group)
            if ind is None:
                items.append(
                    UUDObservationItem(
                        group=group,
                        outcome_id="—",
                        status=UUDObservation.CANNOT_OBSERVE,
                        basis="Задание не требует этого действия явно — по нему нельзя судить.",
                    )
                )
                continue
            st, why = self._uud_status(group, task, low, has_expr, words)
            alts = []
            if st in (UUDObservation.NOT_SHOWN, UUDObservation.PARTIAL):
                alts = [
                    "Ребёнок мог выполнить действие устно или в уме — по записи это не видно.",
                    "Возможно, не понял, что требуется записать объяснение/проверку.",
                ]
            items.append(
                UUDObservationItem(
                    group=group, outcome_id=ind.outcome_id, status=st, basis=why, alternatives=alts
                )
            )

        shown = [i for i in items if i.status == UUDObservation.OBSERVED]
        if not text:
            summary = "Ответа нет. Наблюдений по учебным действиям нет."
        elif number_only:
            summary = (
                f"Предметно: {LABELS_RU[status.value]}. Записано только число — для выводов о выборе способа, "
                "планировании, проверке и объяснении недостаточно наблюдений. Это не значит, что действия не "
                "сформированы: можно уточнить устно."
            )
        else:
            seen = ", ".join(LABELS_RU[i.group.value].lower() for i in shown) or "нет"
            summary = f"Предметно: {LABELS_RU[status.value]}. Наблюдаемые в записи действия: {seen}."
        return ResponseObservation(
            task_id=task.task_id,
            response_text=text,
            subject_status=status,
            subject_basis=basis,
            extracted_answer=fmt(final) if final is not None else None,
            uud=items,
            summary_ru=summary,
        )

    @staticmethod
    def _uud_status(
        group: UUDGroup, task: Task, low: str, has_expr: bool, words: int
    ) -> tuple[UUDObservation, str]:
        if group == UUDGroup.COGNITIVE:
            if has_expr or _has(low, MODEL):
                return UUDObservation.OBSERVED, "Записано действие/модель, соответствующая условию."
            return (
                UUDObservation.NOT_SHOWN,
                "Выбор действия или модели в записи не показан (есть только результат).",
            )
        if group == UUDGroup.REGULATORY:
            if task.kind == TaskKind.FIND_ERROR:
                if _has(low, ERROR_WORDS) and has_expr:
                    return UUDObservation.OBSERVED, "Указана ошибка и записано исправление."
                if _has(low, ERROR_WORDS) or has_expr:
                    return UUDObservation.PARTIAL, "Есть либо указание на ошибку, либо исправление — не оба."
                return UUDObservation.NOT_SHOWN, "Поиск и исправление ошибки в записи не показаны."
            check = _has(low, CHECK)
            plan = _has(low, PLAN)
            if (check and len(_EXPR_RE.findall(low)) >= 2) or (plan and has_expr):
                return UUDObservation.OBSERVED, "В записи есть " + (
                    "проверка" if check else "план / порядок действий"
                ) + "."
            if check or plan:
                return UUDObservation.PARTIAL, "Проверка или план упомянуты, но не выполнены полностью."
            return UUDObservation.NOT_SHOWN, "План, проверка или исправление в записи не показаны."
        # communicative
        if _has(low, EXPLAIN) and words >= 4:
            return UUDObservation.OBSERVED, "Есть объяснение, связывающее данные, действие и ответ."
        if words >= 4:
            return UUDObservation.PARTIAL, "Есть слова, но связь данных, действия и ответа не объяснена."
        return UUDObservation.NOT_SHOWN, "Объяснения нет — только вычисления или число."

    def observe_reflection(self, question: str, answer_text: str) -> ReflectionObservation:
        text = (answer_text or "").strip()
        low = text.lower()
        if not text:
            note = "Ответ на вопрос рефлексии не записан. Это не повод для выводов."
            action = None
        elif _has(low, DIFFICULTY):
            note = (
                f"Ученик отметил затруднение: «{text[:200]}». Это его собственная оценка ситуации — "
                "сведения для беседы, а не оценка личности и не диагноз."
            )
            action = (
                "Спросить, на каком шаге было трудно и какая помощь пригодилась бы; при следующей работе "
                "можно предложить вариант с опорами."
            )
        else:
            note = f"Ответ ученика: «{text[:200]}». Фиксируется как есть, без оценки."
            action = "При желании уточнить устно, что помогло выбрать способ."
        return ReflectionObservation(
            question=question, answer_text=text, neutral_note=note, suggested_teacher_action=action
        )
