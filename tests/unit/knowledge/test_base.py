"""Тесты класса LocalKnowledgeBase (G7)."""

from __future__ import annotations

from pathlib import Path

from socrat.config import Settings, get_settings
from socrat.contracts import KnowledgeBase, OutcomeType
from socrat.knowledge import LocalKnowledgeBase


def test_local_knowledge_base_implements_protocol():
    kb = LocalKnowledgeBase(get_settings())
    assert isinstance(kb, KnowledgeBase)


def test_empty_knowledge_base_graceful(tmp_path: Path):
    """LocalKnowledgeBase без данных -> ready=False, без исключений."""
    empty_settings = Settings(
        knowledge_dir=tmp_path / "non_existent_knowledge",
        sources_file=tmp_path / "non_existent_sources.yaml",
    )
    kb = LocalKnowledgeBase(empty_settings)
    st = kb.status()

    assert not st.ready
    assert len(st.problems) > 0
    assert kb.list_subjects(3) == []
    assert kb.get_outcomes(3, "math") == []
    assert kb.get_outcome("P01") is None
    assert kb.get_page_text("FRP-MATH-2025", 1) is None
    assert kb.search("умножение") == []


def test_list_subjects():
    kb = LocalKnowledgeBase(get_settings())
    subjects = kb.list_subjects(3)
    subject_ids = {s.subject_id for s in subjects}

    # Математика должна быть в списке для 3 класса
    assert "math" in subject_ids

    # Химии не должно быть в 3 классе
    assert "chemistry" not in subject_ids


def test_get_outcomes_contains_sk01():
    """get_outcomes(3, 'math') обязательно содержит P01..L01."""
    kb = LocalKnowledgeBase(get_settings())
    outcomes = kb.get_outcomes(3, "math", k=30)
    out_ids = {o.outcome_id for o in outcomes}

    sk01_required = {"P01", "P02", "C01", "R01", "K01", "L01"}
    assert sk01_required.issubset(out_ids)

    # Первые элементы при math/3 должны начинаться с эталонных
    top_ids = {o.outcome_id for o in outcomes[:6]}
    assert top_ids == sk01_required


def test_get_outcomes_type_filter():
    kb = LocalKnowledgeBase(get_settings())
    subjects_only = kb.get_outcomes(3, "math", types=[OutcomeType.SUBJECT], k=10)
    for o in subjects_only:
        assert o.type == OutcomeType.SUBJECT

    meta_only = kb.get_outcomes(3, "math", types=[OutcomeType.COGNITIVE, OutcomeType.REGULATORY], k=10)
    for o in meta_only:
        assert o.type in (OutcomeType.COGNITIVE, OutcomeType.REGULATORY)


def test_check_topic():
    kb = LocalKnowledgeBase(get_settings())

    res_ok = kb.check_topic(3, "math", "Умножение и деление чисел")
    assert res_ok.in_program
    assert res_ok.confidence >= 0.5

    res_fail = kb.check_topic(3, "math", "Фотосинтез и клеточное дыхание")
    assert not res_fail.in_program
    assert res_fail.suggestions  # предлагает темы программы


async def test_ingest_school_material():
    kb = LocalKnowledgeBase(get_settings())
    doc = await kb.ingest_school_material("school_work.pdf", b"%PDF-1.4 dummy content")

    assert doc.source_id.startswith("SCHOOL-")
    assert not doc.is_normative
    assert doc.title == "school_work.pdf"
    assert kb.get_source(doc.source_id) is not None
