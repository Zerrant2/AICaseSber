"""Проверки кейса на РЕАЛЬНОЙ модели (нужен LLM_API_KEY в .env). Запуск:
pytest -m llm tests/case_sk01 -q      или      python scripts/run_case_tests.py --llm
"""

from __future__ import annotations

import pytest

from socrat.config import get_settings
from socrat.contracts import GenerationRequest, Level, LevelChoice, UUDGroup, student_view
from socrat.core import build_core
from socrat.testing.fakes import FakeKnowledgeBase

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def real_core():
    s = get_settings()
    if not s.llm_api_key.get_secret_value() and "openrouter" in s.llm_base_url:
        pytest.skip("LLM_API_KEY не задан")
    try:
        from socrat.knowledge import LocalKnowledgeBase

        kb = LocalKnowledgeBase(s)
        if not kb.get_outcomes(3, "math", k=1):
            kb = FakeKnowledgeBase()
    except ImportError:
        kb = FakeKnowledgeBase()
    return build_core(s, kb)


async def test_T01_T02_T09_real_llm(real_core, record):
    gen, _, _ = real_core
    req = GenerationRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Умножение и деление в пределах 100 и задачи в одно-два действия",
        level=LevelChoice.ALL,
        task_count=4,
    )
    work = await gen.generate(req)
    assert [v.level for v in work.variants] == [Level.EASY, Level.BASIC, Level.ADVANCED]
    bad = [t.task_id for v in work.variants for t in v.tasks if t.checks.math_verified is False]
    leaks = [t.task_id for v in work.variants for t in v.tasks if not t.checks.answer_leak_free]
    assert not bad and not leaks, (bad, leaks, [w.message_ru for w in work.warnings])
    assert set(work.coverage.uud_groups) == set(UUDGroup)
    assert "expected_answer" not in student_view(work).model_dump_json()
    record(
        "T01",
        "LLM: "
        + "; ".join(
            f"{v.student_label} {len(v.tasks)} зад., {v.time_plan.total_minutes:g} мин" for v in work.variants
        )
        + f" · модель {work.meta.model}, {work.meta.tokens_in}+{work.meta.tokens_out} ток., ${work.meta.cost_usd}",
    )
