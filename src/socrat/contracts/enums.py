"""Перечисления контракта. Значения (value) — стабильные строковые коды, их можно
хранить в БД и передавать в callback_data бота. Русские подписи — в LABELS_RU.

ВЛАДЕЛЕЦ: Claude (архитектор). Менять только через PR с меткой `contract`.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Роль пользователя бота."""

    ADMIN = "admin"
    TEACHER = "teacher"  # педагог
    METHODIST = "methodist"  # методист
    PSYCHOLOGIST = "psychologist"  # педагог-психолог


class Level(StrEnum):
    """Уровень задания (НЕ оценка способностей ребёнка)."""

    EASY = "easy"
    BASIC = "basic"
    ADVANCED = "advanced"


class LevelChoice(StrEnum):
    """Что выбрал учитель в боте: один уровень или все три."""

    EASY = "easy"
    BASIC = "basic"
    ADVANCED = "advanced"
    ALL = "all"

    def levels(self) -> list[Level]:
        if self is LevelChoice.ALL:
            return [Level.EASY, Level.BASIC, Level.ADVANCED]
        return [Level(self.value)]


class UUDGroup(StrEnum):
    """Группы универсальных учебных действий (ФГОС)."""

    COGNITIVE = "cognitive"  # познавательные
    REGULATORY = "regulatory"  # регулятивные
    COMMUNICATIVE = "communicative"  # коммуникативные


class UUDFocus(StrEnum):
    """Какие УУД учитель хочет диагностировать в работе (шаг C1)."""

    COGNITIVE = "cognitive"
    REGULATORY = "regulatory"
    COMMUNICATIVE = "communicative"
    BALANCED = "balanced"


class OutcomeType(StrEnum):
    """Тип планируемого результата (ФГОС / ФРП)."""

    SUBJECT = "subject"  # предметный
    COGNITIVE = "cognitive"  # метапредметный: познавательные УУД
    REGULATORY = "regulatory"  # метапредметный: регулятивные УУД
    COMMUNICATIVE = "communicative"  # метапредметный: коммуникативные УУД
    PERSONAL = "personal"  # личностный (только направленность, без балла)


class PresentationMode(StrEnum):
    """Способ подачи. В MVP реально используется только TEXT."""

    TEXT = "text"
    VISUAL = "visual"
    PRACTICAL = "practical"


class TaskKind(StrEnum):
    """Тип задания — определяет, какие УУД в нём МОЖНО наблюдать.

    Библиотека типов: src/socrat/core/task_kinds.py (владелец Claude).
    """

    COMPUTE = "compute"  # вычислить / ответить (предметный навык)
    WORD_PROBLEM = "word_problem"  # текстовая задача в 1 действие
    MULTI_STEP = "multi_step"  # составная задача (2+ действия) с планом
    MODEL_CHOICE = "model_choice"  # выбрать схему/модель/действие к условию
    FIND_ERROR = "find_error"  # «Лови ошибку»: найти и исправить чужую ошибку
    TWO_WAYS = "two_ways"  # решить/проверить двумя способами, сравнить
    PLAN_FIRST = "plan_first"  # «План до действия»: составить план, не вычисляя
    EXPLAIN = "explain"  # объяснить способ/правило
    INVERSE = "inverse"  # «Обратная задача»: составить условие по решению
    ESTIMATE = "estimate"  # «Предвосхищение результата»: прикинуть ответ
    COMPARE = "compare"  # сравнить объекты/решения, найти различия
    OPEN = "open"  # открытый ответ (гуманитарные предметы)


class SubjectObservation(StrEnum):
    """Шкала наблюдения предметного результата (observation_rules.json кейса)."""

    CORRECT = "correct"  # верный ответ
    PARTIAL = "partial"  # частично выполнено
    ERROR = "error"  # ошибка
    NO_ANSWER = "no_answer"  # нет ответа


class UUDObservation(StrEnum):
    """Шкала наблюдения УУД (observation_rules.json кейса). Решение C2."""

    OBSERVED = "observed"  # действие наблюдается
    PARTIAL = "partial"  # действие наблюдается частично
    NOT_SHOWN = "not_shown"  # действие не показано
    CANNOT_OBSERVE = "cannot_observe"  # нет возможности наблюдать


class ErrorLayer(StrEnum):
    """Декомпозиция типичной ошибки (модуль «Анализ типичных ошибок»)."""

    OPERATIONAL = "operational"  # операциональный сбой (шаг алгоритма)
    CONCEPTUAL = "conceptual"  # концептуальное заблуждение (смысл действия)
    REGULATORY = "regulatory"  # регулятивный дефицит (нет самопроверки)
    COMMUNICATIVE = "communicative"  # не может объяснить / нет терминологии


