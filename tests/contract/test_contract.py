"""Тесты контракта: фикстуры валидны, фейки соответствуют протоколам, ученическая проекция без ответов."""

import json
from pathlib import Path

import pytest

from socrat.contracts import (
    DiagnosticWork,
    ErrorAnalysisResult,
    ErrorAnalyzer,
    FeedbackRepository,
    GenerationRequest,
    KnowledgeBase,
    Level,
    LevelChoice,
    LLMClient,
    ResponseObserver,
    SubjectObservation,
    TeacherRepository,
    UsageRepository,
    UUDObservation,
    WorkGenerator,
    WorkRepository,
    student_view,
)
from socrat.testing import fakes

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "data" / "fixtures"


def load_work() -> DiagnosticWork:
    return DiagnosticWork.model_validate_json((FIX / "work_math3_all.json").read_text(encoding="utf-8"))


def test_fixture_work_valid():
    w = load_work()
    assert [v.level for v in w.variants] == [Level.EASY, Level.BASIC, Level.ADVANCED]
    for v in w.variants:
        assert len(v.tasks) == 4
        assert v.reflection.questions
        assert 15 <= v.time_plan.total_minutes <= 20 and v.time_plan.within_range


def test_fixture_analysis_valid():
    ErrorAnalysisResult.model_validate_json((FIX / "error_analysis_math3.json").read_text(encoding="utf-8"))


def test_fixture_uud_not_mechanical():
    """Не каждое задание помечено всеми тремя группами УУД (критерий кейса)."""
    w = load_work()
    counts = [len(t.uud_indicators) for v in w.variants for t in v.tasks]
    assert min(counts) < 3


def test_student_view_has_no_answers():
    w = load_work()
    dumped = student_view(w).model_dump_json()
    for v in w.variants:
        for t in v.tasks:
            for s in t.solution_steps:
                assert s.text not in dumped, f"решение {s.text!r} утекло в ученическую проекцию"
            assert t.conducting_note not in dumped
    for label in ("Лёгкий", "Базовый", "Сложный"):
        assert label not in dumped


def test_techniques_ids_exist():
    lib = json.loads((ROOT / "data" / "techniques.json").read_text(encoding="utf-8"))
    ids = {t["technique_id"] for t in lib["techniques"]}
    assert len(ids) == 15
    w = load_work()
    for v in w.variants:
        for t in v.tasks:
            assert set(t.technique_ids) <= ids


@pytest.mark.parametrize(
    "obj,proto",
    [
        (fakes.FakeKnowledgeBase(), KnowledgeBase),
        (fakes.FakeWorkGenerator(delay_s=0), WorkGenerator),
        (fakes.FakeErrorAnalyzer(), ErrorAnalyzer),
        (fakes.FakeResponseObserver(), ResponseObserver),
        (fakes.FakeLLMClient(), LLMClient),
        (fakes.InMemoryTeacherRepository(), TeacherRepository),
        (fakes.InMemoryWorkRepository(), WorkRepository),
        (fakes.InMemoryFeedbackRepository(), FeedbackRepository),
        (fakes.InMemoryUsageRepository(), UsageRepository),
    ],
)
def test_fakes_match_protocols(obj, proto):
    assert isinstance(obj, proto)


async def test_fake_generator_respects_request():
    gen = fakes.FakeWorkGenerator(delay_s=0)
    req = GenerationRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="умножение",
        level=LevelChoice.BASIC,
        task_count=2,
    )
    w = await gen.generate(req)
    assert len(w.variants) == 1 and w.variants[0].level == Level.BASIC
    assert len(w.variants[0].tasks) == 2


def test_fake_observer_number_only_T06():
    """T06: правильное число без объяснения → предмет верно, УУД не объявлены."""
    w = load_work()
    task = w.find_task("A-1")
    obs = fakes.FakeResponseObserver().observe(task, "12")
    assert obs.subject_status == SubjectObservation.CORRECT
    assert all(i.status != UUDObservation.OBSERVED for i in obs.uud)
