"""Парсер PDF и очистка текста страниц (G3).

Извлекает текст страниц PDF, склеивает переносы, удаляет повторяющиеся колонтитулы,
нормализует пробелы и сохраняет страницы в data/knowledge/pages/<source_id>.jsonl.
ВЛАДЕЛЕЦ: Gemini (knowledge).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

import pymupdf

logger = logging.getLogger(__name__)


def find_repeating_lines(pages_raw_lines: list[list[str]], threshold_ratio: float = 0.5) -> set[str]:
    """Находит строки (колонтитулы), встречающиеся более чем на 50% страниц."""
    total_pages = len(pages_raw_lines)
    if total_pages < 2:
        return set()

    line_counts: Counter[str] = Counter()
    for lines in pages_raw_lines:
        unique_lines = {line.strip() for line in lines if line.strip()}
        line_counts.update(unique_lines)

    threshold = total_pages * threshold_ratio
    return {line for line, count in line_counts.items() if count > threshold}


def clean_page_text(raw_text: str, repeating_lines: set[str]) -> str:
    """Очищает текст отдельной страницы:
    - убирает повторяющиеся колонтитулы;
    - заменяет мягкий перенос (\xad) на пустую строку;
    - склеивает переносы строк: слово-\\nпродолжение -> словопродолжение;
    - нормализует пробелы.
    """
    # 1. Фильтруем строки по колонтитулам
    filtered_lines = [line for line in raw_text.splitlines() if line.strip() not in repeating_lines]
    text = "\n".join(filtered_lines)

    # 2. Мягкие переносы
    text = text.replace("\xad", "")

    # 3. Склейка переносов слов на стыке строк
    # Пример: "слово-\nпродолжение" -> "словопродолжение"
    text = re.sub(r"([а-яёА-ЯЁa-zA-Z]+)-\s*\n\s*([а-яёА-ЯЁa-zA-Z]+)", r"\1\2", text)

    # 4. Нормализация пробелов внутри строк и обрезка краев
    cleaned_lines = [re.sub(r"[^\S\n]+", " ", line_item).strip() for line_item in text.splitlines()]
    text = "\n".join(cleaned_lines)

    # 5. Схлопывание лишних пустых строк (не более 1 пустой строки подряд)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def get_source_page_range(source_id: str, sources_file: Path | None = None) -> list[int] | None:
    """Извлекает page_range для source_id из sources.yaml (G18)."""
    if sources_file is None:
        from socrat.config import get_settings

        sources_file = get_settings().sources_file
    if not sources_file.exists():
        return None
    try:
        import yaml

        with open(sources_file, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for s in data.get("sources", []):
            if s.get("source_id") == source_id:
                pr = s.get("page_range")
                if isinstance(pr, list) and len(pr) == 2:
                    return [int(pr[0]), int(pr[1])]
    except Exception as exc:
        logger.warning("Could not read page_range for %s: %s", source_id, exc)
    return None


def parse_pdf(
    pdf_path: Path,
    source_id: str,
    page_range: list[int] | tuple[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Парсит PDF-файл и возвращает список страниц с 1-based нумерацией.

    Если указан page_range=[start, end], извлекает только страницы из этого диапазона (включительно).
    """
    doc = pymupdf.open(pdf_path)
    total_pages = doc.page_count

    # Первый проход: собираем сырые строки для поиска колонтитулов
    pages_raw: list[str] = []
    pages_lines: list[list[str]] = []
    for idx in range(total_pages):
        raw_t = doc[idx].get_text("text")
        pages_raw.append(raw_t)
        pages_lines.append(raw_t.splitlines())

    repeating = find_repeating_lines(pages_lines)
    logger.debug("Source %s: found %d repeating header/footer lines", source_id, len(repeating))

    start_p = page_range[0] if page_range and len(page_range) >= 1 else 1
    end_p = page_range[1] if page_range and len(page_range) >= 2 else total_pages

    results: list[dict[str, Any]] = []
    for idx in range(total_pages):
        page_num = idx + 1  # 1-based index в PDF
        if not (start_p <= page_num <= end_p):
            continue
        cleaned = clean_page_text(pages_raw[idx], repeating)
        results.append(
            {
                "source_id": source_id,
                "page": page_num,
                "text": cleaned,
            }
        )

    doc.close()
    return results


def save_pages_jsonl(pages: list[dict[str, Any]], output_file: Path) -> None:
    """Сохраняет страницы в .jsonl файл."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for p in pages:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")


def parse_and_save_pdf(
    pdf_path: Path,
    source_id: str,
    pages_dir: Path,
    page_range: list[int] | tuple[int, int] | None = None,
    sources_file: Path | None = None,
) -> Path:
    """Парсит PDF и записывает результат в <pages_dir>/<source_id>.jsonl."""
    if page_range is None:
        page_range = get_source_page_range(source_id, sources_file)

    out_path = pages_dir / f"{source_id}.jsonl"
    pages = parse_pdf(pdf_path, source_id, page_range=page_range)
    save_pages_jsonl(pages, out_path)
    # Сбрасываем кэш страниц для данного source_id, если он был
    _load_pages_map.cache_clear()
    return out_path


@lru_cache(maxsize=32)
def _load_pages_map(source_id: str, pages_dir_str: str) -> dict[int, str]:
    pages_file = Path(pages_dir_str) / f"{source_id}.jsonl"
    if not pages_file.exists():
        return {}
    res: dict[int, str] = {}
    with open(pages_file, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            res[item["page"]] = item["text"]
    return res


def get_page_text(source_id: str, page: int, pages_dir: Path | str | None = None) -> str | None:
    """Возвращает очищенный текст страницы по её 1-based номеру."""
    if pages_dir is None:
        from socrat.config import get_settings

        pages_dir = get_settings().knowledge_dir / "pages"
    pages_dir_str = str(Path(pages_dir).resolve())
    mapping = _load_pages_map(source_id, pages_dir_str)
    return mapping.get(page)
