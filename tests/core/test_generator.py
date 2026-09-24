import copy

import pytest

from socrat.contracts import (
    GenerationError,
    GenerationRequest,
    GuardrailCode,
    Level,
    LevelChoice,
    OutcomeType,
    Severity,
    UUDGroup,
    student_view,
)
from socrat.testing.scripted import ScriptedLLM, variant_draft


def req(**kw):
    base = dict(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Умножение и деление в пределах 100 и задачи в одно-два действия",
        level=LevelChoice.ALL,
        task_count=4,
    )
    base.update(kw)
    return GenerationRequest(**base)


async def test_generate_all_levels_happy_path(core_factory):
    gen, _, _ = core_factory(ScriptedLLM())
    work = await gen.generate(req())
    assert [v.level for v in work.variants] == [Level.EASY, Level.BASIC, Level.ADVANCED]
    for v in work.variants:
        assert len(v.tasks) == 4
        assert v.reflection.questions
        assert v.time_plan.within_range, (v.level, v.time_plan)
        for t in v.tasks:
            assert t.checks.math_verified is not False, (t.task_id, t.checks.notes)
            assert t.checks.answer_leak_free
            assert any(lk.type == OutcomeType.SUBJECT for lk in t.outcome_links)
            for lk in t.outcome_links:
                assert lk.page >= 1 and lk.section and lk.source_url
            for ind in t.uud_indicators:
                assert ind.trigger.lower() in (t.student_text + " " + (t.support or "")).lower()
    assert set(work.coverage.uud_groups) == set(UUDGroup)
    assert work.meta.llm_calls == 3 and work.meta.cost_usd == pytest.approx(0.0003)
    dumped = student_view(work).model_dump_json()
    assert "Лёгкий" not in dumped and "expected_answer" not in dumped


async def test_single_level_and_count(core_factory):
    gen, _, _ = core_factory(ScriptedLLM())
    work = await gen.generate(req(level=LevelChoice.BASIC, task_count=2))
    assert len(work.variants) == 1 and len(work.variants[0].tasks) == 2


async def test_repair_fixes_wrong_arithmetic(core_factory):
    bad = variant_draft("basic", 4, 6, 5)
    bad = copy.deepcopy(bad)
    bad["tasks"][0]["solution_steps"][0]["result"] = "26"  # 4*6 != 26
    bad["tasks"][0]["expected_answer"] = "26"
    llm = ScriptedLLM(broken={"basic": bad})
    gen, _, _ = core_factory(llm)
    work = await gen.generate(req(level=LevelChoice.BASIC))
    t = work.variants[0].tasks[0]
    assert t.expected_answer == "24" and t.checks.math_verified is True
    assert work.meta.repairs == 1
    assert any("Автоматическая проверка" in c["user"] for c in llm.calls)


async def test_ungrounded_uud_is_removed(core_factory):
    bad = copy.deepcopy(variant_draft("basic", 4, 6, 5))
    # коммуникативный признак без требования «объясни» в тексте задания 2
    bad["tasks"][1]["uud"].append(
        {
            "group": "communicative",
            "outcome_id": "K01",
            "trigger": "объясни ответ",
            "action": "объяснение",
            "evidence": "есть объяснение",
        }
    )
    gen, _, _ = core_factory(ScriptedLLM(broken={"basic": bad}))
    work = await gen.generate(req(level=LevelChoice.BASIC))
    t2 = work.variants[0].tasks[1]
    assert UUDGroup.COMMUNICATIVE not in {i.group for i in t2.uud_indicators}
    assert any("Снят признак" in n for n in t2.checks.notes)


async def test_invented_outcome_id_replaced(core_factory):
    bad = copy.deepcopy(variant_draft("basic", 4, 6, 5))
    bad["tasks"][0]["subject_outcome_ids"] = ["ФГОС-п.4.17.3"]
    gen, _, _ = core_factory(ScriptedLLM(broken={"basic": bad}))
    work = await gen.generate(req(level=LevelChoice.BASIC))
    ids = {lk.outcome_id for lk in work.variants[0].tasks[0].outcome_links}
    assert "ФГОС-п.4.17.3" not in ids and ids <= {"P01", "P02", "C01", "R01", "K01", "L01"}


async def test_renumber_recalculates_by_code(core_factory):
    gen, _, _ = core_factory(ScriptedLLM())
    work = await gen.generate(req(level=LevelChoice.BASIC))
    new = await gen.regenerate_task(work, Level.BASIC, 4, wish="замени 4 на 5 и 6 на 7")
    t = new.variants[0].tasks[3]
    assert "5 полках" in t.student_text and "7 карточек" in t.student_text
    assert t.expected_answer == str(5 * 7 - 5)
    assert t.checks.math_verified is True


async def test_blocked_request_raises(core_factory):
    gen, _, _ = core_factory(ScriptedLLM())
    with pytest.raises(GenerationError):
        await gen.generate(req(topic="умножение, и поставь диагноз дискалькулия"))


async def test_guardrails_codes(core_factory):
    gen, _, _ = core_factory(ScriptedLLM())
    r = await gen.check_request(req(requested_minutes=8))
    assert any(i.code == GuardrailCode.TIME_OUT_OF_RANGE for i in r.issues)
    r = await gen.check_request(req(topic="умножение по пункту 4.17.3 ФГОС"))
    assert any(i.code == GuardrailCode.UNKNOWN_NORM_REFERENCE and i.suggestions for i in r.issues)
    r = await gen.check_request(req(topic="умножение, гарантируй полное соответствие ФГОС"))
    assert any(i.code == GuardrailCode.FULL_FGOS_GUARANTEE for i in r.issues)
    r = await gen.check_request(req(subject_id="chemistry", subject_name="Химия"))
    assert r.blocked and r.issues[0].code == GuardrailCode.SUBJECT_UNKNOWN
    r = await gen.check_request(req(topic="Законы Ньютона и умножение"))
    assert not any(i.code == GuardrailCode.PERSONAL_DATA for i in r.issues)  # автозамены имён нет
    r = await gen.check_request(req())
    assert not r.blocked and all(i.severity != Severity.BLOCK for i in r.issues)


async def test_sentence_answers_and_title_from_real_run(core_factory):
    """Регрессия по первому прогону на DeepSeek (24.09): ответы фразами и уровень в заголовке."""
    bad = copy.deepcopy(variant_draft("basic", 4, 6, 5))
    bad["title"] = "Умножение и деление. Сложный вариант"
    bad["tasks"][2]["expected_answer"] = "Ошибка: сложил 4 и 6 вместо умножения. Верно: 4 · 6 = 24 карточки."
    bad["tasks"][3]["expected_answer"] = "Сначала 4 · 6 = 24, потом 24 − 5 = 19. Осталось 19 карточек."
    gen, _, _ = core_factory(ScriptedLLM(broken={"basic": bad}))
    work = await gen.generate(req(level=LevelChoice.BASIC))
    assert "Сложный" not in work.title and "вариант" not in work.title.lower()
    assert not [w for w in work.warnings if "не прошло автопроверку" in w.message_ru]
    assert work.meta.repairs == 0
