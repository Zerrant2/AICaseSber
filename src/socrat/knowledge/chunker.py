"""Разметка разделов и нарезка нормативных документов на фрагменты (G4).

ВЛАДЕЛЕЦ: Gemini (knowledge).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from socrat.contracts import Chunk, ChunkKind

logger = logging.getLogger(__name__)

GRADE_WORDS: dict[str, int] = {
    "1": 1,
    "первый": 1,
    "первом": 1,
    "2": 2,
    "второй": 2,
    "втором": 2,
    "3": 3,
    "третий": 3,
    "третьем": 3,
    "4": 4,
    "четвертый": 4,
    "четвёртый": 4,
    "четвертом": 4,
    "четвёртом": 4,
    "5": 5,
    "пятый": 5,
    "пятом": 5,
    "6": 6,
    "шестой": 6,
    "шестом": 6,
    "7": 7,
    "седьмой": 7,
    "седьмом": 7,
    "8": 8,
    "восьмой": 8,
    "восьмом": 8,
    "9": 9,
    "девятый": 9,
    "девятом": 9,
    "10": 10,
    "десятый": 10,
    "десятом": 10,
    "11": 11,
    "одиннадцатый": 11,
    "одиннадцатом": 11,
}

# Регулярные выражения для определения заголовков
RE_EXPLANATORY = re.compile(r"^\s*ПОЯСНИТЕЛЬНАЯ\s+ЗАПИСКА\s*$", re.IGNORECASE)
RE_CONTENT_MAIN = re.compile(r"^\s*СОДЕРЖАНИЕ(?:\s+УЧЕБНОГО\s+ПРЕДМЕТА|\s+ОБУЧЕНИЯ)?\s*$", re.IGNORECASE)
RE_PLANNED_RESULTS = re.compile(
    r"^\s*ПЛАНИРУЕМЫЕ(?:\s+ОБРАЗОВАТЕЛЬНЫЕ)?\s+РЕЗУЛЬТАТЫ(?:\s+ОСВОЕНИЯ\s+ПРОГРАММЫ)?.*$",
    re.IGNORECASE,
)
RE_PERSONAL_RESULTS = re.compile(r"^\s*ЛИЧНОСТНЫЕ\s+РЕЗУЛЬТАТЫ\s*$", re.IGNORECASE)
RE_META_RESULTS = re.compile(r"^\s*МЕТАПРЕДМЕТНЫЕ\s+РЕЗУЛЬТАТЫ\s*$", re.IGNORECASE)
RE_COGNITIVE = re.compile(r"^\s*Познавательные\s+универсальные\s+учебные\s+действия.*$", re.IGNORECASE)
RE_COMMUNICATIVE = re.compile(r"^\s*Коммуникативные\s+универсальные\s+учебные\s+действия.*$", re.IGNORECASE)
RE_REGULATORY = re.compile(r"^\s*Регулятивные\s+универсальные\s+учебные\s+действия.*$", re.IGNORECASE)
RE_SUBJECT_RESULTS = re.compile(r"^\s*ПРЕДМЕТНЫЕ\s+РЕЗУЛЬТАТЫ\s*$", re.IGNORECASE)
RE_GRADE_CONTENT = re.compile(
    r"^\s*(\d+|ПЕРВЫЙ|ВТОРОЙ|ТРЕТИЙ|ЧЕТВ[ЕЁ]РТЫЙ|ПЯТЫЙ|ШЕСТОЙ|СЕДЬМОЙ|ВОСЬМОЙ|ДЕВЯТЫЙ|ДЕСЯТЫЙ|ОДИННАДЦАТЫЙ)\s+КЛАСС\s*$",
    re.IGNORECASE,
)
RE_GRADE_RESULT = re.compile(
    r"^\s*К\s+концу\s+обучения\s+в[о]?\s+(\d+|первом|втором|третьем|четв[её]ртом|пятом|шестом|седьмом|восьмом|девятом|десятом|одиннадцатом)\s+классе.*$",
    re.IGNORECASE,
)
RE_THEMATIC = re.compile(r"^\s*ТЕМАТИЧЕСКОЕ\s+ПЛАНИРОВАНИЕ\s*$", re.IGNORECASE)

# Маркер начала пункта списка
RE_BULLET_START = re.compile(r"^(?:[•–—\-\*]|\d+[\.\)]|[а-яё]\))\s+")


class SectionState:
    """Состояние текущего раздела документа."""

    def __init__(self) -> None:
        self.top_section: str = "Общие положения"
        self.section_path: str = "Общие положения"
        self.kind: ChunkKind = ChunkKind.GENERAL
        self.grade: int | None = None
        self.in_thematic: bool = False

    def update(self, line: str) -> bool:
        """Обновляет состояние раздела, если строка является заголовком.

        Возвращает True, если раздел изменился.
        """
        stripped = line.strip()
        if not stripped:
            return False

        if RE_THEMATIC.match(stripped):
            self.in_thematic = True
            self.top_section = "Тематическое планирование"
            self.section_path = "Тематическое планирование"
            self.kind = ChunkKind.GENERAL
            self.grade = None
            return True

        if self.in_thematic:
            # Внутри тематического планирования заголовки классов не меняют смысловой тип на CONTENT
            m = RE_GRADE_CONTENT.match(stripped)
            if m:
                g = GRADE_WORDS.get(m.group(1).lower())
                self.grade = g
                self.section_path = (
                    f"Тематическое планирование > {g} класс" if g else "Тематическое планирование"
                )
                return True
            return False

        if RE_EXPLANATORY.match(stripped):
            self.top_section = "Пояснительная записка"
            self.section_path = "Пояснительная записка"
            self.kind = ChunkKind.GENERAL
            self.grade = None
            return True

        if RE_CONTENT_MAIN.match(stripped):
            self.top_section = "Содержание обучения"
            self.section_path = "Содержание обучения"
            self.kind = ChunkKind.CONTENT
            self.grade = None
            return True

        if self.top_section == "Содержание обучения":
            m = RE_GRADE_CONTENT.match(stripped)
            if m:
                g = GRADE_WORDS.get(m.group(1).lower())
                self.grade = g
                self.section_path = f"Содержание обучения > {g} класс" if g else "Содержание обучения"
                self.kind = ChunkKind.CONTENT
                return True

        if RE_PLANNED_RESULTS.match(stripped):
            self.top_section = "Планируемые результаты"
            self.section_path = "Планируемые результаты"
            self.kind = ChunkKind.GENERAL
            self.grade = None
            return True

        if RE_PERSONAL_RESULTS.match(stripped):
            self.top_section = "Планируемые результаты"
            self.section_path = "Планируемые результаты > Личностные результаты"
            self.kind = ChunkKind.PERSONAL_RESULT
            self.grade = None
            return True

        if RE_META_RESULTS.match(stripped):
            self.top_section = "Планируемые результаты"
            self.section_path = "Планируемые результаты > Метапредметные результаты"
            self.kind = ChunkKind.META_RESULT
            self.grade = None
            return True

        if RE_COGNITIVE.match(stripped):
            self.section_path = "Планируемые результаты > Метапредметные результаты > Познавательные УУД"
            self.kind = ChunkKind.META_RESULT
            self.grade = None
            return True

        if RE_COMMUNICATIVE.match(stripped):
            self.section_path = "Планируемые результаты > Метапредметные результаты > Коммуникативные УУД"
            self.kind = ChunkKind.META_RESULT
            self.grade = None
            return True

        if RE_REGULATORY.match(stripped):
            self.section_path = "Планируемые результаты > Метапредметные результаты > Регулятивные УУД"
            self.kind = ChunkKind.META_RESULT
            self.grade = None
            return True

        if RE_SUBJECT_RESULTS.match(stripped):
            self.top_section = "Планируемые результаты"
            self.section_path = "Планируемые результаты > Предметные результаты"
            self.kind = ChunkKind.SUBJECT_RESULT
            self.grade = None
            return True

        m_res = RE_GRADE_RESULT.match(stripped)
        if m_res:
            g = GRADE_WORDS.get(m_res.group(1).lower())
            self.top_section = "Планируемые результаты"
            self.section_path = (
                f"Планируемые результаты > Предметные результаты > {g} класс"
                if g
                else "Планируемые результаты > Предметные результаты"
            )
            self.kind = ChunkKind.SUBJECT_RESULT
            self.grade = g
            return True

        return False


def extract_paragraphs(
    pages: list[dict[str, Any]],
) -> list[tuple[int, str, SectionState]]:
    """Разбивает страницы на логические абзацы/пункты списков с разметкой разделов."""
    state = SectionState()
    items: list[tuple[int, str, SectionState]] = []

    for page_data in pages:
        p_num = page_data["page"]
        raw_text = page_data.get("text", "")
        lines = raw_text.split("\n")

        current_item_lines: list[str] = []

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.isdigit():
                if current_item_lines:
                    items.append((p_num, " ".join(current_item_lines), state))
                    current_item_lines = []
                continue

            # Проверяем, не является ли строка заголовком раздела
            new_state = SectionState()
            new_state.top_section = state.top_section
            new_state.section_path = state.section_path
            new_state.kind = state.kind
            new_state.grade = state.grade
            new_state.in_thematic = state.in_thematic

            if new_state.update(line_str):
                # Закрываем предыдущий пункт
                if current_item_lines:
                    items.append((p_num, " ".join(current_item_lines), state))
                    current_item_lines = []
                state = new_state
                continue

            # Проверяем начало нового пункта списка
            if RE_BULLET_START.match(line_str):
                if current_item_lines:
                    items.append((p_num, " ".join(current_item_lines), state))
                    current_item_lines = []

            current_item_lines.append(line_str)

        if current_item_lines:
            items.append((p_num, " ".join(current_item_lines), state))

    return items


def chunk_document(
    source_id: str,
    pages: list[dict[str, Any]],
    subject_id: str | None = None,
    min_chars: int = 600,
    max_chars: int = 1200,
) -> list[Chunk]:
    """Нарезает документ на фрагменты 600–1200 символов, сохраняя границы разделов и пунктов."""
    items = extract_paragraphs(pages)
    chunks: list[Chunk] = []
    page_counter: dict[int, int] = {}

    curr_texts: list[str] = []
    curr_start_page: int | None = None
    curr_end_page: int | None = None
    curr_state: SectionState | None = None

    def flush_chunk() -> None:
        nonlocal curr_texts, curr_start_page, curr_end_page, curr_state
        if not curr_texts or curr_start_page is None or curr_state is None:
            return

        text = "\n\n".join(curr_texts).strip()
        if not text:
            return

        seq = page_counter.get(curr_start_page, 0) + 1
        page_counter[curr_start_page] = seq
        cid = f"{source_id}:{curr_start_page}:{seq}"

        page_end = curr_end_page if (curr_end_page and curr_end_page > curr_start_page) else None

        chunks.append(
            Chunk(
                chunk_id=cid,
                source_id=source_id,
                page=curr_start_page,
                page_end=page_end,
                section=curr_state.section_path,
                text=text,
                kind=curr_state.kind,
                grade=curr_state.grade,
                subject_id=subject_id,
            )
        )
        curr_texts = []
        curr_start_page = None
        curr_end_page = None

    for p_num, text_item, st in items:
        if not text_item.strip():
            continue

        # Если сменился раздел, текущий фрагмент не может пересекать раздел
        if curr_state is not None and (
            curr_state.section_path != st.section_path or curr_state.kind != st.kind
        ):
            flush_chunk()

        if curr_state is None or not curr_texts:
            curr_state = st
            curr_start_page = p_num
            curr_end_page = p_num
            curr_texts.append(text_item)
            continue

        # Проверяем добавление следующего элемента
        projected_len = sum(len(t) for t in curr_texts) + len(text_item) + 2

        if projected_len <= max_chars:
            curr_texts.append(text_item)
            curr_end_page = p_num
        else:
            # Превышает max_chars
            curr_len = sum(len(t) for t in curr_texts)
            if curr_len >= min_chars:
                flush_chunk()
                curr_state = st
                curr_start_page = p_num
                curr_end_page = p_num
                curr_texts.append(text_item)
            else:
                # Меньше min_chars — если один элемент очень длинный, добавляем и сразу сбрасываем
                curr_texts.append(text_item)
                curr_end_page = p_num
                flush_chunk()

    flush_chunk()
    return chunks


def chunk_pages_file(
    pages_file: Path,
    source_id: str,
    subject_id: str | None = None,
) -> list[Chunk]:
    """Загружает страницы из JSONL и нарезает на фрагменты."""
    pages: list[dict[str, Any]] = []
    if not pages_file.exists():
        return []

    with open(pages_file, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                pages.append(json.loads(line_str))

    return chunk_document(source_id, pages, subject_id)


def save_chunks(chunks: list[Chunk], output_file: Path) -> None:
    """Сохраняет список фрагментов в JSONL файл."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for ch in chunks:
            f.write(ch.model_dump_json() + "\n")


def load_chunks(chunks_file: Path) -> list[Chunk]:
    """Загружает фрагменты из JSONL файла."""
    if not chunks_file.exists():
        return []
    chunks: list[Chunk] = []
    with open(chunks_file, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                chunks.append(Chunk.model_validate_json(line_str))
    return chunks
