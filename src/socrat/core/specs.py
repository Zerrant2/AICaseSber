"""Справочники ядра: типы заданий, уровни, пределы чисел, триггеры УУД.

Всё, что определяет «диагностическую механику», задаётся здесь кодом, а не LLM:
чертёж работы (какие типы заданий и какие УУД) строится детерминированно.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from socrat.contracts import Level, TaskKind, UUDFocus, UUDGroup

# Предметы, где решения проверяются арифметикой кодом.
MATH_SUBJECTS = {"math", "algebra", "geometry", "probability", "physics", "chemistry", "informatics"}

# Ключевые слова, при наличии которых в ФОРМУЛИРОВКЕ задания группа УУД наблюдаема.
UUD_TRIGGER_WORDS: dict[UUDGroup, tuple[str, ...]] = {
    UUDGroup.COGNITIVE: (
        "схем",
        "модел",
        "выбер",
        "сравни",
        "сравн",
        "какое действие",
        "краткую запись",
        "нарисуй",
        "выдели",
        "определи",
        "составь задачу",
        "составь условие",
        "таблиц",
        "классифиц",
        "найди общее",
        "чем похожи",
        "чем отлича",
        "запиши действие",
        "запиши выражение",
        "запиши решение",
    ),
    UUDGroup.REGULATORY: (
        "план",
        "провер",
        "ошибк",
        "исправ",
        "сначала",
        "порядок",
        "прикинь",
        "оцени",
        "сверь",
        "по шагам",
        "последовательн",
    ),
    UUDGroup.COMMUNICATIVE: (
        "объясни",
        "объясн",
        "докажи",
        "обоснуй",
        "почему",
        "поясни",
        "расскажи",
        "аргумент",
    ),
}

# Готовые фразы-триггеры, которые генератор просит вставить в формулировку.
TRIGGER_PHRASES: dict[UUDGroup, list[str]] = {
    UUDGroup.COGNITIVE: ["Выбери действие и запиши его", "Сделай схему или краткую запись", "Сравни"],
    UUDGroup.REGULATORY: ["Сначала запиши план", "Запиши проверку", "Найди ошибку и исправь её"],
    UUDGroup.COMMUNICATIVE: ["Объясни, почему", "Докажи, что ответ верный"],
}


@dataclass(frozen=True)
class KindSpec:
    kind: TaskKind
    title_ru: str
    groups: tuple[UUDGroup, ...]  # какие группы УУД этот тип ДОПУСКАЕТ
    base_minutes: float
    how: str  # подсказка LLM, как строить задание такого типа
    techniques: tuple[str, ...] = ()


KINDS: dict[TaskKind, KindSpec] = {
    k.kind: k
    for k in [
        KindSpec(
            TaskKind.COMPUTE,
            "Вычисление / ответ на вопрос",
            (UUDGroup.COGNITIVE, UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE),
            1.5,
            "Короткое предметное задание на ключевое умение темы.",
            ("TECH-02", "TECH-09"),
        ),
        KindSpec(
            TaskKind.WORD_PROBLEM,
            "Текстовая задача в одно действие",
            (UUDGroup.COGNITIVE, UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE),
            2.5,
            "Жизненная ситуация; ученик выбирает действие по смыслу условия.",
            ("TECH-02",),
        ),
        KindSpec(
            TaskKind.MULTI_STEP,
            "Составная задача (2 действия)",
            (UUDGroup.COGNITIVE, UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE),
            3.5,
            "Задача в два действия; явно просить сначала план, потом решение и проверку.",
            ("TECH-13", "TECH-05"),
        ),
        KindSpec(
            TaskKind.MODEL_CHOICE,
            "Выбор модели / схемы",
            (UUDGroup.COGNITIVE, UUDGroup.COMMUNICATIVE),
            2.5,
            "Дать 2–3 схемы или выражения; ученик выбирает подходящее к условию и объясняет.",
            ("TECH-04",),
        ),
        KindSpec(
            TaskKind.FIND_ERROR,
            "«Лови ошибку»",
            (UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE, UUDGroup.COGNITIVE),
            3.0,
            "Показать чужое решение с ОДНОЙ типичной ошибкой; ученик находит, объясняет и исправляет.",
            ("TECH-01", "TECH-11"),
        ),
        KindSpec(
            TaskKind.TWO_WAYS,
            "Двумя способами",
            (UUDGroup.COGNITIVE, UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE),
            3.5,
            "Решить или проверить двумя способами и сравнить их.",
            ("TECH-04", "TECH-08"),
        ),
        KindSpec(
            TaskKind.PLAN_FIRST,
            "«План до действия»",
            (UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE),
            3.0,
            "Не вычисляя, записать план (порядок действий) и объяснить порядок.",
            ("TECH-13", "TECH-14"),
        ),
        KindSpec(
            TaskKind.EXPLAIN,
            "Объясни способ",
            (UUDGroup.COMMUNICATIVE, UUDGroup.COGNITIVE),
            2.5,
            "Выполнить и объяснить, почему выбран такой способ; в лёгком уровне дать начало фразы.",
            ("TECH-07", "TECH-09"),
        ),
        KindSpec(
            TaskKind.INVERSE,
            "«Обратная задача»",
            (UUDGroup.COGNITIVE, UUDGroup.COMMUNICATIVE),
            3.5,
            "По выражению или ответу составить условие задачи.",
            ("TECH-06",),
        ),
        KindSpec(
            TaskKind.ESTIMATE,
            "Прикинь результат",
            (UUDGroup.REGULATORY, UUDGroup.COGNITIVE),
            1.5,
            "До вычисления оценить, каким будет ответ (больше/меньше, примерно), затем проверить.",
            ("TECH-03",),
        ),
        KindSpec(
            TaskKind.COMPARE,
            "Сравнение",
            (UUDGroup.COGNITIVE, UUDGroup.COMMUNICATIVE),
            3.0,
            "Сравнить два объекта/решения, назвать сходство и различие.",
            ("TECH-04",),
        ),
        KindSpec(
            TaskKind.OPEN,
            "Открытый ответ",
            (UUDGroup.COGNITIVE, UUDGroup.COMMUNICATIVE, UUDGroup.REGULATORY),
            3.5,
            "Короткий развёрнутый ответ по тексту/ситуации (гуманитарные предметы).",
            ("TECH-07",),
        ),
    ]
}


@dataclass(frozen=True)
class LevelSpec:
    level: Level
    title_ru: str
    support_rule: str
    independence_rule: str
    number_rule: str
    time_factor: float
    instruction: str


LEVELS: dict[Level, LevelSpec] = {
    Level.EASY: LevelSpec(
        Level.EASY,
        "лёгкий",
        support_rule="У КАЖДОГО задания есть опора в поле support: подсказка, на что обратить внимание, начало записи или фразы. Опора НЕ раскрывает ответ и готовое действие.",
        independence_rule="Требования к объяснению и проверке — короткие, с образцом начала («Я выбрал …, потому что …»).",
        number_rule="Самые простые числа/примеры темы; меньше данных в условии.",
        time_factor=0.85,
        instruction="Покажи решение и поясни свой способ. Подсказки помогут. Если что-то не получилось, напиши, на каком шаге возник вопрос.",
    ),
    Level.BASIC: LevelSpec(
        Level.BASIC,
        "базовый",
        support_rule="Подсказок нет (support = null). Все необходимые данные есть в условии.",
        independence_rule="Ученик сам записывает решение и проверку; объяснение — своими словами.",
        number_rule="Типичные для темы числа и данные.",
        time_factor=1.0,
        instruction="Покажи решение и поясни свой способ. Если что-то не получилось, напиши, на каком шаге возник вопрос.",
    ),
    Level.ADVANCED: LevelSpec(
        Level.ADVANCED,
        "сложный",
        support_rule="Подсказок нет (support = null). Если нужно, в support можно поставить ДОПОЛНИТЕЛЬНОЕ требование (напр., «Проверь ответ другим способом»), но не подсказку.",
        independence_rule="Больше самостоятельности: самому составить план, обосновать выбор способа, проверить другим способом, найти ошибку без указания места. Различие — в самостоятельности, а не в объёме текста.",
        number_rule="Более крупные числа или лишнее/недостающее данное в пределах той же темы; НЕ выходить за программу класса.",
        time_factor=1.05,
        instruction="Реши задания, объясни свой выбор и проверь себя. Если что-то не получилось, напиши, на каком шаге возник вопрос.",
    ),
}

DEFAULT_REFLECTION = [
    "Какой способ ты выбрал и почему?",
    "Где ты проверил ответ или исправил ошибку?",
    "Что было трудно и какая помощь могла бы пригодиться?",
]

READING_MINUTES = 1.0
REFLECTION_MINUTES = 3.0

# Скорость чтения (слов/мин) по классам — для расчёта времени на чтение условия.
READING_WPM = {1: 25, 2: 40, 3: 55, 4: 70}
DEFAULT_WPM = 90


def number_limit(grade: int, subject_id: str, topic: str) -> int | None:
    """Верхний предел чисел для математики начальной школы (по ФРП). None — без ограничения."""
    if subject_id != "math" or grade > 4:
        return None
    base = {1: 20, 2: 100, 3: 1000, 4: 1_000_000}[grade]
    m = re.search(r"в пределах\s+(\d[\d\s]*)", topic.lower())
    if m:
        try:
            return min(base, int(m.group(1).replace(" ", "")))
        except ValueError:
            return base
    if grade == 3 and re.search(r"табличн|умножени|делени", topic.lower()) and "внетабл" not in topic.lower():
        return 100
    return base


# ------------------------------------------------------------------ чертёж работы


@dataclass
class Slot:
    number: int
    kind: TaskKind
    target: UUDGroup  # группа УУД, которую ОБЯЗАТЕЛЬНО сделать наблюдаемой
    trigger_hint: str  # фраза, которую нужно включить в формулировку
    extra_groups: list[UUDGroup] = field(default_factory=list)

    def describe(self) -> str:
        k = KINDS[self.kind]
        return (
            f"Задание {self.number}: тип «{k.title_ru}» ({self.kind.value}). {k.how} "
            f"Обязательно сделать наблюдаемым: {self.target.value} — включи в формулировку требование вроде «{self.trigger_hint}»."
        )


# Базовые последовательности (для 4 заданий повторяют логику учебного примера кейса:
# задача → обратное действие с проверкой → «найди ошибку» → составная с планом).
_SEQ_MATH = {
    Level.EASY: [
        TaskKind.WORD_PROBLEM,
        TaskKind.WORD_PROBLEM,
        TaskKind.FIND_ERROR,
        TaskKind.MULTI_STEP,
        TaskKind.EXPLAIN,
        TaskKind.ESTIMATE,
        TaskKind.MODEL_CHOICE,
        TaskKind.COMPUTE,
    ],
    Level.BASIC: [
        TaskKind.WORD_PROBLEM,
        TaskKind.WORD_PROBLEM,
        TaskKind.FIND_ERROR,
        TaskKind.MULTI_STEP,
        TaskKind.MODEL_CHOICE,
        TaskKind.ESTIMATE,
        TaskKind.EXPLAIN,
        TaskKind.COMPUTE,
    ],
    Level.ADVANCED: [
        TaskKind.WORD_PROBLEM,
        TaskKind.TWO_WAYS,
        TaskKind.FIND_ERROR,
        TaskKind.PLAN_FIRST,
        TaskKind.INVERSE,
        TaskKind.MODEL_CHOICE,
        TaskKind.ESTIMATE,
        TaskKind.EXPLAIN,
    ],
}
_SEQ_OTHER = {
    Level.EASY: [
        TaskKind.COMPUTE,
        TaskKind.MODEL_CHOICE,
        TaskKind.FIND_ERROR,
        TaskKind.EXPLAIN,
        TaskKind.COMPARE,
        TaskKind.OPEN,
        TaskKind.PLAN_FIRST,
        TaskKind.COMPUTE,
    ],
    Level.BASIC: [
        TaskKind.COMPUTE,
        TaskKind.COMPARE,
        TaskKind.FIND_ERROR,
        TaskKind.EXPLAIN,
        TaskKind.MODEL_CHOICE,
        TaskKind.OPEN,
        TaskKind.PLAN_FIRST,
        TaskKind.COMPUTE,
    ],
    Level.ADVANCED: [
        TaskKind.OPEN,
        TaskKind.COMPARE,
        TaskKind.FIND_ERROR,
        TaskKind.PLAN_FIRST,
        TaskKind.TWO_WAYS,
        TaskKind.INVERSE,
        TaskKind.EXPLAIN,
        TaskKind.MODEL_CHOICE,
    ],
}

_GROUP_CYCLE = [UUDGroup.COGNITIVE, UUDGroup.REGULATORY, UUDGroup.COMMUNICATIVE]


def _best_group(kind: TaskKind, wanted: UUDGroup) -> UUDGroup:
    groups = KINDS[kind].groups
    return wanted if wanted in groups else groups[0]


def build_blueprint(level: Level, task_count: int, focus: UUDFocus, subject_id: str) -> list[Slot]:
    """Детерминированный чертёж: типы заданий и группы УУД, которые должны стать наблюдаемыми.

    balanced — каждая группа встречается хотя бы раз (при task_count ≥ 3, с учётом доп. групп);
    фокус на группе — она целевая в большинстве заданий (первое задание — предметная «разминка»).
    """
    seq = (_SEQ_MATH if subject_id in MATH_SUBJECTS else _SEQ_OTHER)[level]
    kinds = [seq[i % len(seq)] for i in range(task_count)]
    targets = [_DEFAULT_TARGET[k] for k in kinds]
    extras: list[list[UUDGroup]] = [list(_DEFAULT_EXTRA.get(k, [])) for k in kinds]

    if focus == UUDFocus.BALANCED:
        # второе задание математики (обратное действие) — регулятивное: «запиши проверку»
        if subject_id in MATH_SUBJECTS and task_count >= 2 and kinds[1] == TaskKind.WORD_PROBLEM:
            targets[1] = UUDGroup.REGULATORY
        if task_count >= 3:
            for g in _GROUP_CYCLE:
                present = set(targets) | {x for e in extras for x in e}
                if g in present:
                    continue
                for i in reversed(range(task_count)):
                    if g in KINDS[kinds[i]].groups and targets.count(targets[i]) > 1:
                        targets[i] = g
                        break
                else:
                    for i in reversed(range(task_count)):
                        if g in KINDS[kinds[i]].groups:
                            extras[i].append(g)
                            break
    else:
        wanted = UUDGroup(focus.value)
        for i in range(task_count):
            if i == 0 and task_count > 2:
                continue  # первое задание — предметная «разминка»
            targets[i] = _best_group(kinds[i], wanted)

    slots: list[Slot] = []
    for i, (k, g) in enumerate(zip(kinds, targets, strict=True), start=1):
        extra = [x for x in extras[i - 1] if x != g]
        hint = _SPECIAL_HINTS.get((k, g)) or TRIGGER_PHRASES[g][(i - 1) % len(TRIGGER_PHRASES[g])]
        slots.append(Slot(number=i, kind=k, target=g, trigger_hint=hint, extra_groups=extra))
    return slots


_DEFAULT_TARGET = {
    TaskKind.WORD_PROBLEM: UUDGroup.COGNITIVE,
    TaskKind.COMPUTE: UUDGroup.COGNITIVE,
    TaskKind.MODEL_CHOICE: UUDGroup.COGNITIVE,
    TaskKind.COMPARE: UUDGroup.COGNITIVE,
    TaskKind.INVERSE: UUDGroup.COGNITIVE,
    TaskKind.OPEN: UUDGroup.COGNITIVE,
    TaskKind.FIND_ERROR: UUDGroup.REGULATORY,
    TaskKind.MULTI_STEP: UUDGroup.REGULATORY,
    TaskKind.PLAN_FIRST: UUDGroup.REGULATORY,
    TaskKind.ESTIMATE: UUDGroup.REGULATORY,
    TaskKind.EXPLAIN: UUDGroup.COMMUNICATIVE,
    TaskKind.TWO_WAYS: UUDGroup.COMMUNICATIVE,
}
_DEFAULT_EXTRA = {
    TaskKind.FIND_ERROR: [UUDGroup.COMMUNICATIVE],
    TaskKind.TWO_WAYS: [UUDGroup.COGNITIVE],
    TaskKind.MULTI_STEP: [UUDGroup.COGNITIVE],
    TaskKind.MODEL_CHOICE: [UUDGroup.COMMUNICATIVE],
}
_SPECIAL_HINTS = {
    (TaskKind.FIND_ERROR, UUDGroup.REGULATORY): "Найди ошибку, объясни её и запиши верное решение",
    (TaskKind.MULTI_STEP, UUDGroup.REGULATORY): "Сначала запиши план, затем решение и проверку",
    (TaskKind.TWO_WAYS, UUDGroup.COMMUNICATIVE): "Реши двумя способами и объясни, чем они отличаются",
    (TaskKind.PLAN_FIRST, UUDGroup.REGULATORY): "Не вычисляя, запиши план решения и объясни порядок действий",
    (TaskKind.WORD_PROBLEM, UUDGroup.REGULATORY): "Запиши решение и проверку",
    (TaskKind.WORD_PROBLEM, UUDGroup.COGNITIVE): "Запиши действие и объясни выбор",
    (TaskKind.MODEL_CHOICE, UUDGroup.COGNITIVE): "Выбери подходящую схему или выражение и объясни выбор",
    (TaskKind.ESTIMATE, UUDGroup.REGULATORY): "Сначала прикинь ответ, затем вычисли и сверь",
}


def estimate_task_minutes(kind: TaskKind, level: Level, grade: int, words: int) -> float:
    """Расчёт времени на задание кодом (не LLM). Шаг 0.5 мин."""
    base = KINDS[kind].base_minutes * LEVELS[level].time_factor
    wpm = READING_WPM.get(grade, DEFAULT_WPM)
    minutes = base + words / wpm
    return max(1.0, round(minutes * 2) / 2)


def estimate_work_minutes(
    level: Level, task_count: int, focus: UUDFocus, subject_id: str, grade: int
) -> float:
    """Оценка до генерации (для предупреждений бота): средняя длина условия ~30 слов."""
    slots = build_blueprint(level, task_count, focus, subject_id)
    tasks = sum(estimate_task_minutes(s.kind, level, grade, 30) for s in slots)
    return READING_MINUTES + tasks + REFLECTION_MINUTES
