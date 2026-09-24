"""Индексация нормативных фрагментов: BM25 и эмбеддинги (G6).

ВЛАДЕЛЕЦ: Gemini (knowledge).
"""

from __future__ import annotations

import json
import logging
import pickle
import re
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from socrat.config import Settings
from socrat.contracts import Chunk

logger = logging.getLogger(__name__)

RUSSIAN_STOPWORDS: set[str] = {
    "и",
    "в",
    "во",
    "не",
    "что",
    "он",
    "на",
    "я",
    "с",
    "со",
    "как",
    "а",
    "то",
    "все",
    "она",
    "так",
    "его",
    "но",
    "да",
    "ты",
    "к",
    "у",
    "же",
    "вы",
    "за",
    "бы",
    "по",
    "только",
    "ее",
    "мне",
    "было",
    "вот",
    "от",
    "меня",
    "еще",
    "нет",
    "о",
    "из",
    "ему",
    "теперь",
    "когда",
    "даже",
    "ну",
    "вдруг",
    "ли",
    "если",
    "уже",
    "или",
    "ни",
    "быть",
    "был",
    "него",
    "до",
    "вас",
    "нибудь",
    "опять",
    "уж",
    "вам",
    "ведь",
    "там",
    "потом",
    "себя",
    "ничего",
    "ей",
    "может",
    "они",
    "тут",
    "где",
    "есть",
    "надо",
    "ней",
    "для",
    "мы",
    "тебя",
    "их",
    "чем",
    "была",
    "сам",
    "чтоб",
    "без",
    "будто",
    "чего",
    "раз",
    "тоже",
    "себе",
    "под",
    "будет",
    "ж",
    "тогда",
    "кто",
    "этот",
    "того",
    "потому",
    "этого",
    "какой",
    "совсем",
    "ним",
    "здесь",
    "этом",
    "один",
    "почти",
    "мой",
    "тем",
    "чтобы",
    "нее",
    "сейчас",
    "были",
    "куда",
    "зачем",
    "всех",
    "никогда",
    "можно",
    "при",
    "наконец",
    "два",
    "об",
    "другой",
    "хоть",
    "после",
    "над",
    "больше",
    "тот",
    "через",
    "эти",
    "нас",
    "про",
    "всего",
    "них",
    "какая",
    "много",
    "разве",
    "три",
    "эту",
    "моя",
    "впрочем",
    "хорошо",
    "свою",
    "этой",
    "перед",
    "иногда",
    "лучше",
    "чуть",
    "том",
    "нельзя",
    "такой",
    "им",
    "более",
    "всегда",
    "конечно",
    "всю",
    "между",
}


def tokenize(text: str) -> list[str]:
    """Токенизация текста с удалением стоп-слов и стеммингом основы."""
    tokens = re.findall(r"[а-яёa-z0-9]+", text.lower())
    res: list[str] = []
    for t in tokens:
        if t in RUSSIAN_STOPWORDS or len(t) < 2:
            continue
        # Упрощенный стемминг: основа 5 символов для слов длиннее 5 букв
        stem = t[:5] if len(t) > 5 else t
        res.append(stem)
    return res


class KnowledgeIndex:
    """Индекс фрагментов: BM25Okapi + опциональные плотные эмбеддинги."""

    def __init__(self, chunks: list[Chunk] | None = None) -> None:
        self.chunks: list[Chunk] = chunks or []
        self.chunk_ids: list[str] = [c.chunk_id for c in self.chunks]
        self._chunks_by_id: dict[str, Chunk] = {c.chunk_id: c for c in self.chunks}
        self.tokenized_corpus: list[list[str]] = [tokenize(c.text) for c in self.chunks]
        self.bm25: BM25Okapi | None = BM25Okapi(self.tokenized_corpus) if self.tokenized_corpus else None
        self.embeddings: np.ndarray | None = None

    def add_chunk(self, chunk: Chunk) -> None:
        """Добавляет один фрагмент в индекс (например, при загрузке школьного материала)."""
        if chunk.chunk_id in self._chunks_by_id:
            return
        self.chunks.append(chunk)
        self.chunk_ids.append(chunk.chunk_id)
        self._chunks_by_id[chunk.chunk_id] = chunk
        tokens = tokenize(chunk.text)
        self.tokenized_corpus.append(tokens)
        self.bm25 = BM25Okapi(self.tokenized_corpus)

    def save(self, index_dir: Path) -> None:
        """Сохраняет BM25 индекс и метаданные на диск."""
        index_dir.mkdir(parents=True, exist_ok=True)

        # 1. Сохраняем чанки
        chunks_file = index_dir / "chunks.jsonl"
        with open(chunks_file, "w", encoding="utf-8") as f:
            for ch in self.chunks:
                f.write(ch.model_dump_json() + "\n")

        # 2. Сохраняем BM25 модель
        bm25_file = index_dir / "bm25.pkl"
        with open(bm25_file, "wb") as f:
            pickle.dump(
                {
                    "tokenized_corpus": self.tokenized_corpus,
                    "chunk_ids": self.chunk_ids,
                },
                f,
            )

        # 3. Сохраняем эмбеддинги, если есть
        if self.embeddings is not None:
            np.save(index_dir / "embeddings.npy", self.embeddings)
            ids_file = index_dir / "chunk_ids.json"
            ids_file.write_text(json.dumps(self.chunk_ids, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, index_dir: Path) -> KnowledgeIndex:
        """Загружает индекс с диска."""
        chunks_file = index_dir / "chunks.jsonl"
        if not chunks_file.exists():
            return cls([])

        chunks: list[Chunk] = []
        with open(chunks_file, encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if line_str:
                    chunks.append(Chunk.model_validate_json(line_str))

        instance = cls(chunks)

        # Проверяем кэш BM25
        bm25_file = index_dir / "bm25.pkl"
        if bm25_file.exists():
            try:
                with open(bm25_file, "rb") as f:
                    data = pickle.load(f)
                    instance.tokenized_corpus = data.get("tokenized_corpus", instance.tokenized_corpus)
                    instance.bm25 = BM25Okapi(instance.tokenized_corpus)
            except Exception as e:
                logger.warning("Failed to load cached bm25.pkl, recomputing: %s", e)
                instance.bm25 = BM25Okapi(instance.tokenized_corpus)

        # Проверяем эмбеддинги
        emb_file = index_dir / "embeddings.npy"
        if emb_file.exists():
            try:
                instance.embeddings = np.load(emb_file)
            except Exception as e:
                logger.warning("Failed to load embeddings.npy: %s", e)

        return instance


async def build_embeddings_for_index(index: KnowledgeIndex, settings: Settings, index_dir: Path) -> None:
    """Генерирует эмбеддинги для всех фрагментов индекса, если настроен URL."""
    base_url = settings.embeddings_base_url.strip()
    model = settings.embeddings_model.strip()
    if not base_url or not model:
        return

    try:
        from openai import AsyncOpenAI

        api_key = settings.embeddings_api_key.get_secret_value() or "none"
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)

        texts = [c.text for c in index.chunks]
        if not texts:
            return

        batch_size = 64
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = await client.embeddings.create(model=model, input=batch)
            all_embeddings.extend([item.embedding for item in resp.data])

        index.embeddings = np.array(all_embeddings, dtype=np.float32)
        np.save(index_dir / "embeddings.npy", index.embeddings)
        (index_dir / "chunk_ids.json").write_text(
            json.dumps(index.chunk_ids, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("Successfully built and saved embeddings for %d chunks", len(index.chunks))
    except Exception as e:
        logger.warning("Embeddings generation failed, falling back to BM25 only: %s", e)
