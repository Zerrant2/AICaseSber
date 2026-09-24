"""Интерфейсы между модулями (typing.Protocol). Каждый модуль реализует свой протокол,
а зависит только от протоколов соседей — модули разрабатываются и тестируются независимо,
с фейками из socrat.testing.fakes.

  KnowledgeBase      — реализует knowledge
  LLMClient          — реализует llm
  WorkGenerator      — реализует core
  ErrorAnalyzer      — реализует core
  ResponseObserver   — реализует core
  Exporter           — реализует export
  *Repository        — реализует storage
  Services           — контейнер, собирается в socrat.app
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from .analysis import (
    ErrorAnalysisRequest,
    ErrorAnalysisResult,
    ReflectionObservation,
    ResponseObservation,
)
from .enums import ChunkKind, Level, OutcomeType, Rating, Role
from .normative import (
    KnowledgeStatus,
    Outcome,
    SearchHit,
    SourceDocument,
    Subject,
    TopicCheck,
    UpdateReport,
)
from .request import GenerationRequest, GuardrailResult
from .users import (
    Feedback,
    NewTeacherCredentials,
    TeacherAccount,
    UsageRecord,
    UsageSummary,
)
from .work import DiagnosticWork, Task

# Колбэк прогресса: core/knowledge сообщают этапы ("Ищу результаты в ФРП…"), бот
# показывает их учителю редактированием одного сообщения. Может быть None.
ProgressCallback = Callable[[str], Awaitable[None]]


# ============================== knowledge ======================================


@runtime_checkable
class KnowledgeBase(Protocol):
    """Локальная нормативная база: ФГОС / ФОП / ФРП + материалы школы."""

    def list_subjects(self, grade: int) -> list[Subject]:
        """Предметы для класса (кнопки бота). Порядок — как в учебном плане."""
        ...

    def get_subject(self, subject_id: str) -> Subject | None: ...

    def check_topic(self, grade: int, subject_id: str, topic: str) -> TopicCheck:
        """Есть ли свободная тема учителя в программе этого класса по предмету."""
        ...

    def get_outcomes(
        self,
        grade: int,
        subject_id: str,
        types: list[OutcomeType] | None = None,
        query: str | None = None,
        k: int = 30,
    ) -> list[Outcome]:
        """Планируемые результаты для класса/предмета. Если query задан — ранжировать по нему.
        Метапредметные и личностные результаты уровня образования включаются всегда
        (для них subject_id может быть None). Для math/3 ОБЯЗАТЕЛЬНО включить P01..L01 из кейса."""
        ...

    def get_outcome(self, outcome_id: str) -> Outcome | None: ...

    def search(
        self,
        query: str,
        grade: int | None = None,
        subject_id: str | None = None,
        kinds: list[ChunkKind] | None = None,
        k: int = 8,
        include_school_materials: bool = True,
    ) -> list[SearchHit]:
        """Гибридный поиск (BM25 + эмбеддинги, если настроены)."""
        ...

    def get_page_text(self, source_id: str, page: int) -> str | None:
        """Полный текст страницы PDF — для проверки цитат (анти-галлюцинации)."""
        ...

    def get_source(self, source_id: str) -> SourceDocument | None: ...

    def status(self) -> KnowledgeStatus: ...

    async def update(self, progress: ProgressCallback | None = None) -> UpdateReport:
        """Скачать/обновить документы по sources.yaml, переиндексировать (команда админа)."""
        ...

    async def ingest_school_material(
        self, filename: str, content: bytes, progress: ProgressCallback | None = None
    ) -> SourceDocument:
        """Загрузка PDF/DOCX школы. doc_type=SCHOOL_MATERIAL, is_normative=False."""
        ...


# ================================ llm ==========================================


class LLMUsage(BaseModel):
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None


class LLMResponse(BaseModel):
    text: str
    data: Any | None = None  # распарсенный JSON, если запрашивался
    usage: LLMUsage


@runtime_checkable
class LLMClient(Protocol):
    """OpenAI-совместимый клиент (OpenRouter / Ollama / vLLM). Настройки из .env."""

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Эмбеддинги через EMBEDDINGS_* из .env. Бросает NotImplementedError, если не настроено."""
        ...


# ================================ core =========================================


