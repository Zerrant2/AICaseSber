"""Тесты разметки разделов и нарезки на фрагменты."""

from __future__ import annotations

from pathlib import Path

from socrat.contracts import ChunkKind
from socrat.knowledge.chunker import (
    SectionState,
    chunk_document,
    load_chunks,
    save_chunks,
)


def test_section_state_transitions():
    state = SectionState()
    assert state.kind == ChunkKind.GENERAL

    # Пояснительная записка
    assert state.update("ПОЯСНИТЕЛЬНАЯ ЗАПИСКА")
    assert state.section_path == "Пояснительная записка"
    assert state.kind == ChunkKind.GENERAL

    # Содержание обучения
    assert state.update("СОДЕРЖАНИЕ ОБУЧЕНИЯ")
    assert state.section_path == "Содержание обучения"
    assert state.kind == ChunkKind.CONTENT

    # 3 класс в содержании
    assert state.update("3 КЛАСС")
    assert state.section_path == "Содержание обучения > 3 класс"
    assert state.kind == ChunkKind.CONTENT
    assert state.grade == 3

    # Личностные результаты
    assert state.update("ЛИЧНОСТНЫЕ РЕЗУЛЬТАТЫ")
    assert state.section_path == "Планируемые результаты > Личностные результаты"
    assert state.kind == ChunkKind.PERSONAL_RESULT
    assert state.grade is None

    # Познавательные УУД
    assert state.update("Познавательные универсальные учебные действия")
    assert state.section_path == "Планируемые результаты > Метапредметные результаты > Познавательные УУД"
    assert state.kind == ChunkKind.META_RESULT

    # Предметные результаты 3 класс
    assert state.update("ПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ")
    assert state.kind == ChunkKind.SUBJECT_RESULT

    assert state.update("К концу обучения в 3 классе обучающийся научится:")
    assert state.section_path == "Планируемые результаты > Предметные результаты > 3 класс"
    assert state.kind == ChunkKind.SUBJECT_RESULT
    assert state.grade == 3

    # Тематическое планирование
    assert state.update("ТЕМАТИЧЕСКОЕ ПЛАНИРОВАНИЕ")
    assert state.section_path == "Тематическое планирование"
    assert state.kind == ChunkKind.GENERAL


def test_chunking_preserves_pages_and_sections():
    pages = [
        {
            "page": 1,
            "text": "ПОЯСНИТЕЛЬНАЯ ЗАПИСКА\nПервый абзац документа общих положений.\nВторой абзац документа.",
        },
        {
            "page": 2,
            "text": "СОДЕРЖАНИЕ ОБУЧЕНИЯ\n3 КЛАСС\n• Первая тема: Числа в пределах 1000 и арифметические операции.\n• Вторая тема: Табличное и внетабличное умножение и деление чисел.",
        },
        {
            "page": 3,
            "text": "• Третья тема: Периметр и площадь прямоугольника на плоскости.\n• Четвертая тема: Решение текстовых задач в одно-два действия.",
        },
    ]

    chunks = chunk_document(
        source_id="TEST-FRP",
        pages=pages,
        subject_id="math",
        min_chars=50,
        max_chars=200,
    )

    assert len(chunks) >= 2

    # Проверка первого чанка
    c1 = chunks[0]
    assert c1.source_id == "TEST-FRP"
    assert c1.page == 1
    assert c1.section == "Пояснительная записка"
    assert c1.kind == ChunkKind.GENERAL
    assert "Первый абзац" in c1.text
    assert c1.chunk_id == "TEST-FRP:1:1"

    # Проверка чанков содержания обучения 3 класса
    c_content = [c for c in chunks if c.kind == ChunkKind.CONTENT]
    assert len(c_content) >= 1
    for c in c_content:
        assert c.section == "Содержание обучения > 3 класс"
        assert c.grade == 3
        assert c.subject_id == "math"
        # Чанк не должен начинаться с '3 КЛАСС' (заголовок ушёл в метаданные)
        assert c.page >= 2


def test_save_and_load_chunks(tmp_path: Path):
    pages = [
        {
            "page": 10,
            "text": "СОДЕРЖАНИЕ ОБУЧЕНИЯ\n2 КЛАСС\nТестовый текст описания курса математики во 2 классе.",
        }
    ]
    chunks = chunk_document(
        source_id="FRP-TEST",
        pages=pages,
        subject_id="math",
        min_chars=10,
        max_chars=500,
    )
    assert len(chunks) == 1

    chunks_file = tmp_path / "chunks.jsonl"
    save_chunks(chunks, chunks_file)
    assert chunks_file.exists()

    loaded = load_chunks(chunks_file)
    assert len(loaded) == 1
    assert loaded[0].chunk_id == chunks[0].chunk_id
    assert loaded[0].text == chunks[0].text
    assert loaded[0].grade == 2
    assert loaded[0].kind == ChunkKind.CONTENT
