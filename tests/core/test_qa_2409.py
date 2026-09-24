"""Регрессия по ручной проверке бота 24.09 (CLAUDE_QA_2026-09-24): пожелание о смене действий
при замене задания и ввод педагога вместо ответа ученика в «Анализ действий ученика»."""

import copy

from socrat.contracts import (
    DiagnosticWork,
    GenerationRequest,
    Level,
    LevelChoice,
    SubjectObservation,
    TaskKind,
    UUDObservation,
)
from socrat.core import RuleResponseObserver
from socrat.core.mathcheck import word_numbers
from socrat.core.wish import main_operations, parse_operation_wish
from socrat.testing.scripted import ScriptedLLM, _task, _uud, variant_draft

FIX = "data/fixtures/work_math3_all.json"
WISH = "Замени на вычитание и сложение"


def _req():
    return GenerationRequest(
        grade=3,
        subject_id="math",
        subject_name="Математика",
        topic="Внетабличное умножение и деление",
        level=LevelChoice.BASIC,
        task_count=4,
    )


def _fx(tid):
    return DiagnosticWork.model_validate_json(open(FIX, encoding="utf-8").read()).find_task(tid)


# ------------------------------------------------------------------ пожелание учителя


def test_parse_operation_wish():
    w = parse_operation_wish(WISH)
    assert w.required == ["+", "-"] and not w.forbidden
    w = parse_operation_wish("Замени умножение на деление")
    assert w.required == ["/"] and w.forbidden == ["*"]
    assert parse_operation_wish("Не используй сложение").forbidden == ["+"]
    assert not parse_operation_wish("Сделай сложнее")
    assert not parse_operation_wish("замени 4 на 5")


async def test_wish_ignored_by_model_is_reported(core_factory):
    """Модель снова дала умножение («проверь сложением» не считается) → предупреждение, а не молчаливый успех."""
    llm = ScriptedLLM()
    gen, _, _ = core_factory(llm)
    work = await gen.generate(_req())
    llm.broken["basic"] = variant_draft("basic", 4, 6, 5)
    new = await gen.regenerate_task(work, Level.BASIC, 1, wish=WISH)
    t = new.variants[0].tasks[0]
    assert main_operations(t) == {"*"}
    assert any("выполнить не удалось" in w.message_ru and WISH in w.message_ru for w in new.warnings)
    assert "сложение и вычитание" in llm.calls[-2]["user"] or "сложение и вычитание" in llm.calls[-1]["user"]


async def test_wish_fulfilled(core_factory):
    llm = ScriptedLLM()
    gen, _, _ = core_factory(llm)
    work = await gen.generate(_req())
    good = copy.deepcopy(variant_draft("basic", 4, 6, 5))
    good["tasks"][0] = _task(
        1,
        "На полке было 30 карточек. Положили ещё 12, потом забрали 9. Сколько карточек стало? "
        "Сначала запиши план, потом запиши выражение и объясни выбор.",
        33,
        [("30+12", 42), ("42-9", 33)],
        [_uud("cognitive", "C01", "запиши выражение"), _uud("regulatory", "R01", "Сначала запиши план")],
    )
    llm.broken["basic"] = good
    new = await gen.regenerate_task(work, Level.BASIC, 1, wish=WISH)
    t = new.variants[0].tasks[0]
    assert t.kind == TaskKind.MULTI_STEP
    assert main_operations(t) == {"+", "-"} and t.checks.math_verified is True
    assert not any("выполнить не удалось" in w.message_ru for w in new.warnings)
    prompt = next(c["user"] for c in llm.calls if WISH in c["user"])
    assert "Проверяется кодом" in prompt and "важнее темы" in prompt


# ------------------------------------------------------------------ разбор ответа


def test_teacher_question_is_not_student_answer():
    obs = RuleResponseObserver().observe(_fx("A-1"), "Ученик не справился с умножением, как ему помочь")
    assert obs.subject_status == SubjectObservation.NO_ANSWER
    assert obs.uud == []
    assert "Анализ типичной ошибки" in obs.summary_ru
    assert "ошибка" not in obs.subject_basis.lower()


def test_verbal_student_answer_without_digits():
    o = RuleResponseObserver()
    ok = o.observe(_fx("A-1"), "Всего двенадцать карточек, потому что три раза по четыре")
    assert ok.subject_status == SubjectObservation.CORRECT and ok.extracted_answer == "12"
    no_num = o.observe(_fx("A-1"), "Надо умножить, потому что на полках одинаково")
    assert no_num.subject_status == SubjectObservation.NO_ANSWER  # не «ошибка»


def test_find_error_student_writes_in_third_person():
    obs = RuleResponseObserver().observe(_fx("A-3"), "Он не понял, что надо умножать: 3 · 4 = 12")
    assert obs.uud  # это запись ученика, разбор выполняется


def test_alternatives_not_repeated():
    obs = RuleResponseObserver().observe(_fx("A-4"), "10")
    alts = [a for i in obs.uud for a in i.alternatives]
    assert alts and len(alts) == len(set(alts))
    assert all(i.status != UUDObservation.OBSERVED for i in obs.uud)


def test_word_numbers():
    assert word_numbers("двадцать четыре карточки") == [24]
    assert word_numbers("сорока пяти") == [45]
    assert word_numbers("сто двадцать три и девять") == [123, 9]


async def test_fresh_retry_after_failed_repairs(core_factory):
    """Прогон на DeepSeek 24.09: B-3 не прошло проверку после двух починок → задание составляется заново."""

    class StubbornRepair(ScriptedLLM):
        async def complete(self, system, user, **kw):
            if user.startswith("Автоматическая проверка нашла ошибки"):
                self.calls.append({"system": system, "user": user})
                bad_task = copy.deepcopy(variant_draft("basic", 4, 6, 5)["tasks"][2])
                bad_task["solution_steps"][0]["result"] = "99"
                data = {"tasks": [bad_task]}
                from socrat.contracts import LLMResponse, LLMUsage

                return LLMResponse(text="{}", data=data, usage=LLMUsage(model="s", tokens_in=1, tokens_out=1))
            if "ровно 1 задани" in user:  # новое задание взамен непочиненного: корректное «найди ошибку»
                self.calls.append({"system": system, "user": user})
                good = variant_draft("basic", 4, 6, 5)
                data = dict(good, tasks=[good["tasks"][2]])
                from socrat.contracts import LLMResponse, LLMUsage

                return LLMResponse(text="{}", data=data, usage=LLMUsage(model="s", tokens_in=1, tokens_out=1))
            return await super().complete(system, user, **kw)

    bad = copy.deepcopy(variant_draft("basic", 4, 6, 5))
    bad["tasks"][2]["solution_steps"][0]["result"] = "99"
    llm = StubbornRepair(broken={"basic": bad})
    gen, _, _ = core_factory(llm)
    work = await gen.generate(_req())
    t3 = work.variants[0].tasks[2]
    assert t3.task_id == "B-3" and t3.checks.math_verified is True
    assert not [w for w in work.warnings if "B-3" in w.message_ru]
    assert any("ровно 1 задани" in c["user"] for c in llm.calls)
