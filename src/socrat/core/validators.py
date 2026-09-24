"""Проверки кодом (принцип P1: LLM пишет — код проверяет).

Каждая проверка задания возвращает список ошибок-строк для цикла починки и
заполняет task.checks. Ошибки формулируются так, чтобы их можно было отдать LLM
как инструкцию «что исправить».
"""

from __future__ import annotations

import logging
import re
from fractions import Fraction
from functools import lru_cache

from socrat.contracts import Level, Task, TaskKind, UUDGroup, Variant

from .mathcheck import MathError, answer_value, evaluate, fmt, numbers_in, operands, parse_number
from .specs import KINDS, UUD_TRIGGER_WORDS, Slot

logger = logging.getLogger(__name__)

# Слова, которые НЕЛЬЗЯ показывать ребёнку (ярлыки уровня/способностей).
STUDENT_FORBIDDEN = (
    "лёгкий вариант",
    "легкий вариант",
    "сложный вариант",
    "базовый вариант",
    "для слабых",
    "для сильных",
    "отстающ",
    "одарённ",
    "одаренн",
    "уровень сложности",
)
# Слова-диагнозы и оценки личности — запрещены везде.
DIAGNOSIS_WORDS = (
    "сдвг",
    "дислекси",
    "дисграфи",
    "дискальку",
    "зпр",
    "задержк психическ",
    "умственно отстал",
    "ленив",
    "неспособн",
    "глуп",
    "тупой",
    "бестолков",
    "аутизм",
    "олигофрен",
)
NON_NUMERIC_KINDS = {TaskKind.PLAN_FIRST, TaskKind.INVERSE, TaskKind.OPEN, TaskKind.COMPARE, TaskKind.EXPLAIN}
SMALL_CONSTANTS = {Fraction(x) for x in (0, 1, 2, 10, 100, 1000, 60, 24, 7, 12)}


def _norm(s: str | None) -> str:
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"[«»\"'“”„]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def student_fields(task: Task) -> str:
    return f"{task.student_text}\n{task.support or ''}"


# --------------------------------------------------------------------------- math


def check_math(task: Task, *, is_math: bool, limit: int | None, primary: bool) -> list[str]:
    """Проверяет каждый шаг решения вычислителем; ставит verified; сверяет итог с ответом."""
    errors: list[str] = []
    steps_with_expr = [s for s in task.solution_steps if s.expression]
    if not is_math:
        task.checks.math_verified = None
        return errors
    expected = answer_value(task.expected_answer)
    numeric_expected = expected is not None and task.kind not in NON_NUMERIC_KINDS
    if numeric_expected and not steps_with_expr:
        errors.append(
            f"Задание {task.number}: у ответа «{task.expected_answer}» нет вычислений — заполни "
            "solution_steps[].expression арифметическими выражениями."
        )
    known = set(numbers_in(student_fields(task))) | SMALL_CONSTANTS
    last_value: Fraction | None = None
    known_results: set[Fraction] = set()
    ok = True
    for i, step in enumerate(task.solution_steps, start=1):
        if not step.expression:
            continue
        try:
            value = evaluate(step.expression)
        except MathError as e:
            errors.append(f"Задание {task.number}, шаг {i}: не удалось вычислить «{step.expression}» ({e}).")
            step.verified = False
            ok = False
            continue
        stated = parse_number(step.result)
        if stated is None:
            step.result = fmt(value)
        elif stated != value:
            errors.append(
                f"Задание {task.number}, шаг {i}: {step.expression} = {fmt(value)}, а записано {step.result}. "
                "Исправь результат и все зависящие от него числа."
            )
            step.verified = False
            ok = False
            continue
        for op in operands(step.expression):
            if op not in known:
                errors.append(
                    f"Задание {task.number}, шаг {i}: число {fmt(op)} не встречается в условии и не получено "
                    "в предыдущих шагах. Решение должно опираться на данные задания."
                )
                ok = False
                break
        known.add(value)
        known_results.add(value)
        if primary and (value < 0 or value.denominator != 1):
            errors.append(
                f"Задание {task.number}, шаг {i}: результат {fmt(value)} — в начальной школе используй "
                "только целые неотрицательные числа (деление нацело)."
            )
            ok = False
        if limit is not None and abs(value) > limit:
            errors.append(
                f"Задание {task.number}, шаг {i}: результат {fmt(value)} больше предела темы ({limit})."
            )
            ok = False
        step.verified = step.verified if step.verified is False else True
        last_value = value
    if limit is not None:
        for n in numbers_in(task.student_text):
            if n > limit:
                errors.append(
                    f"Задание {task.number}: число {fmt(n)} в условии больше предела темы ({limit})."
                )
                ok = False
                break
    if numeric_expected and last_value is not None and expected not in known_results:
        errors.append(
            f"Задание {task.number}: ответ «{task.expected_answer}» не получается ни в одном шаге решения "
            f"(последний шаг даёт {fmt(last_value)})."
        )
        ok = False
    task.checks.math_verified = ok if steps_with_expr else (None if not numeric_expected else False)
    return errors


