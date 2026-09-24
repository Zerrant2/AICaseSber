import pytest

from socrat.contracts import (
    DiagnosticWork,
    ErrorAnalysisRequest,
    GenerationError,
    GuardrailCode,
    SubjectObservation,
    UUDGroup,
    UUDObservation,
)
from socrat.core import RuleResponseObserver
from socrat.testing.scripted import DEFAULT_ANALYSIS, ScriptedLLM

FIX = "data/fixtures/work_math3_all.json"


def areq(desc: str) -> ErrorAnalysisRequest:
    return ErrorAnalysisRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Площадь и периметр прямоугольника",
        description=desc,
    )


async def test_analysis_filters_invented_ids(core_factory):
    _, analyzer, _ = core_factory(ScriptedLLM())
    res = await analyzer.analyze(
        areq("Дети перемножают стороны, когда нужно найти периметр, и складывают для площади.")
    )
    assert [t.technique_id for t in res.techniques] == ["TECH-11"]
    assert [lk.outcome_id for lk in res.outcome_links] == ["C01"]
    assert res.hypotheses[0].layer.value == "conceptual" and res.limitations


async def test_analysis_cleans_diagnosis_words(core_factory):
    bad = dict(DEFAULT_ANALYSIS, summary="У детей, вероятно, СДВГ, они ленивые.")
    _, analyzer, _ = core_factory(ScriptedLLM(analysis=bad))
    res = await analyzer.analyze(areq("Дети путают периметр и площадь в задачах про комнату и забор."))
    assert "сдвг" not in res.summary.lower() and "ленив" not in res.summary.lower()
    assert any(w.code == GuardrailCode.DIAGNOSIS_REQUEST for w in res.warnings)


async def test_analysis_blocks_diagnosis_request_keeps_text(core_factory):
    llm = ScriptedLLM()
    _, analyzer, _ = core_factory(llm)
    with pytest.raises(GenerationError):
        await analyzer.analyze(areq("Поставьте диагноз: у кого дискалькулия? Путают периметр и площадь."))
    # Автозамены имён нет (решение 24.09): текст педагога уходит в модель как есть.
    r = await analyzer.check(areq("Путают законы Ньютона и периметр с площадью в каждой задаче."))
    assert not any(i.code == GuardrailCode.PERSONAL_DATA for i in r.issues)
    await analyzer.analyze(areq("Путают законы Ньютона и периметр с площадью в каждой задаче."))
    assert "Ньютона" in llm.calls[-1]["user"] and "[ученик]" not in llm.calls[-1]["user"]


def _task(tid):
    return DiagnosticWork.model_validate_json(open(FIX, encoding="utf-8").read()).find_task(tid)


def test_T06_number_only():
    obs = RuleResponseObserver().observe(_task("A-1"), "12")
    assert obs.subject_status == SubjectObservation.CORRECT
    assert all(i.status in (UUDObservation.NOT_SHOWN, UUDObservation.CANNOT_OBSERVE) for i in obs.uud)
    assert "недостаточно наблюдений" in obs.summary_ru


def test_full_answer_observed():
    obs = RuleResponseObserver().observe(
        _task("A-4"), "Сначала всего 3 × 4 = 12, затем 12 − 2 = 10. Проверка: 10 + 2 = 12."
    )
    st = {i.group: i.status for i in obs.uud}
    assert obs.subject_status == SubjectObservation.CORRECT
    assert st[UUDGroup.REGULATORY] == UUDObservation.OBSERVED
    assert st[UUDGroup.COMMUNICATIVE] == UUDObservation.CANNOT_OBSERVE


def test_wrong_and_empty():
    o = RuleResponseObserver()
    assert o.observe(_task("A-1"), "3 + 4 = 7").subject_status == SubjectObservation.ERROR
    assert o.observe(_task("A-1"), "").subject_status == SubjectObservation.NO_ANSWER


def test_T07_reflection_neutral():
    r = RuleResponseObserver().observe_reflection("Что было трудно?", "Мне было трудно, я не понял задачу 3")
    low = r.neutral_note.lower()
    assert "не оценка личности" in low and "диагноз" in low
    for w in ("ленив", "слаб", "неспособ"):
        assert w not in low
