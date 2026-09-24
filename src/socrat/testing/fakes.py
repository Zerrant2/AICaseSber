"""Фейковые реализации всех протоколов — для параллельной разработки и тестов.

* Бот, экспорт и хранилище работают на FakeWorkGenerator/FakeErrorAnalyzer/FakeKnowledgeBase
  без LLM и нормативной базы. Включается USE_FAKES=true в .env (демо-режим).
* Ядро тестируется на FakeKnowledgeBase (данные кейса SK01) и сценарной LLM.
* LocalKnowledgeBase повторяет сигнатуры FakeKnowledgeBase.

"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from socrat.contracts import (
    ChunkKind,
    DiagnosticWork,
    DocType,
    EduLevel,
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    Feedback,
    GenerationRequest,
    GuardrailCode,
    GuardrailIssue,
    GuardrailResult,
    KnowledgeStatus,
    Level,
    LLMResponse,
    LLMUsage,
    NewTeacherCredentials,
    Outcome,
    OutcomeType,
    ProgressCallback,
    Rating,
    ReflectionObservation,
    ResponseObservation,
    Role,
    SearchHit,
    Severity,
    SourceDocument,
    Subject,
    SubjectObservation,
    Task,
    TeacherAccount,
    TopicCheck,
    UpdateReport,
    UsageRecord,
    UsageSummary,
    UUDGroup,
    UUDObservation,
    UUDObservationItem,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "data" / "fixtures"
CASE_REF = ROOT / "data" / "reference" / "sk01"

_TYPE_MAP = {
    "предметный": OutcomeType.SUBJECT,
    "познавательные УУД": OutcomeType.COGNITIVE,
    "регулятивные УУД": OutcomeType.REGULATORY,
    "коммуникативные УУД": OutcomeType.COMMUNICATIVE,
    "личностная направленность": OutcomeType.PERSONAL,
}

# Упрощённый учебный план для кнопок (реальный список даёт knowledge по ФОП).
_SUBJECTS_NOO = [
    ("russian", "Русский язык"),
    ("literary_reading", "Литературное чтение"),
    ("math", "Математика"),
    ("world", "Окружающий мир"),
]
_SUBJECTS_OOO = [
    ("russian", "Русский язык"),
    ("literature", "Литература"),
    ("math", "Математика"),
    ("algebra", "Алгебра"),
    ("geometry", "Геометрия"),
    ("history", "История"),
    ("biology", "Биология"),
    ("physics", "Физика"),
]


# ==================================== knowledge =========================================


class FakeKnowledgeBase:
    """Знает только справочник кейса SK01 (математика, 3 класс, 6 результатов)."""

    def __init__(self) -> None:
        ref = json.loads((CASE_REF / "outcomes_reference.json").read_text(encoding="utf-8"))
        reg = json.loads((CASE_REF / "source_registry.json").read_text(encoding="utf-8"))
        self._outcomes = [
            Outcome(
                outcome_id=o["outcome_id"],
                type=_TYPE_MAP[o["type"]],
                text=o["paraphrase"],
                quote_is_verbatim=False,
                grade=3,
                subject_id="math" if _TYPE_MAP[o["type"]] == OutcomeType.SUBJECT else None,
                source_id=o["source_id"],
                section=o["section"],
                page=o["page"],
                source_url=o["source_url"],
            )
            for o in ref["outcomes"]
        ]
        self._sources = {
            s["source_id"]: SourceDocument(
                source_id=s["source_id"],
                title=s["title"],
                url=s["url"],
                doc_type=DocType.FRP if s["source_id"].startswith("FRP") else DocType.FGOS,
                edu_levels=[EduLevel.NOO],
                subject_id="math" if s["source_id"].startswith("FRP") else None,
                grades=[1, 2, 3, 4],
            )
            for s in reg
        }

    def list_subjects(self, grade: int) -> list[Subject]:
        base = _SUBJECTS_NOO if grade <= 4 else _SUBJECTS_OOO
        return [Subject(subject_id=i, name=n, grades=[grade]) for i, n in base]

    def get_subject(self, subject_id: str) -> Subject | None:
        for i, n in _SUBJECTS_NOO + _SUBJECTS_OOO:
            if i == subject_id:
                return Subject(subject_id=i, name=n)
        return None

    def check_topic(self, grade: int, subject_id: str, topic: str) -> TopicCheck:
        t = topic.lower()
        ok = (
            grade == 3
            and subject_id == "math"
            and any(w in t for w in ("умнож", "делен", "задач", "табли", "площад", "периметр"))
        )
        return TopicCheck(
            in_program=ok,
            confidence=0.9 if ok else 0.2,
            matched_topics=["Умножение и деление в пределах 100"] if ok else [],
            suggestions=[] if ok else ["Умножение и деление в пределах 100", "Задачи в одно-два действия"],
        )

    def get_outcomes(self, grade, subject_id, types=None, query=None, k=30) -> list[Outcome]:
        if grade != 3 or subject_id != "math":
            return []
        res = [o for o in self._outcomes if not types or o.type in types]
        return res[:k]

    def get_outcome(self, outcome_id: str) -> Outcome | None:
        return next((o for o in self._outcomes if o.outcome_id == outcome_id), None)

    def search(self, query, grade=None, subject_id=None, kinds=None, k=8, include_school_materials=True):
        return []  # фейк без полнотекстовой базы

    def get_page_text(self, source_id: str, page: int) -> str | None:
        return None

    def get_source(self, source_id: str) -> SourceDocument | None:
        return self._sources.get(source_id)

    def status(self) -> KnowledgeStatus:
        return KnowledgeStatus(
            documents=list(self._sources.values()),
            outcomes_total=len(self._outcomes),
            ready=True,
            problems=["FAKE: только справочник кейса SK01"],
        )

    async def update(self, progress: ProgressCallback | None = None) -> UpdateReport:
        if progress:
            await progress("FAKE: обновление не требуется")
        return UpdateReport(unchanged=list(self._sources))

    async def ingest_school_material(self, filename, content, progress=None) -> SourceDocument:
        return SourceDocument(
            source_id=f"SCHOOL-{secrets.token_hex(3)}",
            title=filename,
            url="local://" + filename,
            doc_type=DocType.SCHOOL_MATERIAL,
            is_normative=False,
        )


# ====================================== core ============================================


class FakeWorkGenerator:
    """Отдаёт фикстуру data/fixtures/work_math3_all.json, подрезанную под запрос."""

    def __init__(self, delay_s: float = 1.0) -> None:
        self.delay_s = delay_s
        self._fixture = DiagnosticWork.model_validate_json(
            (FIXTURES / "work_math3_all.json").read_text(encoding="utf-8")
        )

    async def check_request(self, req: GenerationRequest) -> GuardrailResult:
        issues: list[GuardrailIssue] = []
        if req.requested_minutes is not None and not (
            req.minutes_min <= req.requested_minutes <= req.minutes_max
        ):
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.TIME_OUT_OF_RANGE,
                    severity=Severity.WARN,
                    message_ru=f"Запрошено {req.requested_minutes} мин. Прототип рассчитан на работы 15–20 минут.",
                    suggestions=["15 минут", "18 минут", "20 минут"],
                )
            )
        if not (req.grade == 3 and req.subject_id == "math"):
            issues.append(
                GuardrailIssue(
                    code=GuardrailCode.TOPIC_NOT_IN_PROGRAM,
                    severity=Severity.WARN,
                    message_ru="FAKE-режим знает только математику 3 класса. Будет выдан пример по ней.",
                )
            )
        return GuardrailResult(issues=issues, estimated_minutes=4 + req.task_count * 3.5)

    async def generate(
        self, req: GenerationRequest, progress: ProgressCallback | None = None
    ) -> DiagnosticWork:
        for step in ("Ищу планируемые результаты…", "Составляю задания…", "Проверяю ответы кодом…"):
            if progress:
                await progress(step)
            await asyncio.sleep(self.delay_s / 3)
        work = self._fixture.model_copy(deep=True)
        levels = req.level.levels()
        work.variants = [v for v in work.variants if v.level in levels]
        for v in work.variants:
            v.tasks = v.tasks[: req.task_count]
        work.request = req
        work.work_id = f"fake-{secrets.token_hex(4)}"
        work.created_at = datetime.now(UTC)
        return work

    async def regenerate_task(self, work, level: Level, task_number: int, wish=None, progress=None):
        return work


class FakeErrorAnalyzer:
    def __init__(self) -> None:
        self._fixture = ErrorAnalysisResult.model_validate_json(
            (FIXTURES / "error_analysis_math3.json").read_text(encoding="utf-8")
        )

    async def check(self, req: ErrorAnalysisRequest) -> GuardrailResult:
        issues: list[GuardrailIssue] = []
        return GuardrailResult(issues=issues)

    async def analyze(self, req: ErrorAnalysisRequest, progress=None) -> ErrorAnalysisResult:
        if progress:
            await progress("Анализирую описание ошибки…")
        res = self._fixture.model_copy(deep=True)
        res.request = req
        return res


_EXPLAIN_MARKERS = ("потому", "так как", "значит", "поэтому", "групп", "нужно")
_CHECK_MARKERS = ("провер", "обратн")
_PLAN_MARKERS = ("сначала", "затем", "план", "потом")


class FakeResponseObserver:
    """Простая эвристика. Реальная версия — core.observation."""

    def observe(self, task: Task, response_text: str) -> ResponseObservation:
        text = response_text.strip()
        nums = re.findall(r"-?\d+", text)
        if not text:
            status, basis = SubjectObservation.NO_ANSWER, "Ответ не записан."
        elif nums and nums[-1] == task.expected_answer.strip():
            status, basis = SubjectObservation.CORRECT, f"Итоговое число {nums[-1]} совпадает с ожидаемым."
        else:
            status, basis = SubjectObservation.ERROR, "Итоговое число не совпадает с ожидаемым."
        low = text.lower()
        items = []
        for ind in task.uud_indicators:
            markers = {
                UUDGroup.COMMUNICATIVE: _EXPLAIN_MARKERS,
                UUDGroup.REGULATORY: _CHECK_MARKERS + _PLAN_MARKERS,
                UUDGroup.COGNITIVE: ("×", "*", ":", "групп", "схем"),
            }[ind.group]
            seen = any(m in low for m in markers)
            items.append(
                UUDObservationItem(
                    group=ind.group,
                    outcome_id=ind.outcome_id,
                    status=UUDObservation.OBSERVED if seen else UUDObservation.NOT_SHOWN,
                    basis="В записи есть признак действия."
                    if seen
                    else "В записи только результат; действие не показано.",
                    alternatives=[]
                    if seen
                    else ["Ребёнок мог выполнить действие устно или в уме — по записи это не видно."],
                )
            )
        summary = f"Предметно: {status.value}. " + (
            "УУД: недостаточно наблюдений — записан только ответ."
            if all(i.status != UUDObservation.OBSERVED for i in items)
            else "УУД: см. по пунктам."
        )
        return ResponseObservation(
            task_id=task.task_id,
            response_text=text,
            subject_status=status,
            subject_basis=basis,
            extracted_answer=nums[-1] if nums else None,
            uud=items,
            summary_ru=summary,
        )

    def observe_reflection(self, question: str, answer_text: str) -> ReflectionObservation:
        return ReflectionObservation(
            question=question,
            answer_text=answer_text,
            neutral_note="Ребёнок отметил, что испытал трудность. Это сведения для беседы, а не оценка.",
            suggested_teacher_action="Спросить, на каком шаге было трудно и какая помощь пригодилась бы.",
        )


# ======================================= llm ============================================


class FakeLLMClient:
    """Возвращает заранее заданные ответы по очереди (для юнит-тестов core)."""

    def __init__(self, scripted: list[str | dict] | None = None, model: str = "fake-llm") -> None:
        self.scripted = list(scripted or [])
        self.calls: list[dict] = []
        self.model = model

    async def complete(
        self, system, user, *, json_schema=None, temperature=None, max_tokens=None
    ) -> LLMResponse:
        self.calls.append({"system": system, "user": user, "json_schema": json_schema})
        item = self.scripted.pop(0) if self.scripted else "{}"
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        data = None
        if json_schema is not None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return LLMResponse(
            text=text,
            data=data,
            usage=LLMUsage(model=self.model, tokens_in=len(user) // 4, tokens_out=len(text) // 4),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("FakeLLMClient: embeddings not configured")


# ===================================== storage ==========================================


class InMemoryTeacherRepository:
    def __init__(self) -> None:
        self._rows: dict[int, dict] = {}
        self._next = 1

    async def create(self, nick: str, role: Role) -> NewTeacherCredentials:
        pwd = secrets.token_urlsafe(8)
        acc = TeacherAccount(teacher_id=self._next, nick=nick, role=role)
        self._rows[self._next] = {"acc": acc, "pwd": pwd}
        self._next += 1
        return NewTeacherCredentials(account=acc, plain_password=pwd)

    async def authenticate(self, password: str) -> TeacherAccount | None:
        for r in self._rows.values():
            if r["acc"].active and secrets.compare_digest(r["pwd"], password):
                return r["acc"]
        return None

    async def list(self, include_inactive: bool = False) -> list[TeacherAccount]:
        return [r["acc"] for r in self._rows.values() if include_inactive or r["acc"].active]

    async def get(self, teacher_id: int) -> TeacherAccount | None:
        r = self._rows.get(teacher_id)
        return r["acc"] if r else None

    async def reset_password(self, teacher_id: int) -> NewTeacherCredentials:
        r = self._rows[teacher_id]
        r["pwd"] = secrets.token_urlsafe(8)
        return NewTeacherCredentials(account=r["acc"], plain_password=r["pwd"])

    async def set_active(self, teacher_id: int, active: bool) -> None:
        self._rows[teacher_id]["acc"].active = active

    async def set_role(self, teacher_id: int, role: Role) -> None:
        self._rows[teacher_id]["acc"].role = role


class InMemoryWorkRepository:
    def __init__(self) -> None:
        self._rows: dict[str, DiagnosticWork] = {}

    async def save(self, work: DiagnosticWork, teacher_id: int | None) -> None:
        self._rows[work.work_id] = work

    async def get(self, work_id: str) -> DiagnosticWork | None:
        return self._rows.get(work_id)


class InMemoryFeedbackRepository:
    def __init__(self, works: InMemoryWorkRepository | None = None) -> None:
        self._rows: list[Feedback] = []
        self._works = works

    async def add(self, feedback: Feedback) -> None:
        self._rows.append(feedback)

    async def top_examples(self, grade: int, subject_id: str, k: int = 3) -> list[Task]:
        if not self._works:
            return []
        out: list[Task] = []
        for f in self._rows:
            if f.rating != Rating.UP:
                continue
            w = await self._works.get(f.work_id)
            if w and w.request.grade == grade and w.request.subject_id == subject_id:
                t = w.find_task(f.task_id)
                if t:
                    out.append(t)
        return out[:k]

    async def stats(self) -> dict[Rating, int]:
        c: dict[Rating, int] = defaultdict(int)
        for f in self._rows:
            c[f.rating] += 1
        return dict(c)


class InMemoryUsageRepository:
    def __init__(self) -> None:
        self._rows: list[UsageRecord] = []

    async def add(self, record: UsageRecord) -> None:
        self._rows.append(record)

    async def summary(self, days: int = 30) -> UsageSummary:
        s = UsageSummary(period_days=days)
        for r in self._rows:
            s.requests += 1
            s.tokens_in += r.tokens_in
            s.tokens_out += r.tokens_out
            s.cost_usd += r.cost_usd or 0.0
            s.by_kind[r.kind] = s.by_kind.get(r.kind, 0) + 1
        return s


__all__ = [
    "FakeKnowledgeBase",
    "FakeWorkGenerator",
    "FakeErrorAnalyzer",
    "FakeResponseObserver",
    "FakeLLMClient",
    "InMemoryTeacherRepository",
    "InMemoryWorkRepository",
    "InMemoryFeedbackRepository",
    "InMemoryUsageRepository",
    "ChunkKind",
    "SearchHit",
]