# --------------------------------------------------------------------------- leak


def check_leak(task: Task) -> list[str]:
    """Ответ/решение не должны быть видны ребёнку."""
    errors: list[str] = []
    visible = student_fields(task)
    vis_norm = re.sub(r"\s+", "", visible).replace("×", "*").replace("·", "*").replace(":", "/")
    expected = answer_value(task.expected_answer)
    if expected is not None and task.kind != TaskKind.FIND_ERROR:
        e = re.escape(fmt(expected))
        if re.search(rf"=\s*{e}(?![\d,.])", visible) or re.search(
            rf"ответ\s*[:\-—]?\s*{e}(?![\d,.])", visible, re.I
        ):
            errors.append(
                f"Задание {task.number}: в тексте для ученика виден ответ {fmt(expected)} — убери его."
            )
    if task.support:
        sup = re.sub(r"\s+", "", task.support).replace("×", "*").replace("·", "*").replace(":", "/")
        for s in task.solution_steps:
            if s.expression and len(s.expression) >= 3:
                ex = re.sub(r"\s+", "", s.expression).replace("×", "*").replace(":", "/")
                if ex in sup:
                    errors.append(
                        f"Задание {task.number}: подсказка содержит готовое действие «{s.expression}» — "
                        "подсказка должна направлять, а не решать."
                    )
                    break
    task.checks.answer_leak_free = not errors
    _ = vis_norm
    return errors


# --------------------------------------------------------------------------- UUD


def trigger_ok(group: UUDGroup, trigger: str, visible: str) -> tuple[bool, str]:
    t, v = _norm(trigger), _norm(visible)
    if not t:
        return False, "пустой trigger"
    if t not in v:
        return False, "trigger не найден дословно в формулировке"
    if not any(w in t for w in UUD_TRIGGER_WORDS[group]):
        return False, "в trigger нет требования, делающего это действие видимым"
    return True, ""


def check_uud(task: Task, slot: Slot | None) -> list[str]:
    """Снимает индикаторы без основания; требует целевую группу слота."""
    errors: list[str] = []
    visible = student_fields(task)
    kept = []
    for ind in task.uud_indicators:
        ok, why = trigger_ok(ind.group, ind.trigger, visible)
        if ok and ind.group in KINDS[task.kind].groups:
            kept.append(ind)
        else:
            task.checks.notes.append(
                f"Снят признак «{ind.group.value}»: {why or 'тип задания не позволяет это наблюдать'}."
            )
    task.uud_indicators = kept
    if slot is not None:
        present = {i.group for i in kept}
        if slot.target not in present:
            errors.append(
                f"Задание {task.number}: не видно действия группы «{slot.target.value}». Добавь в student_text "
                f"явное требование вроде «{slot.trigger_hint}» и укажи в uud trigger — дословную цитату."
            )
    task.checks.uud_grounded = not errors
    return errors


# --------------------------------------------------------------------------- language


