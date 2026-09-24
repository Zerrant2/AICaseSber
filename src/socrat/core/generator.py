"""Генератор диагностической работы: чертёж (код) → задания (LLM) → проверки (код) → починка.

Реализует socrat.contracts.WorkGenerator.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from pydantic import ValidationError

from socrat.config import Settings
from socrat.contracts import (
    LABELS_RU,
    PERSONAL_NOTE,
    STUDENT_VARIANT_LABEL,
    CoverageSummary,
    DiagnosticWork,
    ErrorLayer,
    FeedbackRepository,
    GenerationError,
    GenerationMeta,
    GenerationRequest,
    GuardrailCode,
    GuardrailIssue,
    GuardrailResult,
    KnowledgeBase,
    Level,
    LLMClient,
    Outcome,
    OutcomeLink,
    OutcomeType,
    ProgressCallback,
    Reflection,
    Severity,
    SolutionStep,
    SourceDocument,
    Task,
    TaskChecks,
    TaskKind,
    TimePlan,
    TypicalError,
    UUDGroup,
    UUDIndicator,
    UUDObservation,
    Variant,
)
from socrat.prompts import PROMPT_VERSION, render

from .drafts import TaskDraft, VariantDraft, compact_schema
from .guardrails import Guardrails
from .links import make_link
from .mathcheck import answer_value, evaluate, fmt, parse_number, parse_number_mapping, replace_numbers
from .specs import (
    DEFAULT_REFLECTION,
    KINDS,
    LEVELS,
    MATH_SUBJECTS,
    READING_MINUTES,
    REFLECTION_MINUTES,
    Slot,
    build_blueprint,
    estimate_task_minutes,
    number_limit,
)
from .techniques import TechniqueLibrary
from .validators import (
    check_language,
    check_leak,
    check_levels,
    check_math,
    check_uud,
    fix_numeral_agreement,
)
from .wish import OperationWish, check_operations, parse_operation_wish

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "1.0.0"
LETTER = {Level.EASY: "A", Level.BASIC: "B", Level.ADVANCED: "C"}
_LONG_ANSWER = {TaskKind.MULTI_STEP, TaskKind.TWO_WAYS, TaskKind.PLAN_FIRST, TaskKind.FIND_ERROR}
GROUP_TO_TYPE = {
    UUDGroup.COGNITIVE: OutcomeType.COGNITIVE,
    UUDGroup.REGULATORY: OutcomeType.REGULATORY,
    UUDGroup.COMMUNICATIVE: OutcomeType.COMMUNICATIVE,
}
SCALE_HINTS = {
    UUDObservation.OBSERVED: "Действие явно видно в записи.",
    UUDObservation.PARTIAL: "Видна часть действия (например, проверка без вывода).",
    UUDObservation.NOT_SHOWN: "Записан только ответ — действие не показано (это не значит «не умеет»).",
}
BASE_LIMITATIONS = [
    "Работа показывает соответствие ВЫБРАННЫМ планируемым результатам, а не всему ФГОС.",
    "Коды результатов — внутренние идентификаторы системы, а не номера пунктов ФГОС.",
    "Время — расчётная оценка, не измерение на детях.",
    "Уровни — уровни задания (опоры и самостоятельность), а не оценка способностей ребёнка.",
    "Материал подготовлен ИИ и проверен кодом (арифметика, ссылки, отсутствие ответов у ученика); "
    "перед использованием его просматривает педагог.",
]


@dataclass
class _Ctx:
    req: GenerationRequest
    outcomes: dict[str, Outcome]
    ranked: list[Outcome]
    is_math: bool
    limit: int | None
    primary: bool
    system: str
    usage: dict = field(
        default_factory=lambda: {"in": 0, "out": 0, "cost": 0.0, "calls": 0, "repairs": 0, "cost_known": True}
    )
    model: str = ""


class LLMWorkGenerator:
    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeBase,
        llm: LLMClient,
        feedback: FeedbackRepository | None = None,
        techniques: TechniqueLibrary | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self.llm = llm
        self.feedback = feedback
        self.techniques = techniques or TechniqueLibrary.load()
        self.guard = Guardrails(settings, knowledge)

    # ================================================================== public API

    async def check_request(self, req: GenerationRequest) -> GuardrailResult:
        return self.guard.check_request(req)

    async def generate(
        self, req: GenerationRequest, progress: ProgressCallback | None = None
    ) -> DiagnosticWork:
        t0 = time.monotonic()
        guard = self.guard.check_request(req)
        if guard.blocked:
            raise GenerationError(
                "\n".join(i.message_ru for i in guard.issues if i.severity == Severity.BLOCK)
            )
        req = self._sanitize(req, guard)
        await _say(progress, "Подбираю планируемые результаты по ФГОС…")
        ctx = await self._context(req)

        levels = req.level.levels()
        await _say(
            progress, f"Составляю задания ({', '.join(LABELS_RU[lv.value].lower() for lv in levels)})…"
        )
        results = await asyncio.gather(
            *(self._variant(ctx, lv, progress) for lv in levels), return_exceptions=True
        )
        variants: list[Variant] = []
        warnings: list[GuardrailIssue] = [i for i in guard.issues if i.severity != Severity.BLOCK]
        title = req.topic
        for lv, res in zip(levels, results, strict=True):
            if isinstance(res, Exception):
                logger.exception("variant %s failed", lv, exc_info=res)
                if isinstance(res, GenerationError):
                    raise res
                raise GenerationError(
                    "Не удалось получить ответ от языковой модели. Попробуйте ещё раз через минуту.",
                    repr(res),
                ) from res
            variant, draft_title, var_warnings = res
            variants.append(variant)
            _ = draft_title  # название работы берём из темы учителя: заголовок модели мог содержать уровень
            warnings.extend(var_warnings)

        await _say(progress, "Проверяю уровни и покрытие УУД…")
        for w in check_levels(variants):
            warnings.append(GuardrailIssue(code=GuardrailCode.OK, severity=Severity.INFO, message_ru=w))
        work = self._assemble(ctx, variants, title, warnings)
        work.meta.duration_s = round(time.monotonic() - t0, 1)
        return work

    async def regenerate_task(
        self,
        work: DiagnosticWork,
        level: Level,
        task_number: int,
        wish: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> DiagnosticWork:
        work = work.model_copy(deep=True)
        variant = work.variant(level)
        if variant is None or not (1 <= task_number <= len(variant.tasks)):
            raise GenerationError("Такого задания нет в работе.")
        old = variant.tasks[task_number - 1]
        ctx = await self._context(work.request)
        mapping = parse_number_mapping(wish)
        if mapping and old.kind != TaskKind.FIND_ERROR:
            await _say(progress, "Меняю числа и пересчитываю решение кодом…")
            new = self._renumber(old, mapping, ctx)
        else:
            await _say(progress, f"Составляю новое задание {task_number}…")
            ops = parse_operation_wish(wish)
            kind = old.kind
            if len(ops.required) >= 2 and kind in (TaskKind.WORD_PROBLEM, TaskKind.COMPUTE):
                kind = TaskKind.MULTI_STEP  # два разных действия — это составная задача
            slot = Slot(
                number=task_number, kind=kind, target=_primary_group(old), trigger_hint=_hint_for(old)
            )
            new, errs = await self._single_task(
                ctx,
                level,
                slot,
                avoid=[old.student_text],
                wish=wish,
                ops=ops,
                repairs=self.settings.llm_max_repairs,
            )
            op_errs = check_operations(new, ops)
            if op_errs:
                new.checks.notes.append("Пожелание учителя выполнено не полностью.")
                work.warnings.append(
                    GuardrailIssue(
                        code=GuardrailCode.OK,
                        severity=Severity.WARN,
                        message_ru=(
                            f"Задание {new.task_id}: пожелание «{wish}» выполнить не удалось — "
                            f"{ops.describe_ru()}, а модель составила задачу с другими действиями. "
                            "Попробуйте сформулировать пожелание иначе или замените задание ещё раз."
                        ),
                    )
                )
            other = [e for e in errs if e not in op_errs]
            if other:
                work.warnings.append(
                    GuardrailIssue(
                        code=GuardrailCode.OK,
                        severity=Severity.WARN,
                        message_ru=f"Задание {new.task_id}: " + "; ".join(other[:3]),
                    )
                )
        variant.tasks[task_number - 1] = new
        variant.time_plan = self._time_plan(variant.tasks)
        work.coverage = self._coverage(work.variants)
        work.meta.llm_calls += ctx.usage["calls"]
        work.meta.tokens_in += ctx.usage["in"]
        work.meta.tokens_out += ctx.usage["out"]
        if ctx.usage["cost_known"] and work.meta.cost_usd is not None:
            work.meta.cost_usd = round(work.meta.cost_usd + ctx.usage["cost"], 6)
        return work

    # ================================================================== context

    def _sanitize(self, req: GenerationRequest, guard: GuardrailResult) -> GenerationRequest:
        """Запрос передаётся как есть. Автозамена имён на «[ученик]» убрана 24.09: она принимала
        фамилии учёных и писателей из темы («Законы Ньютона») за данные детей."""
        return req

    async def _context(self, req: GenerationRequest) -> _Ctx:
        ranked = self.knowledge.get_outcomes(req.grade, req.subject_id, query=req.topic, k=40)
        outcomes = {o.outcome_id: o for o in ranked}
        examples = []
        if self.feedback is not None:
            try:
                examples = await self.feedback.top_examples(req.grade, req.subject_id, k=3)
            except Exception:  # обратная связь не должна ломать генерацию
                logger.warning("feedback examples unavailable", exc_info=True)
        is_math = req.subject_id in MATH_SUBJECTS
        limit = number_limit(req.grade, req.subject_id, req.topic)
        system = render(
            "system_methodologist",
            role_label={
                "teacher": "педагогу",
                "methodist": "методисту",
                "psychologist": "педагогу-психологу",
                "admin": "педагогу",
            }[req.author_role.value],
            grade=req.grade,
            subject_name=req.subject_name,
            topic=req.topic,
            number_limit=limit if is_math else None,
            primary=is_math and req.grade <= 4,
            outcomes=ranked,
            techniques=self.techniques.all(),
            examples=examples,
        )
        return _Ctx(
            req=req,
            outcomes=outcomes,
            ranked=ranked,
            is_math=is_math,
            limit=limit,
            primary=is_math and req.grade <= 4,
            system=system,
        )

    # ================================================================== variant

    async def _variant(self, ctx: _Ctx, level: Level, progress: ProgressCallback | None):
        req = ctx.req
        slots = build_blueprint(level, req.task_count, req.uud_focus, req.subject_id)
        draft = await self._ask_variant(ctx, level, slots)
        by_num = {t.number: t for t in draft.tasks}
        drafts = [
            by_num.get(s.number) or (draft.tasks[s.number - 1] if s.number - 1 < len(draft.tasks) else None)
            for s in slots
        ]
        if any(d is None for d in drafts):
            raise GenerationError("Модель вернула не все задания. Попробуйте ещё раз.")

        tasks, errors = self._build_and_check(ctx, level, slots, drafts)
        for attempt in range(1, self.settings.llm_max_repairs + 1):
            if not errors:
                break
            await _say(
                progress, f"Исправляю найденные ошибки ({LABELS_RU[level.value].lower()}, попытка {attempt})…"
            )
            bad = sorted(errors)
            fixed = await self._repair(ctx, [drafts[n - 1] for n in bad], [e for n in bad for e in errors[n]])
            for d in fixed:
                if 1 <= d.number <= len(drafts):
                    drafts[d.number - 1] = d
            tasks, errors = self._build_and_check(ctx, level, slots, drafts)

        if errors:
            # Починка не помогла — составляем такие задания заново (один раз, параллельно).
            await _say(
                progress,
                f"Составляю заново задания {', '.join(map(str, sorted(errors)))} "
                f"({LABELS_RU[level.value].lower()})…",
            )
            numbers = sorted(errors)
            fresh = await asyncio.gather(
                *(
                    self._single_task(ctx, level, slots[n - 1], avoid=[tasks[n - 1].student_text])
                    for n in numbers
                ),
                return_exceptions=True,
            )
            for n, res in zip(numbers, fresh, strict=True):
                if isinstance(res, Exception):
                    logger.warning("fresh retry for task %s failed: %r", n, res)
                    continue
                new_task, new_errs = res
                if len(new_errs) < len(errors[n]):
                    tasks[n - 1] = new_task
                    if new_errs:
                        errors[n] = new_errs
                    else:
                        del errors[n]

        warnings = []
        for n, errs in sorted(errors.items()):
            tasks[n - 1].checks.notes.extend(errs)
            warnings.append(
                GuardrailIssue(
                    code=GuardrailCode.OK,
                    severity=Severity.WARN,
                    message_ru=f"Задание {tasks[n - 1].task_id} не прошло автопроверку — просмотрите его: "
                    + "; ".join(errs[:2]),
                )
            )
        questions = [q for q in draft.reflection_questions if q.strip()][:4] or DEFAULT_REFLECTION
        spec = LEVELS[level]
        variant = Variant(
            level=level,
            student_label=STUDENT_VARIANT_LABEL[level],
            instruction=spec.instruction,
            tasks=tasks,
            reflection=Reflection(
                questions=questions,
                minutes=REFLECTION_MINUTES,
                reading_guide=(
                    "Фиксируйте, какой способ ребёнок назвал, где проверял и что отметил как трудность. "
                    "Нет правильных ответов о чувствах; это материал для беседы, а не оценка личности."
                ),
            ),
            time_plan=self._time_plan(tasks),
            level_rationale=(
                draft.level_rationale.strip() or f"Уровень «{spec.title_ru}»: {spec.support_rule}"
            ),
        )
        return variant, draft.title.strip(), warnings

    async def _ask_variant(
        self,
        ctx: _Ctx,
        level: Level,
        slots: list[Slot],
        avoid=None,
        wish=None,
        ops: OperationWish | None = None,
    ) -> VariantDraft:
        req = ctx.req
        user = render(
            "write_variant",
            level_title=LEVELS[level].title_ru,
            level=LEVELS[level],
            slots=slots,
            topic=req.topic,
            grade=req.grade,
            subject_name=req.subject_name,
            teacher_note=wish or req.teacher_note,
            required_ops=ops.describe_ru() if ops else "",
            avoid=avoid or [],
        )
        schema = compact_schema(VariantDraft)
        last_err = ""
        for _ in range(2):
            resp = await self.llm.complete(ctx.system, user + last_err, json_schema=schema)
            self._account(ctx, resp)
            data = resp.data
            if isinstance(data, dict):
                try:
                    return VariantDraft.model_validate(data)
                except ValidationError as e:
                    last_err = f"\n\nПредыдущий ответ не прошёл проверку структуры: {str(e)[:500]}. Верни JSON строго по схеме."
            else:
                last_err = "\n\nПредыдущий ответ не был JSON. Верни ТОЛЬКО JSON-объект по схеме."
        raise GenerationError("Модель вернула ответ в неверном формате. Попробуйте ещё раз.")

    async def _repair(self, ctx: _Ctx, drafts: list[TaskDraft], errors: list[str]) -> list[TaskDraft]:
        ctx.usage["repairs"] += 1
        user = render(
            "repair",
            errors=errors,
            tasks_json=json.dumps([d.model_dump() for d in drafts], ensure_ascii=False, indent=1),
        )
        schema = {
            "type": "object",
            "properties": {"tasks": {"type": "array", "items": compact_schema(TaskDraft)}},
            "required": ["tasks"],
        }
        resp = await self.llm.complete(ctx.system, user, json_schema=schema)
        self._account(ctx, resp)
        data = resp.data if isinstance(resp.data, dict) else {}
        out = []
        for item in data.get("tasks", []) if isinstance(data.get("tasks"), list) else []:
            try:
                out.append(TaskDraft.model_validate(item))
            except ValidationError:
                continue
        return out

    async def _single_task(
        self,
        ctx: _Ctx,
        level: Level,
        slot: Slot,
        avoid=None,
        wish=None,
        ops: OperationWish | None = None,
        repairs: int = 1,
    ):
        one = Slot(
            number=1,
            kind=slot.kind,
            target=slot.target,
            trigger_hint=slot.trigger_hint,
            extra_groups=slot.extra_groups,
        )
        ops = ops or OperationWish()
        draft = await self._ask_variant(ctx, level, [one], avoid=avoid, wish=wish, ops=ops)
        if not draft.tasks:
            raise GenerationError("Модель не вернула задание.")
        d = draft.tasks[0].model_copy(update={"number": slot.number})

        def run(dr):
            tasks, errors = self._build_and_check(ctx, level, [slot], [dr])
            errs = [e for es in errors.values() for e in es] + check_operations(tasks[0], ops)
            return tasks[0], errs

        task, errs = run(d)
        for _ in range(max(1, repairs)):
            if not errs:
                break
            fixed = await self._repair(ctx, [d], errs)
            if not fixed:
                break
            d = fixed[0].model_copy(update={"number": slot.number})
            task, errs = run(d)
        return task, errs

    # ================================================================== build + checks

    def _build_and_check(
        self, ctx: _Ctx, level: Level, slots: list[Slot], drafts: list[TaskDraft]
    ) -> tuple[list[Task], dict[int, list[str]]]:
        tasks: list[Task] = []
        errors: dict[int, list[str]] = {}
        for slot, d in zip(slots, drafts, strict=True):
            task = self._to_task(ctx, level, slot, d)
            errs = []
            errs += check_math(task, is_math=ctx.is_math, limit=ctx.limit, primary=ctx.primary)
            errs += check_leak(task)
            errs += check_uud(task, slot)
            errs += check_language(task)
            self._finalize_links(ctx, task)
            task.estimated_minutes = estimate_task_minutes(
                task.kind, level, ctx.req.grade, len((task.student_text + " " + (task.support or "")).split())
            )
            tasks.append(task)
            if errs:
                errors[slot.number] = errs
        return tasks, errors

    def _to_task(self, ctx: _Ctx, level: Level, slot: Slot, d: TaskDraft) -> Task:
        indicators = []
        for u in d.uud:
            group = UUDGroup(u.group)
            oid = self._pick_outcome(ctx, u.outcome_id, GROUP_TO_TYPE[group])
            indicators.append(
                UUDIndicator(
                    group=group,
                    outcome_id=oid or "—",
                    action=u.action,
                    trigger=u.trigger,
                    evidence=u.evidence,
                    scale_hints=dict(SCALE_HINTS),
                )
            )
        subject_ids = []
        for sid in d.subject_outcome_ids[:3]:
            oid = self._pick_outcome(ctx, sid, OutcomeType.SUBJECT, fallback=False)
            if oid and oid not in subject_ids:
                subject_ids.append(oid)
        notes = []
        if not subject_ids:
            fb = self._pick_outcome(ctx, None, OutcomeType.SUBJECT)
            if fb:
                subject_ids.append(fb)
                notes.append("Предметный результат подобран кодом по теме (модель не указала допустимый ID).")
        support = (
            d.support.strip() if d.support and d.support.strip().lower() not in {"null", "нет", "-"} else None
        )
        if level != Level.EASY and support and "подсказ" in support.lower():
            notes.append(
                "Подсказка в неэлементарном уровне — проверьте, не снижает ли она самостоятельность."
            )
        return Task(
            task_id=f"{LETTER[level]}-{slot.number}",
            number=slot.number,
            level=level,
            kind=slot.kind,
            student_text=fix_numeral_agreement(d.student_text.strip()),
            support=fix_numeral_agreement(support) if support else None,
            answer_lines=5 if slot.kind in _LONG_ANSWER else 3,
            subject_goal=d.subject_goal,
            meta_goal=d.meta_goal,
            expected_answer=d.expected_answer.strip(),
            solution_steps=[
                SolutionStep(
                    text=_step_text(s.text, s.expression, s.result),
                    expression=s.expression or None,
                    result=s.result,
                )
                for s in d.solution_steps
            ],
            alternative_solutions=d.alternative_solutions,
            outcome_links=[self._link(ctx, oid, d.subject_goal) for oid in subject_ids],
            uud_indicators=indicators,
            personal_orientation=(d.personal_orientation or "").strip() or None,
            typical_errors=[
                TypicalError(
                    description=e.description,
                    layer=ErrorLayer(e.layer),
                    interpretation=e.interpretation,
                    teacher_move=e.teacher_move,
                )
                for e in d.typical_errors
            ],
            oral_questions=d.oral_questions[:3],
            technique_ids=self.techniques.filter(d.technique_ids) or list(KINDS[slot.kind].techniques[:1]),
            conducting_note=d.conducting_note,
            estimated_minutes=1.0,
            checks=TaskChecks(notes=notes),
        )

    def _pick_outcome(
        self, ctx: _Ctx, oid: str | None, otype: OutcomeType, fallback: bool = True
    ) -> str | None:
        if oid and oid in ctx.outcomes and ctx.outcomes[oid].type == otype:
            return oid
        if oid:
            o = self.knowledge.get_outcome(oid)
            if o is not None and o.type == otype and (o.grade in (None, ctx.req.grade)):
                ctx.outcomes[o.outcome_id] = o
                return o.outcome_id
        if not fallback:
            return None
        return next((o.outcome_id for o in ctx.ranked if o.type == otype), None)

    def _link(self, ctx: _Ctx, oid: str, rationale: str) -> OutcomeLink:
        return make_link(self.knowledge, ctx.outcomes[oid], rationale)

    def _finalize_links(self, ctx: _Ctx, task: Task) -> None:
        links = [lk for lk in task.outcome_links if lk.type == OutcomeType.SUBJECT]
        seen = {lk.outcome_id for lk in links}
        for ind in task.uud_indicators:
            if ind.outcome_id in ctx.outcomes and ind.outcome_id not in seen:
                links.append(
                    self._link(ctx, ind.outcome_id, f"В формулировке есть требование: «{ind.trigger}».")
                )
                seen.add(ind.outcome_id)
        if task.personal_orientation:
            pid = self._pick_outcome(ctx, None, OutcomeType.PERSONAL)
            if pid and pid not in seen:
                links.append(self._link(ctx, pid, f"{task.personal_orientation} {PERSONAL_NOTE}"))
        task.outcome_links = links
        task.checks.outcomes_verified = all(lk.verified for lk in links)

    # ================================================================== deterministic renumber (T11)

    def _renumber(self, old: Task, mapping: dict[int, int], ctx: _Ctx) -> Task:
        t = old.model_copy(deep=True)
        t.student_text = fix_numeral_agreement(replace_numbers(t.student_text, mapping))
        t.support = fix_numeral_agreement(replace_numbers(t.support, mapping)) if t.support else None
        # операнды: результаты прежних шагов → новые значения; остальные числа — по словарю учителя
        results_map: dict[str, str] = {}
        old_expected = answer_value(old.expected_answer)
        last = None
        new_expected = None
        for s in t.solution_steps:
            if not s.expression:
                continue

            def sub(m: re.Match, rm=results_map) -> str:
                tok = m.group(0)
                if tok in rm:
                    return rm[tok]
                return str(mapping.get(int(tok), tok)) if tok.isdigit() else tok

            expr = re.sub(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", sub, s.expression)
            try:
                val = evaluate(expr)
            except Exception as e:
                raise GenerationError(f"С такими числами решение не вычисляется: {e}") from e
            if s.result:
                results_map[s.result.strip()] = fmt(val)
                if new_expected is None and parse_number(s.result) == old_expected:
                    new_expected = val
            s.text = f"{expr.replace('*', ' × ').replace('/', ' : ')} = {fmt(val)}"
            s.expression, s.result, s.verified = expr, fmt(val), None
            last = val

        def sub_answer(m: re.Match) -> str:
            tok = m.group(0)
            if tok in results_map:
                return results_map[tok]
            return str(mapping.get(int(tok), tok)) if tok.isdigit() else tok

        t.expected_answer = re.sub(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])", sub_answer, t.expected_answer)
        _ = (new_expected, last)
        t.checks = TaskChecks(
            notes=[
                f"Числа изменены по запросу учителя ({', '.join(f'{a}→{b}' for a, b in mapping.items())}); решение пересчитано кодом."
            ]
        )
        errs = check_math(t, is_math=ctx.is_math, limit=ctx.limit, primary=ctx.primary) + check_leak(t)
        if errs:
            raise GenerationError("С новыми числами задание некорректно: " + "; ".join(errs[:2]))
        t.estimated_minutes = estimate_task_minutes(
            t.kind, t.level, ctx.req.grade, len(t.student_text.split())
        )
        return t

    # ================================================================== assembly

    def _time_plan(self, tasks: list[Task]) -> TimePlan:
        tm = sum(t.estimated_minutes for t in tasks)
        total = READING_MINUTES + tm + REFLECTION_MINUTES
        lo, hi = self.settings.work_minutes_min, self.settings.work_minutes_max
        return TimePlan(
            reading_minutes=READING_MINUTES,
            tasks_minutes=tm,
            reflection_minutes=REFLECTION_MINUTES,
            total_minutes=total,
            minutes_min=lo,
            minutes_max=hi,
            within_range=lo <= total <= hi,
        )

    def _coverage(self, variants: list[Variant]) -> CoverageSummary:
        groups = sorted({i.group for v in variants for t in v.tasks for i in t.uud_indicators})
        ids = sorted({lk.outcome_id for v in variants for t in v.tasks for lk in t.outcome_links})
        return CoverageSummary(
            uud_groups=groups, outcome_ids=ids, missing_groups=[g for g in UUDGroup if g not in groups]
        )

    def _assemble(
        self, ctx: _Ctx, variants: list[Variant], title: str, warnings: list[GuardrailIssue]
    ) -> DiagnosticWork:
        req = ctx.req
        coverage = self._coverage(variants)
        limitations = list(BASE_LIMITATIONS)
        if not ctx.ranked:
            limitations.insert(
                0,
                "Для этого класса и предмета в базе нет планируемых результатов — карта соответствия пуста.",
            )
        if coverage.missing_groups and req.uud_focus.value == "balanced":
            names = ", ".join(LABELS_RU[g.value].lower() for g in coverage.missing_groups)
            warnings.append(
                GuardrailIssue(
                    code=GuardrailCode.OK,
                    severity=Severity.WARN,
                    message_ru=f"В работе не представлены {names} УУД.",
                )
            )
        for v in variants:
            if not v.time_plan.within_range:
                warnings.append(
                    GuardrailIssue(
                        code=GuardrailCode.TIME_OUT_OF_RANGE,
                        severity=Severity.INFO,
                        message_ru=f"{v.student_label}: расчётное время {v.time_plan.total_minutes:g} мин — вне "
                        f"{v.time_plan.minutes_min}–{v.time_plan.minutes_max} мин.",
                    )
                )
        source_ids = {lk.source_id for v in variants for t in v.tasks for lk in t.outcome_links}
        sources: list[SourceDocument] = [
            s for s in (self.knowledge.get_source(i) for i in sorted(source_ids)) if s
        ]
        for extra in ("FGOS-NOO-286", "FGOS-OOO-287", "FGOS-SOO-413"):
            s = self.knowledge.get_source(extra)
            if s and s not in sources and req.grade in (s.grades or [req.grade]):
                sources.append(s)
        u = ctx.usage
        meta = GenerationMeta(
            generator_version=GENERATOR_VERSION,
            prompt_version=PROMPT_VERSION,
            provider=self.settings.llm_base_url,
            model=ctx.model or self.settings.llm_model,
            tokens_in=u["in"],
            tokens_out=u["out"],
            cost_usd=round(u["cost"], 6) if u["cost_known"] else None,
            llm_calls=u["calls"],
            repairs=u["repairs"],
        )
        return DiagnosticWork(
            request=req,
            title=title[:120],
            variants=variants,
            coverage=coverage,
            limitations=limitations,
            sources=sources,
            warnings=_dedupe(warnings),
            meta=meta,
        )

    def _account(self, ctx: _Ctx, resp) -> None:
        ctx.usage["calls"] += 1
        ctx.usage["in"] += resp.usage.tokens_in
        ctx.usage["out"] += resp.usage.tokens_out
        ctx.model = resp.usage.model or ctx.model
        if resp.usage.cost_usd is None:
            ctx.usage["cost_known"] = False
        else:
            ctx.usage["cost"] += resp.usage.cost_usd


# ======================================================================== helpers


async def _say(progress: ProgressCallback | None, text: str) -> None:
    if progress is None:
        return
    try:
        await progress(text)
    except Exception:  # прогресс не должен ломать генерацию
        logger.debug("progress callback failed", exc_info=True)


def _step_text(text: str, expression: str | None, result: str | None) -> str:
    """Если модель описала шаг словами без вычисления — дописываем «(6 · 4 = 24)», чтобы учитель видел расчёт."""
    text = (text or "").strip()
    if not expression:
        return text
    pretty = expression.replace("*", " · ").replace("/", " : ").replace("-", " − ").replace("+", " + ")
    pretty = re.sub(r"\s+", " ", pretty).strip()
    shown = f"{pretty} = {result}" if result else pretty
    has_calc = bool(re.search(r"\d\s*[·×x*:/+\-−]\s*\d", text))
    return text if has_calc else f"{text} ({shown})".strip()


def _primary_group(task: Task) -> UUDGroup:
    return task.uud_indicators[0].group if task.uud_indicators else KINDS[task.kind].groups[0]


def _hint_for(task: Task) -> str:
    return task.uud_indicators[0].trigger if task.uud_indicators else "Запиши решение и объясни выбор"


def _dedupe(items: list[GuardrailIssue]) -> list[GuardrailIssue]:
    seen, out = set(), []
    for i in items:
        if i.message_ru not in seen:
            seen.add(i.message_ru)
            out.append(i)
    return out
