"""Сценарная LLM для офлайн-тестов ядра и тестов кейса: отвечает корректными черновиками
по уровню из промпта (математика, 3 класс, сюжет «полки и карточки» из учебных данных SK01).
"""

from __future__ import annotations

import json
import re

from socrat.contracts import LLMResponse, LLMUsage


def _uud(group, oid, trigger, action="действие", evidence="видно в записи"):
    return {"group": group, "outcome_id": oid, "trigger": trigger, "action": action, "evidence": evidence}


def _task(n, text, answer, steps, uud, support=None, subject=("P01",)):
    return {
        "number": n,
        "student_text": text,
        "support": support,
        "subject_goal": "Умножение и деление в пределах 100",
        "meta_goal": "Выбор действия, проверка, объяснение",
        "expected_answer": str(answer),
        "solution_steps": [{"text": f"{e} = {r}", "expression": e, "result": str(r)} for e, r in steps],
        "alternative_solutions": ["Сложение одинаковых слагаемых"],
        "subject_outcome_ids": list(subject),
        "uud": uud,
        "personal_orientation": "Практическая ситуация: раскладываем карточки для занятия.",
        "typical_errors": [
            {
                "description": "Сложил вместо умножения",
                "layer": "conceptual",
                "interpretation": "Возможно, не видит равные группы",
                "teacher_move": "Нарисовать группы",
            }
        ],
        "oral_questions": ["Как ты выбрал действие?"],
        "technique_ids": ["TECH-02"],
        "conducting_note": "Не подсказывать действие.",
    }


def variant_draft(level: str, a: int, b: int, taken: int) -> dict:
    total = a * b
    easy = level == "easy"
    sup = "Подсказка: отметь одинаковые группы и что нужно найти." if easy else None
    t1 = _task(
        1,
        f"На {a} полках лежит по {b} карточек. Сколько карточек всего? Запиши действие и объясни выбор.",
        total,
        [(f"{a}*{b}", total)],
        [_uud("cognitive", "C01", "Запиши действие"), _uud("communicative", "K01", "объясни выбор")],
        sup,
    )
    if level == "advanced":
        t2 = _task(
            2,
            f"{total} карточек разложили поровну на {a} полок. Сколько карточек на одной полке? "
            "Реши двумя способами и объясни, чем они отличаются.",
            b,
            [(f"{total}/{a}", b), (f"{b}*{a}", total)],
            [
                _uud("communicative", "K01", "объясни, чем они отличаются"),
                _uud("cognitive", "C01", "Реши двумя способами"),
            ],
        )
        t4 = _task(
            4,
            f"На {a} полках лежало по {b} карточек. Взяли {taken} карточек. Не вычисляя, запиши план "
            "решения и объясни порядок действий.",
            f"План: 1) {a}×{b}; 2) вычесть {taken}. Ответ {total - taken}",
            [(f"{a}*{b}", total), (f"{total}-{taken}", total - taken)],
            [
                _uud("regulatory", "R01", "запиши план решения"),
                _uud("communicative", "K01", "объясни порядок действий"),
            ],
            subject=("P02",),
        )
    else:
        t2 = _task(
            2,
            f"{total} карточек разложили поровну на {a} полки. Сколько карточек на одной полке? "
            "Запиши решение и проверку.",
            b,
            [(f"{total}/{a}", b), (f"{b}*{a}", total)],
            [_uud("regulatory", "R01", "Запиши решение и проверку")],
            sup,
        )
        t4 = _task(
            4,
            f"На {a} полках лежало по {b} карточек. Взяли {taken} карточки. Сколько осталось? "
            "Сначала запиши план, затем решение и проверку.",
            total - taken,
            [(f"{a}*{b}", total), (f"{total}-{taken}", total - taken), (f"{total - taken}+{taken}", total)],
            [
                _uud("regulatory", "R01", "Сначала запиши план, затем решение и проверку"),
                _uud("cognitive", "C01", "Сначала запиши план"),
            ],
            sup,
            subject=("P01", "P02"),
        )
    t3 = _task(
        3,
        f"В решении написано: «На {a} полках по {b} карточек, значит всего {a}+{b}={a + b}». "
        "Найди ошибку, объясни её и запиши верное решение.",
        total,
        [(f"{a}*{b}", total)],
        [_uud("regulatory", "R01", "Найди ошибку"), _uud("communicative", "K01", "объясни её")],
        sup,
    )
    return {
        "title": "Умножение и деление",
        "tasks": [t1, t2, t3, t4],
        "reflection_questions": [
            "Какой способ ты выбрал и почему?",
            "Где ты проверил ответ?",
            "Что было трудно и какая помощь пригодилась бы?",
        ],
        "level_rationale": {
            "easy": "Есть подсказки.",
            "basic": "Без подсказок.",
            "advanced": "Нужно объяснять и проверять самому.",
        }[level],
    }


