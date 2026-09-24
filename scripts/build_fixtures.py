"""Строит фикстуры контракта из учебных данных кейса SK01.

  data/fixtures/work_math3_all.json      — DiagnosticWork (3 уровня × 4 задания)
  data/fixtures/error_analysis_math3.json — ErrorAnalysisResult (пример из концепции)

Фикстуры нужны, чтобы бот/экспорт и тесты работали ДО готовности генератора.
Запуск: python scripts/build_fixtures.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from socrat.contracts import (  # noqa: E402
    PERSONAL_NOTE,
    STUDENT_VARIANT_LABEL,
    CoverageSummary,
    DiagnosticWork,
    DocType,
    EduLevel,
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    ErrorHypothesis,
    ErrorLayer,
    GenerationMeta,
    GenerationRequest,
    Level,
    LevelChoice,
    OutcomeLink,
    OutcomeType,
    QuickCheckTask,
    Reflection,
    SolutionStep,
    SourceDocument,
    Task,
    TaskChecks,
    TaskKind,
    TechniqueRecommendation,
    TimePlan,
    TypicalError,
    UUDFocus,
    UUDGroup,
    UUDIndicator,
    UUDObservation,
    Variant,
)

REF = ROOT / "data" / "reference" / "sk01"
OUT = ROOT / "data" / "fixtures"

TYPE_MAP = {
    "предметный": OutcomeType.SUBJECT,
    "познавательные УУД": OutcomeType.COGNITIVE,
    "регулятивные УУД": OutcomeType.REGULATORY,
    "коммуникативные УУД": OutcomeType.COMMUNICATIVE,
    "личностная направленность": OutcomeType.PERSONAL,
}
LEVEL_MAP = {"easy": Level.EASY, "basic": Level.BASIC, "advanced": Level.ADVANCED}
LETTER = {Level.EASY: "A", Level.BASIC: "B", Level.ADVANCED: "C"}

# Тип задания по позиции в учебном наборе (1 — умножение, 2 — деление, 3 — найди ошибку, 4 — 2 действия)
KIND_BY_POS = {
    1: TaskKind.WORD_PROBLEM,
    2: TaskKind.WORD_PROBLEM,
    3: TaskKind.FIND_ERROR,
    4: TaskKind.MULTI_STEP,
}

# Избирательная привязка УУД: только если в формулировке есть основание (триггер).
TRIGGERS: dict[UUDGroup, list[str]] = {
    UUDGroup.COGNITIVE: ["Сколько", "Найди ошибку", "одинаковые группы"],
    UUDGroup.REGULATORY: ["план", "проверь", "проверку", "Найди ошибку", "исправить"],
    UUDGroup.COMMUNICATIVE: ["объясни", "Объясни"],
}
GROUP_OUTCOME = {UUDGroup.COGNITIVE: "C01", UUDGroup.REGULATORY: "R01", UUDGroup.COMMUNICATIVE: "K01"}


def load(name: str):
    return json.loads((REF / name).read_text(encoding="utf-8"))


def outcome_link(ref: dict, rationale: str) -> OutcomeLink:
    return OutcomeLink(
        outcome_id=ref["outcome_id"],
        type=TYPE_MAP[ref["type"]],
        text=ref["paraphrase"],
        source_id=ref["source_id"],
        source_title="Федеральная рабочая программа «Математика» 1–4 классы, 2025",
        section=ref["section"],
        page=ref["page"],
        source_url=ref["source_url"],
        rationale=rationale,
        verified=True,
        verification_note="ID и страница есть в фиксированном справочнике кейса (outcomes_reference.json)",
    )


def find_trigger(text: str, group: UUDGroup) -> str | None:
    for t in TRIGGERS[group]:
        if t in text:
            m = re.search(r"[^.?!]*" + re.escape(t) + r"[^.?!]*[.?!]?", text)
            return (m.group(0) if m else t).strip()
    return None


def solution_steps(sol: str) -> list[SolutionStep]:
    steps = []
    for expr, res in re.findall(r"(\d+\s*[×x:+−\-]\s*\d+)\s*=\s*(\d+)", sol):
        py = expr.replace("×", "*").replace("x", "*").replace(":", "/").replace("−", "-")
        steps.append(
            SolutionStep(text=f"{expr} = {res}", expression=py.replace(" ", ""), result=res, verified=True)
        )
    return steps


def build_work() -> DiagnosticWork:
    variants_src = load("example_variants.json")
    refs = {o["outcome_id"]: o for o in load("outcomes_reference.json")["outcomes"]}
    req = GenerationRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Умножение и деление в пределах 100 и задачи в одно-два действия",
        level=LevelChoice.ALL,
        uud_focus=UUDFocus.BALANCED,
        task_count=4,
    )
    variants: list[Variant] = []
    for v in variants_src:
        level = LEVEL_MAP[v["variant_id"]]
        tasks: list[Task] = []
        for pos, t in enumerate(v["tasks"], start=1):
            text = t["prompt"]
            support = t["support"] if level != Level.BASIC else None
            probe = text + " " + (support or "")
            links = [
                outcome_link(refs[o], "Задание требует выполнить это действие в рамках темы.")
                for o in t["subject_outcomes"]
            ]
            indicators: list[UUDIndicator] = []
            for group in UUDGroup:
                trig = find_trigger(probe, group)
                oid = GROUP_OUTCOME[group]
                if not trig or oid not in t["meta_outcomes"]:
                    continue
                indicators.append(
                    UUDIndicator(
                        group=group,
                        outcome_id=oid,
                        action=refs[oid]["paraphrase"],
                        trigger=trig,
                        evidence=t["observable_evidence"][oid],
                        scale_hints={
                            UUDObservation.OBSERVED: "Действие явно видно в записи.",
                            UUDObservation.PARTIAL: "Видна часть действия (напр., проверка без вывода).",
                            UUDObservation.NOT_SHOWN: "Записан только ответ — действие не показано (не значит «не умеет»).",
                        },
                    )
                )
                links.append(outcome_link(refs[oid], f"В формулировке есть требование: «{trig}»."))
            links.append(outcome_link(refs["L01"], "Практический сюжет и рефлексия; балл не ставится."))
            tasks.append(
                Task(
                    task_id=f"{LETTER[level]}-{pos}",
                    number=pos,
                    level=level,
                    kind=KIND_BY_POS[pos],
                    student_text=text,
                    support=support,
                    answer_lines=4 if pos >= 3 else 3,
                    subject_goal="Умножение и деление в пределах 100; текстовая задача"
                    + (" в два действия" if pos == 4 else " в одно действие"),
                    meta_goal=", ".join(i.action for i in indicators) or "—",
                    expected_answer=str(t["expected_answer"]),
                    solution_steps=solution_steps(t["solution_example"]),
                    alternative_solutions=["Сложение одинаковых слагаемых вместо умножения — верный способ."]
                    if pos == 1
                    else [],
                    outcome_links=links,
                    uud_indicators=indicators,
                    personal_orientation=PERSONAL_NOTE,
                    typical_errors=[
                        TypicalError(
                            description="Складывает число групп и число предметов в группе.",
                            layer=ErrorLayer.CONCEPTUAL,
                            interpretation="Возможно, не различает смысл умножения как сложения равных групп.",
                            teacher_move="Попросить нарисовать группы кружками и пересчитать.",
                        )
                    ]
                    if pos in (1, 3)
                    else [],
                    oral_questions=["Как ты понял, какое действие нужно?", "Как можно проверить ответ?"],
                    technique_ids=["TECH-01"]
                    if pos == 3
                    else (["TECH-13", "TECH-05"] if pos == 4 else ["TECH-02"]),
                    conducting_note=t["teacher_comment"],
                    estimated_minutes=float(t["estimated_minutes"]),
                    checks=TaskChecks(math_verified=True),
                )
            )
        tasks_min = sum(x.estimated_minutes for x in tasks)
        total = v["read_instructions_minutes"] + tasks_min + v["reflection_minutes"]
        variants.append(
            Variant(
                level=level,
                student_label=STUDENT_VARIANT_LABEL[level],
                instruction="Покажи решение и поясни свой способ. Если что-то не получилось, можно написать, на каком шаге возник вопрос.",
                tasks=tasks,
                reflection=Reflection(
                    questions=v["reflection"],
                    minutes=v["reflection_minutes"],
                    reading_guide="Фиксируйте, что ребёнок написал о способе и трудностях, без оценки личности.",
                ),
                time_plan=TimePlan(
                    reading_minutes=v["read_instructions_minutes"],
                    tasks_minutes=tasks_min,
                    reflection_minutes=v["reflection_minutes"],
                    total_minutes=total,
                    within_range=15 <= total <= 20,
                ),
                level_rationale={
                    Level.EASY: "Опоры: подсказка о равных группах, небольшие числа (таблица на 3–4).",
                    Level.BASIC: "Без подсказок; все данные в условии; числа таблицы на 4–6.",
                    Level.ADVANCED: "Требуется объяснить выбор способа и предложить иной способ проверки; числа таблицы на 7–8.",
                }[level],
            )
        )
    groups = sorted({i.group for v in variants for t in v.tasks for i in t.uud_indicators})
    return DiagnosticWork(
        work_id="fixture-math3-all",
        request=req,
        title="Умножение и деление. Задачи в одно-два действия",
        variants=variants,
        coverage=CoverageSummary(
            uud_groups=groups,
            outcome_ids=sorted({lk.outcome_id for v in variants for t in v.tasks for lk in t.outcome_links}),
            missing_groups=[g for g in UUDGroup if g not in groups],
        ),
        limitations=[
            "Работа показывает соответствие ВЫБРАННЫМ планируемым результатам, а не всему ФГОС.",
            "Коды P01, C01, R01, K01, L01 — внутренние идентификаторы, не номера пунктов ФГОС.",
            "Время — расчётная оценка, не измерение на детях.",
            "Уровни — уровни задания, а не оценка способностей ребёнка.",
        ],
        sources=[
            SourceDocument(
                source_id="FRP-MATH-2025",
                title="Федеральная рабочая программа «Математика» 1–4 классы, Москва 2025",
                url="https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_matematika_1-4.pdf",
                doc_type=DocType.FRP,
                edu_levels=[EduLevel.NOO],
                subject_id="math",
                grades=[1, 2, 3, 4],
            ),
            SourceDocument(
                source_id="FGOS-NOO-286",
                title="ФГОС НОО, приказ от 31.05.2021 № 286",
                url="https://publication.pravo.gov.ru/Document/View/0001202107050028",
                doc_type=DocType.FGOS,
                edu_levels=[EduLevel.NOO],
                grades=[1, 2, 3, 4],
                note="Ссылка на первоначальное опубликование",
            ),
        ],
        meta=GenerationMeta(is_fixture=True, prompt_version="fixture", model="none"),
    )


def build_analysis() -> ErrorAnalysisResult:
    refs = {o["outcome_id"]: o for o in load("outcomes_reference.json")["outcomes"]}
    req = ErrorAnalysisRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Площадь и периметр прямоугольника",
        description=(
            "Дети перемножают стороны, когда нужно найти периметр, или складывают, когда нужно "
            "найти площадь. Формулы знают и могут проговорить, но в задаче применяют механически."
        ),
    )
    return ErrorAnalysisResult(
        analysis_id="fixture-analysis-math3",
        request=req,
        summary=(
            "Вероятно, формулы заучены отдельно от смысла величин: дети не соотносят «границу» и "
            "«внутреннюю часть» фигуры с выбором действия."
        ),
        hypotheses=[
            ErrorHypothesis(
                layer=ErrorLayer.CONCEPTUAL,
                uud_group=UUDGroup.COGNITIVE,
                statement="Разрыв между формулой и смыслом величины (периметр — длина границы, площадь — мера внутренней области).",
                confidence="medium",
                signs_to_check=["Может ли ребёнок показать периметр и площадь на рисунке?"],
            ),
            ErrorHypothesis(
                layer=ErrorLayer.REGULATORY,
                uud_group=UUDGroup.REGULATORY,
                statement="Нет шага проверки: «что я сейчас измеряю?» перед выбором действия.",
                confidence="low",
                signs_to_check=["Есть ли в записи единицы измерения (см и кв. см)?"],
            ),
        ],
        outcome_links=[
            outcome_link(refs["C01"], "Выбор модели (граница/область) соответствует отношениям в условии.")
        ],
        techniques=[
            TechniqueRecommendation(
                technique_id="TECH-11",
                name="«Ошибка как находка»",
                how_to_apply="Разобрать с классом решение «Петя нашёл площадь, чтобы узнать длину плинтуса».",
                expected_shift="Дети начинают сверять вопрос задачи с выбранной величиной.",
            ),
            TechniqueRecommendation(
                technique_id="TECH-02",
                name="«Карточка-алгоритм проверки»",
                how_to_apply="Первый пункт памятки: «Что измеряем — границу или внутреннюю часть?»",
                expected_shift="Вопрос-опора постепенно становится внутренним действием контроля.",
            ),
        ],
        quick_checks=[
            QuickCheckTask(
                student_text="Нужно обшить края коврика 3 дм на 2 дм лентой. Что найти: периметр или площадь? Объясни.",
                expected_answer="Периметр: (3 + 2) · 2 = 10 дм.",
                what_it_checks="Выбор величины по смыслу ситуации.",
            )
        ],
        teacher_reflection_question="В каких типах задач (текстовых, на чертеже, практических) ошибка встречается чаще?",
        meta=GenerationMeta(is_fixture=True, prompt_version="fixture", model="none"),
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    w = build_work()
    (OUT / "work_math3_all.json").write_text(w.model_dump_json(indent=2), encoding="utf-8")
    a = build_analysis()
    (OUT / "error_analysis_math3.json").write_text(a.model_dump_json(indent=2), encoding="utf-8")
    print("ok:", OUT)


if __name__ == "__main__":
    main()
