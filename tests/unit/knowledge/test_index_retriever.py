"""Тесты поискового индекса BM25 и Retriever."""

from __future__ import annotations

from pathlib import Path

from socrat.contracts import Chunk, ChunkKind, SourceDocument
from socrat.knowledge.index import KnowledgeIndex, tokenize
from socrat.knowledge.retriever import Retriever


def test_tokenize_russian_and_stemming():
    tokens = tokenize("Умножение и деление чисел в пределах 100")
    # 'и', 'в' — стоп-слова, должны быть удалены
    assert "и" not in tokens
    assert "в" not in tokens
    # Основы длинных слов усечены до 5 символов
    assert "умнож" in tokens
    assert "делен" in tokens
    assert "чисел" in tokens
    assert "преде" in tokens
    assert "100" in tokens


def test_knowledge_index_lifecycle(tmp_path: Path):
    chunks = [
        Chunk(
            chunk_id="SRC-1:10:1",
            source_id="SRC-1",
            page=10,
            section="Содержание обучения > 3 класс",
            text="Табличное и внетабличное умножение и деление чисел в пределах 100.",
            kind=ChunkKind.CONTENT,
            grade=3,
            subject_id="math",
        ),
        Chunk(
            chunk_id="SRC-1:12:1",
            source_id="SRC-1",
            page=12,
            section="Содержание обучения > 3 класс",
            text="Периметр и площадь прямоугольника. Геометрические вычисления.",
            kind=ChunkKind.CONTENT,
            grade=3,
            subject_id="math",
        ),
    ]

    index = KnowledgeIndex(chunks)
    assert len(index.chunks) == 2
    assert index.bm25 is not None

    # Добавление нового чанка
    new_chunk = Chunk(
        chunk_id="SRC-1:14:1",
        source_id="SRC-1",
        page=14,
        section="Содержание обучения > 4 класс",
        text="Деление многозначного числа на двузначное число с остатком.",
        kind=ChunkKind.CONTENT,
        grade=4,
        subject_id="math",
    )
    index.add_chunk(new_chunk)
    assert len(index.chunks) == 3

    # Сохранение и загрузка
    index_dir = tmp_path / "index"
    index.save(index_dir)
    loaded_index = KnowledgeIndex.load(index_dir)

    assert len(loaded_index.chunks) == 3
    assert loaded_index.bm25 is not None


def test_retriever_search_and_filters():
    c1 = Chunk(
        chunk_id="MATH:1:1",
        source_id="FRP-MATH",
        page=1,
        text="Сложение и вычитание чисел в пределах 1000.",
        kind=ChunkKind.CONTENT,
        grade=3,
        subject_id="math",
    )
    c2 = Chunk(
        chunk_id="MATH:2:1",
        source_id="FRP-MATH",
        page=2,
        text="Деление многозначных чисел в 4 классе.",
        kind=ChunkKind.CONTENT,
        grade=4,
        subject_id="math",
    )
    c3 = Chunk(
        chunk_id="RUS:1:1",
        source_id="FRP-RUS",
        page=1,
        text="Спряжение глаголов настоящего времени.",
        kind=ChunkKind.CONTENT,
        grade=4,
        subject_id="russian",
    )

    index = KnowledgeIndex([c1, c2, c3])
    retriever = Retriever(index)

    # 1. Поиск по предмету math и классу 3
    hits = retriever.search("сложение чисел", grade=3, subject_id="math")
    assert len(hits) == 1
    assert hits[0].chunk.chunk_id == "MATH:1:1"
    assert hits[0].score >= 0.5

    # 2. Поиск по другому предмету
    hits_rus = retriever.search("глаголы", subject_id="russian")
    assert len(hits_rus) == 1
    assert hits_rus[0].chunk.chunk_id == "RUS:1:1"

    # 3. Фильтр по классу: если точного совпадения нет, ослабление (±1 класс)
    # Ищем запрос для 5 класса (в индексе 4 класс)
    hits_loosen = retriever.search("деление чисел", grade=5, subject_id="math")
    assert len(hits_loosen) >= 1
    assert hits_loosen[0].chunk.grade == 4


def test_retriever_school_materials_exclusion():
    c_norm = Chunk(
        chunk_id="FRP:1:1",
        source_id="FRP-MATH",
        page=1,
        text="Нормативная программа обучения математике.",
        kind=ChunkKind.CONTENT,
        grade=3,
        subject_id="math",
    )
    c_school = Chunk(
        chunk_id="SCHOOL:1:1",
        source_id="SCHOOL-123",
        page=1,
        text="Школьная программа и дополнительные контрольные работы.",
        kind=ChunkKind.CONTENT,
        grade=3,
        subject_id="math",
    )

    sources = {
        "FRP-MATH": SourceDocument(
            source_id="FRP-MATH",
            title="ФРП",
            url="https://edsoo.ru",
            doc_type="frp",
            is_normative=True,
        ),
        "SCHOOL-123": SourceDocument(
            source_id="SCHOOL-123",
            title="Школа",
            url="local://test",
            doc_type="school_material",
            is_normative=False,
        ),
    }

    index = KnowledgeIndex([c_norm, c_school])
    retriever = Retriever(index, sources=sources)

    # При include_school_materials=False школьный материал не попадает в результат
    hits_norm = retriever.search("программа", include_school_materials=False)
    assert all(h.chunk.source_id != "SCHOOL-123" for h in hits_norm)

    # При include_school_materials=True школьный материал включён
    hits_all = retriever.search("программа", include_school_materials=True)
    assert any(h.chunk.source_id == "SCHOOL-123" for h in hits_all)