def check_language(task: Task) -> list[str]:
    errors: list[str] = []
    low = _norm(student_fields(task))
    for w in STUDENT_FORBIDDEN:
        if w in low:
            errors.append(f"Задание {task.number}: в тексте для ученика есть ярлык «{w}» — убери.")
    teacher_text = _norm(
        " ".join(
            [task.conducting_note, task.personal_orientation or ""]
            + [e.interpretation for e in task.typical_errors]
        )
    )
    for w in DIAGNOSIS_WORDS:
        if re.search(rf"\b{w}", low) or re.search(rf"\b{w}", teacher_text):
            errors.append(
                f"Задание {task.number}: недопустимая формулировка «{w}» (диагноз/оценка личности) — убери."
            )
    return errors


@lru_cache(maxsize=1)
def _morph():
    try:
        import pymorphy3

        return pymorphy3.MorphAnalyzer()
    except Exception:  # pragma: no cover - нет словарей
        logger.warning("pymorphy3 unavailable; numeral agreement disabled")
        return None


_PREPS = {
    "в",
    "во",
    "на",
    "у",
    "из",
    "от",
    "до",
    "с",
    "со",
    "к",
    "ко",
    "о",
    "об",
    "за",
    "под",
    "над",
    "при",
    "без",
    "для",
    "про",
    "через",
    "между",
}


def fix_numeral_agreement(text: str) -> str:
    """«4 карточек» → «4 карточки», «5 карточки» → «5 карточек». Не трогает случаи после предлогов."""
    morph = _morph()
    if morph is None or not text:
        return text

    def repl(m: re.Match) -> str:
        before, num_s, word = m.group(1), m.group(2), m.group(3)
        keep = m.group(0)
        if before and before.lower() in _PREPS:
            return keep
        num = int(num_s)
        parses = [p for p in morph.parse(word) if "NOUN" in p.tag and p.tag.case in {"nomn", "gent", "accs"}]
        if not parses:
            return keep
        p = parses[0]
        if p.tag.number == "sing" and p.tag.case == "nomn" and num % 10 != 1:
            return keep  # «3 класс», «Задание 2» — порядковое употребление, не трогаем
        base = p.inflect({"nomn", "sing"}) or p
        agreed = base.make_agree_with_number(num)
        if agreed is None or agreed.word == word.lower():
            return keep
        new = agreed.word
        if word[:1].isupper():
            new = new[:1].upper() + new[1:]
        return f"{before} {num_s} {new}" if before else f"{num_s} {new}"

    return re.sub(r"(?:(\b[А-Яа-яЁё]+)\s)?(\d+)\s([А-Яа-яЁё]{3,})", repl, text)


# --------------------------------------------------------------------------- level (T09)


def check_levels(variants: list[Variant]) -> list[str]:
    """Уровни должны отличаться опорами и самостоятельностью, а не только объёмом."""
    warnings: list[str] = []
    by = {v.level: v for v in variants}
    easy, adv, basic = by.get(Level.EASY), by.get(Level.ADVANCED), by.get(Level.BASIC)
    if easy:
        with_support = sum(1 for t in easy.tasks if t.support)
        if with_support * 2 < len(easy.tasks):
            warnings.append("В лёгком варианте опоры есть меньше чем у половины заданий.")
            for t in easy.tasks:
                t.checks.level_consistent = False
    if basic:
        hints = [t for t in basic.tasks if t.support and "подсказ" in t.support.lower()]
        if hints:
            warnings.append("В базовом варианте есть подсказки — уровень ближе к лёгкому.")
    if adv:
        independent = sum(
            1
            for t in adv.tasks
            if any(i.group in (UUDGroup.COMMUNICATIVE, UUDGroup.REGULATORY) for i in t.uud_indicators)
        )
        if independent * 2 < len(adv.tasks):
            warnings.append(
                "В сложном варианте мало требований к объяснению и проверке — отличие может быть только в объёме."
            )
            for t in adv.tasks:
                t.checks.level_consistent = False
    if easy and adv:
        emax = max((n for t in easy.tasks for n in numbers_in(t.student_text)), default=0)
        amax = max((n for t in adv.tasks for n in numbers_in(t.student_text)), default=0)
        if amax and emax and amax < emax:
            warnings.append("В сложном варианте числа проще, чем в лёгком.")
    return warnings