DEFAULT_ANALYSIS = {
    "summary": "Вероятно, формулы заучены отдельно от смысла величин: периметр и площадь не связаны с границей и внутренней частью фигуры.",
    "hypotheses": [
        {
            "layer": "conceptual",
            "uud_group": "cognitive",
            "statement": "Разрыв между формулой и смыслом величины.",
            "confidence": "medium",
            "signs_to_check": ["Может ли ребёнок показать периметр и площадь на рисунке?"],
        },
        {
            "layer": "regulatory",
            "uud_group": "regulatory",
            "statement": "Нет вопроса-проверки «что я измеряю?».",
            "confidence": "low",
            "signs_to_check": ["Есть ли единицы измерения в ответе?"],
        },
    ],
    "outcome_ids": ["C01", "NOPE-1"],
    "techniques": [
        {
            "technique_id": "TECH-11",
            "how_to_apply": "Разобрать решение «Петя нашёл площадь, чтобы купить плинтус».",
            "expected_shift": "Сверяют вопрос задачи с величиной.",
        },
        {"technique_id": "TECH-99", "how_to_apply": "выдуманный приём", "expected_shift": "—"},
    ],
    "quick_checks": [
        {
            "student_text": "Нужно обшить края коврика 3 дм на 2 дм лентой. Что найти?",
            "expected_answer": "Периметр: 10 дм",
            "what_it_checks": "Выбор величины по смыслу",
        }
    ],
    "teacher_reflection_question": "В каких типах задач ошибка встречается чаще?",
}


LEVEL_NUMS = {"лёгкий": ("easy", 3, 4, 2), "базовый": ("basic", 4, 6, 5), "сложный": ("advanced", 7, 8, 9)}


class ScriptedLLM:
    """Отвечает корректным черновиком по уровню из промпта. `broken` — первые ответы для конкретных уровней."""

    def __init__(self, broken: dict[str, dict] | None = None, analysis: dict | None = None):
        self.broken = dict(broken or {})
        self.analysis = analysis
        self.calls: list[dict] = []

    async def complete(self, system, user, *, json_schema=None, temperature=None, max_tokens=None):
        self.calls.append({"system": system, "user": user})
        if "Педагог описал ТИПИЧНУЮ ошибку" in user:
            data = self.analysis or DEFAULT_ANALYSIS
        elif user.startswith("Автоматическая проверка нашла ошибки"):
            tasks = json.loads(user.split("## Задания для исправления (JSON)")[1].split("Верни JSON")[0])
            level = getattr(self, "_last_level", "basic")
            good = {t["number"]: t for t in variant_draft(*self._params(level))["tasks"]}
            data = {"tasks": [good[t["number"]] for t in tasks]}
        else:
            m = re.search(r"Составь (\w+) вариант", user)
            key = m.group(1) if m else "базовый"
            level, a, b, taken = LEVEL_NUMS[key]
            self._last_level = level
            n = int(re.search(r"ровно (\d+)", user).group(1))
            data = self.broken.pop(level, None) or variant_draft(level, a, b, taken)
            data = dict(data, tasks=data["tasks"][:n])
        text = json.dumps(data, ensure_ascii=False)
        return LLMResponse(
            text=text,
            data=data,
            usage=LLMUsage(model="scripted", tokens_in=1000, tokens_out=500, cost_usd=0.0001),
        )

    @staticmethod
    def _params(level):
        for _k, (lv, a, b, t) in LEVEL_NUMS.items():
            if lv == level:
                return lv, a, b, t
        raise KeyError(level)

    async def embed(self, texts):
        raise NotImplementedError
