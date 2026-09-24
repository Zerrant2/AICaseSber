"""Модели нормативной базы (ФГОС / ФОП / ФРП) и каталога планируемых результатов.

Производитель: модуль knowledge. Потребитель: core, bot.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from .enums import ChunkKind, DocType, EduLevel, OutcomeType


class SourceDocument(BaseModel):
    """Документ в локальном хранилище нормативной базы."""

    source_id: str = Field(..., description="Стабильный ID, напр. 'FGOS-NOO-286', 'FRP-MATH-2025'")
    title: str
    url: str = Field(..., description="Официальный URL, откуда скачан документ")
    doc_type: DocType
    edu_levels: list[EduLevel] = Field(default_factory=list)
    subject_id: str | None = Field(None, description="Для ФРП — предмет; для ФГОС/ФОП — None")
    grades: list[int] = Field(default_factory=list, description="Какие классы покрывает")
    is_normative: bool = Field(True, description="False для SCHOOL_MATERIAL: нельзя ссылаться как на норму")
    checked_on: date | None = Field(None, description="Дата скачивания/проверки")
    sha256: str | None = None
    pages: int | None = None
    local_path: str | None = Field(None, description="Путь к PDF относительно KNOWLEDGE_DIR")
    note: str | None = Field(None, description="Оговорки, напр. 'первоначальная редакция'")


class Subject(BaseModel):
    """Учебный предмет для кнопок бота."""

    subject_id: str = Field(..., description="Латиница, напр. 'math', 'russian', 'algebra'")
    name: str = Field(..., description="Как показывать учителю: 'Математика'")
    grades: list[int] = Field(default_factory=list)


class Chunk(BaseModel):
    """Фрагмент документа для поиска. Страница — по нумерации PDF (1-based)."""

    chunk_id: str
    source_id: str
    page: int = Field(..., ge=1)
    page_end: int | None = Field(None, description="Если фрагмент переходит на след. страницу")
    section: str = Field("", description="Путь раздела: 'Планируемые результаты > 3 класс'")
    text: str
    kind: ChunkKind = ChunkKind.GENERAL
    grade: int | None = None
    subject_id: str | None = None


class SearchHit(BaseModel):
    chunk: Chunk
    score: float
    source_title: str = ""
    source_url: str = ""


class Outcome(BaseModel):
    """Планируемый результат из каталога.

    ВАЖНО: outcome_id — ВНУТРЕННИЙ идентификатор, НЕ номер пункта ФГОС.
    Для кейса SK01 используются ID из outcomes_reference.json (P01, P02, C01, R01, K01, L01).
    Для остальных: '<subject_id>-<grade>-<P|C|R|K|L>-<NN>', напр. 'math-3-P-07'.
    Для метапредметных/личностных (не зависят от предмета): '<level>-<C|R|K|L>-<NN>', напр. 'noo-R-03'.
    """

    outcome_id: str
    type: OutcomeType
    text: str = Field(..., description="Формулировка (дословно из документа, если quote_is_verbatim)")
    quote_is_verbatim: bool = Field(
        True, description="True — text дословно есть на странице; False — методический пересказ"
    )
    grade: int | None = None
    subject_id: str | None = None
    source_id: str
    section: str
    page: int = Field(..., ge=1)
    source_url: str = Field(..., description="URL с якорем страницы, напр. ...pdf#page=23")


class TopicCheck(BaseModel):
    """Результат проверки свободной темы учителя на соответствие программе класса."""

    in_program: bool
    confidence: float = Field(..., ge=0, le=1)
    matched_topics: list[str] = Field(default_factory=list, description="Найденные темы программы")
    suggestions: list[str] = Field(default_factory=list, description="Близкие темы для кнопок бота (≤5)")
    evidence: list[SearchHit] = Field(default_factory=list)


class KnowledgeStatus(BaseModel):
    documents: list[SourceDocument] = Field(default_factory=list)
    chunks_total: int = 0
    outcomes_total: int = 0
    embeddings_enabled: bool = False
    last_update: datetime | None = None
    ready: bool = False
    problems: list[str] = Field(default_factory=list)


class UpdateReport(BaseModel):
    downloaded: list[str] = Field(default_factory=list, description="source_id скачанных/обновлённых")
    unchanged: list[str] = Field(default_factory=list)
    failed: dict[str, str] = Field(default_factory=dict, description="source_id -> причина")
    chunks_total: int = 0
    outcomes_total: int = 0
    duration_s: float = 0.0