@runtime_checkable
class WorkGenerator(Protocol):
    async def check_request(self, req: GenerationRequest) -> GuardrailResult:
        """Быстрая проверка ДО генерации: класс, предмет, тема в программе, время, ПДн,
        просьбы о несуществующих пунктах / гарантии полного ФГОС. Без тяжёлых LLM-вызовов."""
        ...

    async def generate(
        self, req: GenerationRequest, progress: ProgressCallback | None = None
    ) -> DiagnosticWork:
        """Полная генерация + валидация кодом. Бросает GenerationError при неустранимой ошибке."""
        ...

    async def regenerate_task(
        self,
        work: DiagnosticWork,
        level: Level,
        task_number: int,
        wish: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> DiagnosticWork:
        """Перегенерировать одно задание (по 👎 или просьбе учителя), сохранив остальное."""
        ...


@runtime_checkable
class ErrorAnalyzer(Protocol):
    async def check(self, req: ErrorAnalysisRequest) -> GuardrailResult:
        """ПДн в описании (ФИО детей), просьбы о диагнозе и т.п."""
        ...

    async def analyze(
        self, req: ErrorAnalysisRequest, progress: ProgressCallback | None = None
    ) -> ErrorAnalysisResult: ...


@runtime_checkable
class ResponseObserver(Protocol):
    def observe(self, task: Task, response_text: str) -> ResponseObservation:
        """Детерминированный разбор обезличенного ответа (T06)."""
        ...

    def observe_reflection(self, question: str, answer_text: str) -> ReflectionObservation:
        """Нейтральная фиксация рефлексии (T07)."""
        ...


class GenerationError(Exception):
    """Генерация не удалась после всех попыток починки. message — для учителя."""

    def __init__(self, message_ru: str, details: str | None = None):
        super().__init__(message_ru)
        self.message_ru = message_ru
        self.details = details


# =============================== export =========================================


@runtime_checkable
class Exporter(Protocol):
    def student_docx(self, work: DiagnosticWork) -> bytes:
        """Лист ученика: задания + рефлексия. БЕЗ ответов/решений/карты. Все варианты работы,
        каждый с новой страницы."""
        ...

    def teacher_docx(self, work: DiagnosticWork) -> bytes:
        """Методичка учителя: параметры, план времени, карта результатов, решения, признаки УУД,
        типичные ошибки, приёмы, комментарий по проведению, ограничения, источники."""
        ...

    def analysis_docx(self, result: ErrorAnalysisResult) -> bytes: ...

    def filenames(self, work: DiagnosticWork) -> tuple[str, str]:
        """('Задания_3кл_Математика_<тема>.docx', 'Методичка_3кл_Математика_<тема>.docx')"""
        ...


# =============================== storage ========================================


@runtime_checkable
class TeacherRepository(Protocol):
    async def create(self, nick: str, role: Role) -> NewTeacherCredentials: ...
    async def authenticate(self, password: str) -> TeacherAccount | None: ...
    async def list(self, include_inactive: bool = False) -> list[TeacherAccount]: ...
    async def get(self, teacher_id: int) -> TeacherAccount | None: ...
    async def reset_password(self, teacher_id: int) -> NewTeacherCredentials: ...
    async def set_active(self, teacher_id: int, active: bool) -> None: ...
    async def set_role(self, teacher_id: int, role: Role) -> None: ...


@runtime_checkable
class WorkRepository(Protocol):
    async def save(self, work: DiagnosticWork, teacher_id: int | None) -> None: ...
    async def get(self, work_id: str) -> DiagnosticWork | None: ...


@runtime_checkable
class FeedbackRepository(Protocol):
    async def add(self, feedback: Feedback) -> None: ...
    async def top_examples(self, grade: int, subject_id: str, k: int = 3) -> list[Task]:
        """Задания с 👍 для few-shot (калибровка под школу). Без 👎."""
        ...

    async def stats(self) -> dict[Rating, int]: ...


@runtime_checkable
class UsageRepository(Protocol):
    async def add(self, record: UsageRecord) -> None: ...
    async def summary(self, days: int = 30) -> UsageSummary: ...


# ============================== composition root ========================================


@dataclass
class Services:
    """Собирается один раз в socrat.app.build_services(settings). Передаётся в хендлеры бота
    через middleware / aiogram dependency injection."""

    knowledge: KnowledgeBase
    generator: WorkGenerator
    analyzer: ErrorAnalyzer
    observer: ResponseObserver
    exporter: Exporter
    teachers: TeacherRepository
    works: WorkRepository
    feedback: FeedbackRepository
    usage: UsageRepository
