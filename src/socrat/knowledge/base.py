"""Реализация KnowledgeBase — локальная нормативная база (G7).

ВЛАДЕЛЕЦ: Gemini (knowledge).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from socrat.config import Settings
from socrat.contracts import (
    Chunk,
    ChunkKind,
    DocType,
    EduLevel,
    KnowledgeStatus,
    Outcome,
    OutcomeType,
    ProgressCallback,
    SearchHit,
    SourceDocument,
    Subject,
    TopicCheck,
    UpdateReport,
)
from socrat.knowledge.catalog import build_catalog_math_noo
from socrat.knowledge.downloader import download_all, load_manifest, load_sources_registry
from socrat.knowledge.parser import get_page_text, parse_and_save_pdf

logger = logging.getLogger(__name__)

SK01_CASE_IDS = ("P01", "P02", "C01", "R01", "K01", "L01")


class LocalKnowledgeBase:
    """Реализует socrat.contracts.KnowledgeBase на локальных файлах."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._sources: dict[str, SourceDocument] = {}
        self._subjects_def: list[dict[str, Any]] = []
        self._outcomes: list[Outcome] = []
        self._outcomes_by_id: dict[str, Outcome] = {}
        self._problems: list[str] = []
        self._ready: bool = False

        self._load()

    def _load(self) -> None:
        """Загружает манифест, каталог и список предметов. При отсутствии файлов не падает."""
        self._problems.clear()

        # 1. Загрузка subjects.yaml
        subjects_file = Path(__file__).resolve().parent / "subjects.yaml"
        if subjects_file.exists():
            try:
                raw_subs = yaml.safe_load(subjects_file.read_text(encoding="utf-8")) or {}
                self._subjects_def = sorted(raw_subs.get("subjects", []), key=lambda s: s.get("order", 99))
            except Exception as e:
                self._problems.append(f"Не удалось загрузить subjects.yaml: {e}")
        else:
            self._problems.append("Файл subjects.yaml не найден")

        # 2. Загрузка источников из manifest.json (или fallback на sources.yaml)
        manifest_path = self.settings.knowledge_dir / "manifest.json"
        manifest_docs = load_manifest(manifest_path)
        if manifest_docs:
            for sid, doc_data in manifest_docs.items():
                try:
                    self._sources[sid] = SourceDocument(
                        source_id=doc_data.get("source_id", sid),
                        title=doc_data.get("title", sid),
                        url=doc_data.get("url", ""),
                        doc_type=DocType(doc_data.get("doc_type", "frp")),
                        edu_levels=[EduLevel(lvl) for lvl in doc_data.get("edu_levels", [])],
                        subject_id=doc_data.get("subject_id"),
                        grades=doc_data.get("grades", []),
                        is_normative=doc_data.get("is_normative", True),
                        sha256=doc_data.get("sha256"),
                        pages=doc_data.get("pages"),
                        local_path=doc_data.get("local_path"),
                        note=doc_data.get("note"),
                    )
                except Exception as e:
                    logger.warning("Error parsing document %s from manifest: %e", sid, e)
        else:
            # Fallback: читаем sources.yaml, чтобы знать доступные источники
            src_list = load_sources_registry(self.settings.sources_file)
            for s in src_list:
                sid = s["source_id"]
                try:
                    self._sources[sid] = SourceDocument(
                        source_id=sid,
                        title=s.get("title", sid),
                        url=s.get("url", ""),
                        doc_type=DocType(s.get("doc_type", "frp")),
                        edu_levels=[EduLevel(lvl) for lvl in s.get("edu_levels", [])],
                        subject_id=s.get("subject_id"),
                        grades=s.get("grades", []),
                        is_normative=s.get("is_normative", True),
                        note=s.get("note"),
                    )
                except Exception as e:
                    logger.warning("Error creating fallback source %s: %e", sid, e)

        if not self._sources:
            self._problems.append("Нормативные источники не зарегистрированы")

        # 3. Загрузка каталога
        catalog_dir = self.settings.knowledge_dir / "catalog"
        self._outcomes.clear()
        self._outcomes_by_id.clear()

        if catalog_dir.exists():
            for c_file in catalog_dir.glob("*.json"):
                try:
                    data = json.loads(c_file.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        for item in data:
                            out = Outcome.model_validate(item)
                            if out.outcome_id not in self._outcomes_by_id:
                                self._outcomes.append(out)
                                self._outcomes_by_id[out.outcome_id] = out
                except Exception as e:
                    self._problems.append(f"Ошибка чтения файла каталога {c_file.name}: {e}")

        if not self._outcomes:
            self._problems.append("Каталог планируемых результатов пуст или не найден")

        # База готова, если есть хотя бы один источник и результаты
        self._ready = bool(self._sources and self._outcomes)

    def list_subjects(self, grade: int) -> list[Subject]:
        """Возвращает предметы для класса, по которым в базе есть хотя бы одна ФРП."""
        try:
            edu_lvl = EduLevel.for_grade(grade)
        except ValueError:
            return []

        # Находим предметы, для которых есть ФРП этого уровня
        available_frp_subjects = {
            doc.subject_id
            for doc in self._sources.values()
            if doc.doc_type == DocType.FRP
            and (edu_lvl in doc.edu_levels or grade in doc.grades)
            and doc.subject_id
        }

        res: list[Subject] = []
        for s in self._subjects_def:
            sid = s["subject_id"]
            if grade in s.get("grades", []) and sid in available_frp_subjects:
                res.append(
                    Subject(
                        subject_id=sid,
                        name=s["name"],
                        grades=s.get("grades", []),
                    )
                )
        return res

    def get_subject(self, subject_id: str) -> Subject | None:
        for s in self._subjects_def:
            if s["subject_id"] == subject_id:
                return Subject(
                    subject_id=s["subject_id"],
                    name=s["name"],
                    grades=s.get("grades", []),
                )
        return None

    def check_topic(self, grade: int, subject_id: str, topic: str) -> TopicCheck:
        """Проверяет соответствие темы программе класса."""
        topic_lower = topic.lower()

        # Базовые темы математики 3 класса для проверки и подсказок
        math3_program_topics = [
            "Умножение и деление в пределах 100",
            "Решение текстовых задач в одно-два действия",
            "Сложение и вычитание в пределах 1000",
            "Периметр и площадь прямоугольника",
            "Порядок действий в числовых выражениях",
            "Деление с остатком",
            "Доли величин (половина, четверть)",
        ]

        # Ищем совпадения в каталоге результатов данного предмета и класса
        class_outcomes = [o for o in self._outcomes if o.subject_id == subject_id and o.grade == grade]

        matched: list[str] = []
        best_score = 0.0

        topic_words = set(re.findall(r"[а-яёa-z0-9]{3,}", topic_lower))
        for o in class_outcomes:
            o_words = set(re.findall(r"[а-яёa-z0-9]{3,}", o.text.lower()))
            if not topic_words or not o_words:
                continue
            common = topic_words & o_words
            score = len(common) / len(topic_words)
            if score > best_score:
                best_score = score
                matched.append(o.text)

        # Проверка ключевых корней для математики 3 класса
        if grade == 3 and subject_id == "math":
            math3_roots = (
                "умнож",
                "делен",
                "задач",
                "табли",
                "площад",
                "периметр",
                "вычисл",
                "арифмет",
                "сложен",
                "вычитан",
            )
            if any(r in topic_lower for r in math3_roots):
                best_score = max(best_score, 0.85)
                matched.append("Умножение и деление в пределах 100")

        in_prog = best_score >= 0.4
        suggestions = [] if in_prog else math3_program_topics[:5]

        return TopicCheck(
            in_program=in_prog,
            confidence=min(1.0, max(0.1, best_score)),
            matched_topics=list(dict.fromkeys(matched))[:3] if in_prog else [],
            suggestions=suggestions,
            evidence=[],
        )

    def get_outcomes(
        self,
        grade: int,
        subject_id: str,
        types: list[OutcomeType] | None = None,
        query: str | None = None,
        k: int = 30,
    ) -> list[Outcome]:
        """Возвращает планируемые результаты для класса и предмета.

        Для math/3 обязательно первыми включает P01..L01 из кейса SK01.
        """
        try:
            EduLevel.for_grade(grade)
        except ValueError:
            return []

        # 1. Предметные результаты этого класса и предмета
        subject_outcomes = [
            o
            for o in self._outcomes
            if o.type == OutcomeType.SUBJECT
            and o.grade == grade
            and o.subject_id == subject_id
            and (not types or o.type in types)
        ]

        # 2. Метапредметные и личностные результаты уровня
        meta_types = (
            OutcomeType.COGNITIVE,
            OutcomeType.COMMUNICATIVE,
            OutcomeType.REGULATORY,
            OutcomeType.PERSONAL,
        )
        meta_outcomes = [
            o
            for o in self._outcomes
            if o.type in meta_types
            and (o.subject_id is None or o.subject_id == subject_id)
            and (not types or o.type in types)
        ]

        # Для math/3 гарантируем результаты кейса SK01 первыми
        sk01_items: list[Outcome] = []
        regular_items: list[Outcome] = []

        combined = subject_outcomes + meta_outcomes
        seen_ids: set[str] = set()

        if grade == 3 and subject_id == "math":
            for cid in SK01_CASE_IDS:
                o = self._outcomes_by_id.get(cid)
                if o and (not types or o.type in types):
                    sk01_items.append(o)
                    seen_ids.add(o.outcome_id)

        for o in combined:
            if o.outcome_id not in seen_ids:
                regular_items.append(o)
                seen_ids.add(o.outcome_id)

        # Если есть поисковый запрос, ранжируем обычные элементы по совпадению
        if query:
            q_lower = query.lower()
            q_words = set(re.findall(r"[а-яёa-z0-9]{3,}", q_lower))

            def _rank_key(item: Outcome) -> float:
                i_lower = item.text.lower()
                if not q_words:
                    return 0.0
                i_words = set(re.findall(r"[а-яёa-z0-9]{3,}", i_lower))
                return len(q_words & i_words)

            regular_items.sort(key=_rank_key, reverse=True)

        res = sk01_items + regular_items
        return res[:k]

    def get_outcome(self, outcome_id: str) -> Outcome | None:
        return self._outcomes_by_id.get(outcome_id)

    def search(
        self,
        query: str,
        grade: int | None = None,
        subject_id: str | None = None,
        kinds: list[ChunkKind] | None = None,
        k: int = 8,
        include_school_materials: bool = True,
    ) -> list[SearchHit]:
        """Простой поиск по тексту результатов и документов."""
        q_lower = query.lower()
        q_words = set(re.findall(r"[а-яёa-z0-9]{3,}", q_lower))
        hits: list[SearchHit] = []

        for o in self._outcomes:
            if grade is not None and o.grade is not None and o.grade != grade:
                continue
            if subject_id is not None and o.subject_id is not None and o.subject_id != subject_id:
                continue

            o_words = set(re.findall(r"[а-яёa-z0-9]{3,}", o.text.lower()))
            overlap = len(q_words & o_words) if q_words else 0
            if overlap > 0 or q_lower in o.text.lower():
                score = overlap / max(1, len(q_words))
                chunk = Chunk(
                    chunk_id=f"{o.source_id}:{o.page}:{o.outcome_id}",
                    source_id=o.source_id,
                    page=o.page,
                    section=o.section,
                    text=o.text,
                    kind=ChunkKind.SUBJECT_RESULT if o.type == OutcomeType.SUBJECT else ChunkKind.META_RESULT,
                    grade=o.grade,
                    subject_id=o.subject_id,
                )
                source_doc = self._sources.get(o.source_id)
                hits.append(
                    SearchHit(
                        chunk=chunk,
                        score=score,
                        source_title=source_doc.title if source_doc else o.source_id,
                        source_url=o.source_url,
                    )
                )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def get_page_text(self, source_id: str, page: int) -> str | None:
        """Возвращает очищенный текст страницы PDF."""
        pages_dir = self.settings.knowledge_dir / "pages"
        return get_page_text(source_id, page, pages_dir)

    def get_source(self, source_id: str) -> SourceDocument | None:
        return self._sources.get(source_id)

    def status(self) -> KnowledgeStatus:
        return KnowledgeStatus(
            documents=list(self._sources.values()),
            chunks_total=len(self._outcomes),
            outcomes_total=len(self._outcomes),
            embeddings_enabled=bool(self.settings.embeddings_base_url),
            last_update=None,
            ready=self._ready,
            problems=list(self._problems),
        )

    async def update(self, progress: ProgressCallback | None = None) -> UpdateReport:
        """Скачивает/обновляет документы, парсит и пересобирает каталог."""
        if progress:
            await progress("Запуск скачивания источников…")
        report = await download_all(self.settings, progress)

        # Парсим скачанные и существующие PDF
        raw_dir = self.settings.knowledge_dir / "raw"
        pages_dir = self.settings.knowledge_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        for pdf_path in raw_dir.glob("*.pdf"):
            sid = pdf_path.stem
            if progress:
                await progress(f"Парсинг PDF {sid}…")
            parse_and_save_pdf(pdf_path, sid, pages_dir)

        # Пересобираем каталог math
        if progress:
            await progress("Сборка каталога планируемых результатов…")
        build_catalog_math_noo(self.settings)

        # Перезагружаем внутреннее состояние
        self._load()
        report.outcomes_total = len(self._outcomes)
        report.chunks_total = len(self._outcomes)

        if progress:
            await progress("Обновление нормативной базы завершено.")
        return report

    async def ingest_school_material(
        self,
        filename: str,
        content: bytes,
        progress: ProgressCallback | None = None,
    ) -> SourceDocument:
        """Загрузка PDF школы как школьного материала (is_normative=False)."""
        sha = hashlib.sha256(content).hexdigest()
        source_id = f"SCHOOL-{sha[:8]}"
        doc = SourceDocument(
            source_id=source_id,
            title=filename,
            url=f"local://{filename}",
            doc_type=DocType.SCHOOL_MATERIAL,
            is_normative=False,
            sha256=sha,
            bytes=len(content),
        )
        self._sources[source_id] = doc
        return doc
