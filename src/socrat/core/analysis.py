"""Анализ типичной ошибки (сценарий 2): описание педагога → слои ошибки → гипотезы → приёмы.

Реализует socrat.contracts.ErrorAnalyzer.
"""

from __future__ import annotations

import logging
import re
import time

from pydantic import ValidationError

from socrat.config import Settings
from socrat.contracts import (
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    ErrorHypothesis,
    ErrorLayer,
    GenerationError,
    GenerationMeta,
    GuardrailCode,
    GuardrailIssue,
    GuardrailResult,
    KnowledgeBase,
    LLMClient,
    ProgressCallback,
    QuickCheckTask,
    Severity,
    TechniqueRecommendation,
    UUDGroup,
)
from socrat.prompts import PROMPT_VERSION, render

from .drafts import AnalysisDraft, compact_schema
from .guardrails import DIAGNOSIS_RE, Guardrails
from .links import make_link
from .techniques import TechniqueLibrary

logger = logging.getLogger(__name__)

SYSTEM = (
    "Ты — методист и педагог-психолог российской школы. Ты помогаешь педагогу понять возможные причины "
    "типичных ошибок детей и подобрать приёмы. Ты формулируешь только гипотезы, не ставишь диагнозов, "
    "не оцениваешь личность и не выдумываешь нормативные пункты. Отвечаешь строго JSON на русском языке."
)
FALLBACK_TECH = {
    ErrorLayer.OPERATIONAL: ["TECH-02", "TECH-03"],
    ErrorLayer.CONCEPTUAL: ["TECH-06", "TECH-11"],
    ErrorLayer.REGULATORY: ["TECH-01", "TECH-13"],
    ErrorLayer.COMMUNICATIVE: ["TECH-07", "TECH-09"],
}


def _clean(text: str) -> tuple[str, bool]:
    """Убирает диагнозы/ярлыки, если модель всё же их написала."""
    new = DIAGNOSIS_RE.sub("[удалено]", text or "")
    new = re.sub(r"\b(ленив\w*|неспособн\w*|глуп\w*)", "[удалено]", new, flags=re.I)
    return new, new != (text or "")


class LLMErrorAnalyzer:
    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeBase,
        llm: LLMClient,
        techniques: TechniqueLibrary | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self.llm = llm
        self.techniques = techniques or TechniqueLibrary.load()
        self.guard = Guardrails(settings, knowledge)

    async def check(self, req: ErrorAnalysisRequest) -> GuardrailResult:
        return self.guard.check_analysis(req)

    async def analyze(
        self, req: ErrorAnalysisRequest, progress: ProgressCallback | None = None
    ) -> ErrorAnalysisResult:
        t0 = time.monotonic()
        guard = self.guard.check_analysis(req)
        if guard.blocked:
            raise GenerationError(
                "\n".join(i.message_ru for i in guard.issues if i.severity == Severity.BLOCK)
            )
        safe = req
        if progress:
            await progress("Анализирую описание ошибки…")
        outcomes = self.knowledge.get_outcomes(
            req.grade, req.subject_id, query=f"{req.topic} {req.description}", k=20
        )
        by_id = {o.outcome_id: o for o in outcomes}
        user = render(
            "error_analysis",
            grade=req.grade,
            subject_name=req.subject_name,
            topic=safe.topic,
            description=safe.description,
            outcomes=outcomes,
            techniques=self.techniques.all(),
        )
        schema = compact_schema(AnalysisDraft)
        draft, usage, calls = None, {"in": 0, "out": 0, "cost": 0.0, "known": True}, 0
        extra = ""
        model = self.settings.llm_model
        for _ in range(2):
            resp = await self.llm.complete(SYSTEM, user + extra, json_schema=schema)
            calls += 1
            usage["in"] += resp.usage.tokens_in
            usage["out"] += resp.usage.tokens_out
            model = resp.usage.model or model
            if resp.usage.cost_usd is None:
                usage["known"] = False
            else:
                usage["cost"] += resp.usage.cost_usd
            if isinstance(resp.data, dict):
                try:
                    draft = AnalysisDraft.model_validate(resp.data)
                    break
                except ValidationError as e:
                    extra = f"\n\nПредыдущий ответ не прошёл проверку структуры: {str(e)[:400]}. Верни JSON по схеме."
            else:
                extra = "\n\nВерни ТОЛЬКО JSON-объект по схеме."
        if draft is None or not draft.hypotheses:
            raise GenerationError("Модель не смогла разобрать описание. Попробуйте описать ошибку подробнее.")

        warnings = [i for i in guard.issues if i.severity != Severity.BLOCK]
        cleaned_any = False

        def clean(s: str) -> str:
            nonlocal cleaned_any
            out, changed = _clean(s)
            cleaned_any |= changed
            return out

        hypotheses = [
            ErrorHypothesis(
                layer=ErrorLayer(h.layer),
                uud_group=UUDGroup(h.uud_group) if h.uud_group else None,
                statement=clean(h.statement),
                confidence=h.confidence,
                signs_to_check=[clean(s) for s in h.signs_to_check][:4],
            )
            for h in draft.hypotheses[:3]
        ]
        techs: list[TechniqueRecommendation] = []
        for t in draft.techniques:
            ids = self.techniques.filter([t.technique_id])
            if ids and ids[0] not in {x.technique_id for x in techs}:
                lib = self.techniques.get(ids[0])
                techs.append(
                    TechniqueRecommendation(
                        technique_id=lib.technique_id,
                        name=lib.name,
                        how_to_apply=clean(t.how_to_apply),
                        expected_shift=clean(t.expected_shift),
                    )
                )
        if not techs:
            for tid in FALLBACK_TECH[hypotheses[0].layer]:
                lib = self.techniques.get(tid)
                techs.append(
                    TechniqueRecommendation(
                        technique_id=tid,
                        name=lib.name,
                        how_to_apply=lib.essence,
                        expected_shift=lib.expected_shift,
                    )
                )
            warnings.append(
                GuardrailIssue(
                    code=GuardrailCode.OK,
                    severity=Severity.INFO,
                    message_ru="Приёмы подобраны по слою ошибки из библиотеки.",
                )
            )
        links = [
            make_link(self.knowledge, by_id[i], "Дефицит, описанный педагогом, связан с этим результатом.")
            for i in dict.fromkeys(draft.outcome_ids)
            if i in by_id
        ][:3]
        summary = clean(draft.summary)
        reflection_q = clean(draft.teacher_reflection_question)
        if cleaned_any:
            warnings.append(
                GuardrailIssue(
                    code=GuardrailCode.DIAGNOSIS_REQUEST,
                    severity=Severity.INFO,
                    message_ru="Из ответа удалены формулировки, похожие на диагнозы или оценки личности.",
                )
            )
        return ErrorAnalysisResult(
            request=req,
            summary=summary,
            hypotheses=hypotheses,
            outcome_links=links,
            techniques=techs[:3],
            quick_checks=[
                QuickCheckTask(
                    student_text=q.student_text,
                    expected_answer=q.expected_answer,
                    what_it_checks=q.what_it_checks,
                )
                for q in draft.quick_checks[:2]
            ],
            teacher_reflection_question=reflection_q,
            warnings=warnings,
            meta=GenerationMeta(
                prompt_version=PROMPT_VERSION,
                provider=self.settings.llm_base_url,
                model=model,
                tokens_in=usage["in"],
                tokens_out=usage["out"],
                cost_usd=round(usage["cost"], 6) if usage["known"] else None,
                llm_calls=calls,
                duration_s=round(time.monotonic() - t0, 1),
            ),
        )
