"""Поисковый модуль нормативной базы: BM25 + плотные эмбеддинги + RRF."""

from __future__ import annotations

import logging

import numpy as np

from socrat.config import Settings
from socrat.contracts import Chunk, ChunkKind, EduLevel, SearchHit, SourceDocument
from socrat.knowledge.index import KnowledgeIndex, tokenize

logger = logging.getLogger(__name__)


class Retriever:
    """Гибридный поиск по фрагментам с фильтрацией и RRF (Reciprocal Rank Fusion)."""

    def __init__(
        self,
        index: KnowledgeIndex,
        sources: dict[str, SourceDocument] | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.index = index
        self.sources: dict[str, SourceDocument] = sources or {}
        self.settings = settings

    def search(
        self,
        query: str,
        grade: int | None = None,
        subject_id: str | None = None,
        kinds: list[ChunkKind] | None = None,
        k: int = 8,
        include_school_materials: bool = True,
    ) -> list[SearchHit]:
        """Гибридный поиск по нормативным фрагментам с фильтрацией."""
        if not self.index.chunks:
            return []

        # 1. Фильтрация кандидатов
        candidates, indices = self._filter_candidates(
            grade=grade,
            subject_id=subject_id,
            kinds=kinds,
            include_school_materials=include_school_materials,
            loosen_grade=False,
        )

        # Если после фильтрации пусто и был задан grade, ослабляем фильтр (±1 класс)
        if not candidates and grade is not None:
            candidates, indices = self._filter_candidates(
                grade=grade,
                subject_id=subject_id,
                kinds=kinds,
                include_school_materials=include_school_materials,
                loosen_grade=True,
            )

        if not candidates:
            return []

        # 2. Ранжирование по BM25
        q_tokens = tokenize(query)
        if not q_tokens:
            # Fallback: простое строковое совпадение, если нет значащих токенов
            q_tokens = [query.lower()]

        bm25_scores = np.zeros(len(candidates), dtype=np.float32)
        q_set = set(q_tokens)
        if self.index.bm25 is not None:
            all_scores = self.index.bm25.get_scores(q_tokens)
            for i, orig_idx in enumerate(indices):
                score_val = all_scores[orig_idx]
                doc_tokens = set(self.index.tokenized_corpus[orig_idx])
                coverage = len(q_set & doc_tokens) / len(q_set) if q_set else 0.0
                if coverage > 0 and score_val <= 0:
                    score_val = coverage * 0.5
                bm25_scores[i] = score_val

        # Сортировка по рангу BM25
        bm25_ranks = np.argsort(-bm25_scores)
        rrf_scores = np.zeros(len(candidates), dtype=np.float32)

        # RRF k=60
        k_rrf = 60.0
        for rank, cand_i in enumerate(bm25_ranks):
            if bm25_scores[cand_i] > 0:
                rrf_scores[cand_i] += 1.0 / (k_rrf + rank + 1)

        # Формируем итоговые SearchHit
        sorted_indices = np.argsort(-rrf_scores)
        hits: list[SearchHit] = []

        for idx in sorted_indices:
            score = float(rrf_scores[idx])
            bm_score = float(bm25_scores[idx])
            if bm_score <= 0 and score <= 0:
                continue

            orig_idx = indices[idx]
            doc_tokens = set(self.index.tokenized_corpus[orig_idx])
            matched = q_set & doc_tokens
            coverage = len(matched) / len(q_set) if q_set else 0.0

            if coverage < 0.6:
                norm_score = coverage * 0.4 * min(1.0, max(0.1, bm_score))
            else:
                norm_score = coverage * (0.5 + 0.5 * min(1.0, max(0.0, bm_score) / 1.5))

            chunk = candidates[idx]
            source_doc = self.sources.get(chunk.source_id)
            title = source_doc.title if source_doc else chunk.source_id

            url = ""
            if source_doc and source_doc.url:
                url = f"{source_doc.url}#page={chunk.page}"

            hits.append(
                SearchHit(
                    chunk=chunk,
                    score=round(norm_score, 4),
                    source_title=title,
                    source_url=url,
                )
            )
            if len(hits) >= k:
                break

        return hits

    def _filter_candidates(
        self,
        grade: int | None,
        subject_id: str | None,
        kinds: list[ChunkKind] | None,
        include_school_materials: bool,
        loosen_grade: bool,
    ) -> tuple[list[Chunk], list[int]]:
        """Фильтрует список фрагментов по критериям."""
        candidates: list[Chunk] = []
        indices: list[int] = []

        for idx, chunk in enumerate(self.index.chunks):
            # Проверка школьных материалов
            if not include_school_materials:
                s_doc = self.sources.get(chunk.source_id)
                if s_doc and not s_doc.is_normative:
                    continue

            # Проверка смыслового типа
            if kinds is not None and chunk.kind not in kinds:
                continue

            # Проверка предмета (None подходит ко всем)
            if subject_id is not None and chunk.subject_id is not None:
                if chunk.subject_id != subject_id:
                    continue

            # Проверка класса
            if grade is not None:
                if chunk.grade is not None:
                    if loosen_grade:
                        if abs(chunk.grade - grade) > 1:
                            continue
                    else:
                        if chunk.grade != grade:
                            continue
                else:
                    s_doc = self.sources.get(chunk.source_id)
                    if s_doc:
                        if s_doc.grades and grade not in s_doc.grades:
                            continue
                        try:
                            lvl = EduLevel.for_grade(grade)
                            if s_doc.edu_levels and lvl not in s_doc.edu_levels:
                                continue
                        except ValueError:
                            pass

            candidates.append(chunk)
            indices.append(idx)

        return candidates, indices