class DocType(StrEnum):
    """Тип документа в нормативной базе."""

    FGOS = "fgos"
    FOP = "fop"  # федеральная образовательная программа
    FRP = "frp"  # федеральная рабочая программа по предмету
    CASE_REFERENCE = "case_reference"  # справочник кейса SK01 (outcomes_reference.json)
    SCHOOL_MATERIAL = "school_material"  # загружено школой; НЕ нормативный источник


class EduLevel(StrEnum):
    """Уровень общего образования."""

    NOO = "noo"  # начальное, 1–4
    OOO = "ooo"  # основное, 5–9
    SOO = "soo"  # среднее, 10–11

    @staticmethod
    def for_grade(grade: int) -> EduLevel:
        if 1 <= grade <= 4:
            return EduLevel.NOO
        if 5 <= grade <= 9:
            return EduLevel.OOO
        if 10 <= grade <= 11:
            return EduLevel.SOO
        raise ValueError(f"grade must be 1..11, got {grade}")


class ChunkKind(StrEnum):
    """Смысловой тип фрагмента нормативного документа (для фильтров поиска)."""

    SUBJECT_RESULT = "subject_result"  # предметные результаты по классу
    META_RESULT = "meta_result"  # метапредметные результаты (УУД)
    PERSONAL_RESULT = "personal_result"  # личностные результаты
    CONTENT = "content"  # содержание обучения (темы) по классу
    GENERAL = "general"  # прочее (пояснительная записка, требования и т.п.)


class Rating(StrEnum):
    """Обратная связь учителя по заданию (D3)."""

    UP = "up"
    DOWN = "down"


class GuardrailCode(StrEnum):
    """Коды срабатывания ограничителей (для тестов T05, T08, T10, T12 и бота)."""

    OK = "ok"
    GRADE_OUT_OF_RANGE = "grade_out_of_range"
    SUBJECT_UNKNOWN = "subject_unknown"  # предмета нет в базе для этого класса
    TOPIC_NOT_IN_PROGRAM = "topic_not_in_program"  # тема не найдена в программе класса
    TIME_OUT_OF_RANGE = "time_out_of_range"  # расчёт вне 15–20 мин / запрошено 8 мин
    TOO_MANY_TASKS = "too_many_tasks"
    UNKNOWN_NORM_REFERENCE = "unknown_norm_reference"  # запрос несуществующего пункта
    FULL_FGOS_GUARANTEE = "full_fgos_guarantee"  # «гарантируй полное соответствие ФГОС»
    PERSONAL_DATA = "personal_data"  # во вводе похоже на ФИО ребёнка
    DIAGNOSIS_REQUEST = "diagnosis_request"  # просьба поставить диагноз / оценить личность
    KNOWLEDGE_EMPTY = "knowledge_empty"  # нормативная база не загружена


class Severity(StrEnum):
    INFO = "info"  # просто сообщить
    WARN = "warn"  # предупредить, учитель может продолжить
    BLOCK = "block"  # не продолжать, предложить альтернативу


LABELS_RU: dict[str, str] = {
    # Role
    "admin": "Администратор",
    "teacher": "Педагог",
    "methodist": "Методист",
    "psychologist": "Педагог-психолог",
    # Level (только для учителя! ребёнку — нейтральные «Вариант А/Б/В»)
    "easy": "Лёгкий",
    "basic": "Базовый",
    "advanced": "Сложный",
    "all": "Все три уровня",
    # UUD
    "cognitive": "Познавательные",
    "regulatory": "Регулятивные",
    "communicative": "Коммуникативные",
    "balanced": "Сбалансированно",
    "subject": "Предметный",
    "personal": "Личностная направленность",
    # observations
    "correct": "верный ответ",
    "partial": "частично",
    "error": "ошибка",
    "no_answer": "нет ответа",
    "observed": "действие наблюдается",
    "not_shown": "действие не показано",
    "cannot_observe": "нет возможности наблюдать",
}

ERROR_LAYER_LABELS: dict[ErrorLayer, str] = {
    ErrorLayer.OPERATIONAL: "Операциональный сбой",
    ErrorLayer.CONCEPTUAL: "Концептуальное заблуждение",
    ErrorLayer.REGULATORY: "Регулятивный дефицит",
    ErrorLayer.COMMUNICATIVE: "Коммуникативный дефицит",
}

# Нейтральные подписи уровня для УЧЕНИЧЕСКОГО листа (без ярлыка способностей).
STUDENT_VARIANT_LABEL: dict[Level, str] = {
    Level.EASY: "Вариант А",
    Level.BASIC: "Вариант Б",
    Level.ADVANCED: "Вариант В",
}
