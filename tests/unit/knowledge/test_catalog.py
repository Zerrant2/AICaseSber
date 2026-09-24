"""Тесты каталога планируемых результатов (G5)."""

from __future__ import annotations

import json

from socrat.config import get_settings
from socrat.contracts import Outcome, OutcomeType
from socrat.knowledge.catalog import load_sk01_reference_outcomes


def test_sk01_reference_outcomes_loading():
    settings = get_settings()
    outcomes = load_sk01_reference_outcomes(settings.case_reference_dir)
    assert len(outcomes) == 6

    ids = {o.outcome_id for o in outcomes}
    assert ids == {"P01", "P02", "C01", "R01", "K01", "L01"}

    # Проверка типов
    types_by_id = {o.outcome_id: o.type for o in outcomes}
    assert types_by_id["P01"] == OutcomeType.SUBJECT
    assert types_by_id["P02"] == OutcomeType.SUBJECT
    assert types_by_id["C01"] == OutcomeType.COGNITIVE
    assert types_by_id["R01"] == OutcomeType.REGULATORY
    assert types_by_id["K01"] == OutcomeType.COMMUNICATIVE
    assert types_by_id["L01"] == OutcomeType.PERSONAL

    # Все в кейсе имеют quote_is_verbatim=False (методический пересказ)
    for o in outcomes:
        assert not o.quote_is_verbatim
        assert o.grade == 3
        assert o.source_id == "FRP-MATH-2025"
        assert o.page >= 1


def test_committed_catalog_validity():
    settings = get_settings()
    catalog_file = settings.knowledge_dir / "catalog" / "noo_math.json"
    assert catalog_file.exists(), "noo_math.json must be committed in catalog/"

    data = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert len(data) >= 100

    outcomes = [Outcome.model_validate(item) for item in data]
    ids = [o.outcome_id for o in outcomes]

    # Проверка уникальности ID
    assert len(ids) == len(set(ids)), "Все outcome_id в каталоге должны быть уникальны"

    # Проверка стабильности формата ID
    for o in outcomes:
        if o.outcome_id in ("P01", "P02", "C01", "R01", "K01", "L01"):
            continue
        if o.type == OutcomeType.SUBJECT:
            assert f"math-{o.grade}-P-" in o.outcome_id
        else:
            assert "math-noo-" in o.outcome_id
