"""Тесты класса LocalKnowledgeBase (G7, G10, G11, G12, G13)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from socrat.config import Settings, get_settings
from socrat.contracts import KnowledgeBase, OutcomeType
from socrat.knowledge import LocalKnowledgeBase


@pytest.fixture
def test_kb(tmp_path: Path) -> LocalKnowledgeBase:
    """Изолированная база знаний в tmp_path, не пишущая в реальный data/knowledge/ (G11)."""
    real_settings = get_settings()
    k_dir = tmp_path / "knowledge"
    k_dir.mkdir(parents=True)

    # Копируем catalog/, pages/, manifest.json если они есть
    for subdir in ("catalog", "pages"):
        src = real_settings.knowledge_dir / subdir
        if src.exists():
            shutil.copytree(src, k_dir / subdir)

    manifest_src = real_settings.knowledge_dir / "manifest.json"
    if manifest_src.exists():
        shutil.copy2(manifest_src, k_dir / "manifest.json")

    test_settings = Settings(
        _env_file=None,
        knowledge_dir=k_dir,
        sources_file=real_settings.sources_file,
        case_reference_dir=real_settings.case_reference_dir,
    )
    return LocalKnowledgeBase(test_settings)


def test_local_knowledge_base_implements_protocol(test_kb: LocalKnowledgeBase):
    assert isinstance(test_kb, KnowledgeBase)


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


def test_list_subjects(test_kb: LocalKnowledgeBase):
    subjects = test_kb.list_subjects(3)
    subject_ids = {s.subject_id for s in subjects}

    # Математика должна быть в списке для 3 класса
    assert "math" in subject_ids

    # Химии не должно быть в 3 классе
    assert "chemistry" not in subject_ids


def test_list_subjects_ooo(test_kb: LocalKnowledgeBase):
    """Проверка доступности только предметов с каталогами (G12, G15)."""
    subjs_7 = [s.subject_id for s in test_kb.list_subjects(7)]
    # list_subjects(7) содержит строго russian, algebra, geometry, biology, physics
    assert subjs_7 == ["russian", "algebra", "geometry", "biology", "physics"]

    # Для 10 и 11 классов список должен быть пустым
    assert test_kb.list_subjects(10) == []
    assert test_kb.list_subjects(11) == []

    # Для 5 класса
    subjs_5 = {s.subject_id for s in test_kb.list_subjects(5)}
    assert subjs_5 == {"russian", "math", "biology"}


def test_check_topic_g14(test_kb: LocalKnowledgeBase):
    """Проверка G14: 5 типичных тем для algebra 7, russian 5, physics 8, и отсечение Фотосинтеза."""
    # Алгебра 7 класс (5 типичных тем)
    algebra_7_topics = [
        "Линейная функция и её график",
        "Степень с натуральным показателем",
        "Одночлены и многочлены",
        "Системы линейных уравнений",
        "Формулы сокращенного умножения",
    ]
    for topic in algebra_7_topics:
        res = test_kb.check_topic(7, "algebra", topic)
        assert res.in_program, f"Topic '{topic}' should be in program for algebra 7"
        assert res.confidence >= 0.4

    # Русский язык 5 класс (5 типичных тем)
    russian_5_topics = [
        "Фонетический анализ слова",
        "Синонимы, антонимы и омонимы",
        "Лексическое значение слова",
        "Морфемный состав слова",
        "Правописание безударных гласных в корне",
    ]
    for topic in russian_5_topics:
        res = test_kb.check_topic(5, "russian", topic)
        assert res.in_program, f"Topic '{topic}' should be in program for russian 5"
        assert res.confidence >= 0.4

    # Физика 8 класс (5 типичных тем)
    physics_8_topics = [
        "Теплопередача и виды теплопередачи",
        "Закон Ома для участка цепи",
        "Электрический ток и закон Джоуля-Ленца",
        "Испарение и конденсация",
        "Плавление и кристаллизация",
    ]
    for topic in physics_8_topics:
        res = test_kb.check_topic(8, "physics", topic)
        assert res.in_program, f"Topic '{topic}' should be in program for physics 8"
        assert res.confidence >= 0.4

    # Отрицательный тест: Фотосинтез для математики 3 класса
    res_neg = test_kb.check_topic(3, "math", "Фотосинтез и клеточное дыхание")
    assert not res_neg.in_program
    assert len(res_neg.suggestions) > 0


def test_get_outcomes_contains_sk01(test_kb: LocalKnowledgeBase):
    """get_outcomes(3, 'math') обязательно содержит P01..L01."""
    outcomes = test_kb.get_outcomes(3, "math", k=30)
    out_ids = {o.outcome_id for o in outcomes}

    sk01_required = {"P01", "P02", "C01", "R01", "K01", "L01"}
    assert sk01_required.issubset(out_ids)

    # Первые элементы при math/3 должны начинаться с эталонных
    top_ids = {o.outcome_id for o in outcomes[:6]}
    assert top_ids == sk01_required


def test_get_outcomes_type_filter(test_kb: LocalKnowledgeBase):
    subjects_only = test_kb.get_outcomes(3, "math", types=[OutcomeType.SUBJECT], k=10)
    for o in subjects_only:
        assert o.type == OutcomeType.SUBJECT

    meta_only = test_kb.get_outcomes(3, "math", types=[OutcomeType.COGNITIVE, OutcomeType.REGULATORY], k=10)
    for o in meta_only:
        assert o.type in (OutcomeType.COGNITIVE, OutcomeType.REGULATORY)


def test_check_topic(test_kb: LocalKnowledgeBase):
    res_ok = test_kb.check_topic(3, "math", "Умножение и деление чисел")
    assert res_ok.in_program
    assert res_ok.confidence >= 0.5

    res_fail = test_kb.check_topic(3, "math", "Фотосинтез и клеточное дыхание")
    assert not res_fail.in_program
    assert res_fail.suggestions  # предлагает темы программы


def test_check_topic_fallback_without_index_or_pages(tmp_path: Path):
    """Проверка работы check_topic на чистом клоне без index/ и pages/ (G10)."""
    real_settings = get_settings()
    k_dir = tmp_path / "knowledge"
    k_dir.mkdir(parents=True)

    # Копируем ТОЛЬКО catalog/ (без pages/ и без index/)
    src_catalog = real_settings.knowledge_dir / "catalog"
    if src_catalog.exists():
        shutil.copytree(src_catalog, k_dir / "catalog")

    clean_settings = Settings(
        _env_file=None,
        knowledge_dir=k_dir,
        sources_file=real_settings.sources_file,
        case_reference_dir=real_settings.case_reference_dir,
    )
    kb = LocalKnowledgeBase(clean_settings)
    assert len(kb.index.chunks) == 0

    # Проверка fallback по результатам каталога
    res_ok = kb.check_topic(3, "math", "Умножение и деление чисел")
    assert res_ok.in_program

    res_fail = kb.check_topic(3, "math", "Фотосинтез и клеточное дыхание")
    assert not res_fail.in_program
    assert len(res_fail.suggestions) > 0


def test_status_diagnostics_reports_missing_pages_or_catalog(tmp_path: Path):
    """Проверка диагностики в status().problems для админки (G13)."""
    real_settings = get_settings()
    k_dir = tmp_path / "knowledge"
    k_dir.mkdir(parents=True)

    # Пустой каталог без pages
    diag_settings = Settings(
        _env_file=None,
        knowledge_dir=k_dir,
        sources_file=real_settings.sources_file,
        case_reference_dir=real_settings.case_reference_dir,
    )
    kb = LocalKnowledgeBase(diag_settings)
    st = kb.status()

    # Должны быть сообщения о необходимости выполнить build
    assert any("— выполните build" in p for p in st.problems)


async def test_ingest_school_material(test_kb: LocalKnowledgeBase):
    doc = await test_kb.ingest_school_material("school_work.pdf", b"%PDF-1.4 dummy content")

    assert doc.source_id.startswith("SCHOOL-")
    assert not doc.is_normative
    assert doc.title == "school_work.pdf"
    assert test_kb.get_source(doc.source_id) is not None
