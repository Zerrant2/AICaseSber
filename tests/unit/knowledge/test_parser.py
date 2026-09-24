"""Тесты парсера PDF (G3)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from socrat.knowledge.parser import (
    clean_page_text,
    find_repeating_lines,
    get_page_text,
    get_source_page_range,
    parse_and_save_pdf,
    parse_pdf,
)


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    """Создаёт тестовый двухстраничный PDF прямо в тесте."""
    pdf_path = tmp_path / "test_doc.pdf"
    doc = pymupdf.open()

    # Страница 1
    page1 = doc.new_page()
    page1.insert_text((50, 50), "Repeating document header\nText of first page with hyphen-\nation of words.")

    # Страница 2
    page2 = doc.new_page()
    page2.insert_text((50, 50), "Repeating document header\nText of second page.\nAnother line.")

    doc.save(pdf_path)
    doc.close()
    return pdf_path


def test_clean_page_text_hyphenation_and_soft_hyphens():
    repeating = {"Колонтитул"}
    raw = "Колонтитул\nСлово с мягким\xadпереносом и обыч-\nным переносом."
    cleaned = clean_page_text(raw, repeating)
    assert "Колонтитул" not in cleaned
    assert "\xad" not in cleaned
    assert "обычным переносом" in cleaned
    assert "мягкимпереносом" in cleaned


def test_parse_and_save_synthetic_pdf(synthetic_pdf: Path, tmp_path: Path):
    pages_dir = tmp_path / "pages"
    out_file = parse_and_save_pdf(synthetic_pdf, "TEST-01", pages_dir)

    assert out_file.exists()
    p1 = get_page_text("TEST-01", 1, pages_dir)
    p2 = get_page_text("TEST-01", 2, pages_dir)

    assert p1 is not None
    assert p2 is not None
    # 1-based indexing
    assert "hyphenation of words" in p1
    assert "second page" in p2
    assert "Repeating document header" not in p1  # Repeating header dropped
    assert "Repeating document header" not in p2
    # Несуществующая страница
    assert get_page_text("TEST-01", 99, pages_dir) is None


def test_repeating_lines_threshold():
    pages_lines = [
        ["Header line", "Content page 1"],
        ["Header line", "Content page 2"],
        ["Header line", "Content page 3"],
    ]
    rep = find_repeating_lines(pages_lines, threshold_ratio=0.5)
    assert "Header line" in rep
    assert "Content page 1" not in rep


def test_parse_pdf_page_range(tmp_path: Path):
    """Проверка извлечения только заданного диапазона страниц PDF (G18)."""
    pdf_path = tmp_path / "multi_doc.pdf"
    doc = pymupdf.open()
    for idx in range(1, 6):
        p = doc.new_page()
        p.insert_text((50, 50), f"Content of page {idx}")
    doc.save(pdf_path)
    doc.close()

    # Извлекаем только страницы 2..4
    pages = parse_pdf(pdf_path, "MULTI-01", page_range=[2, 4])
    assert len(pages) == 3
    page_numbers = [p["page"] for p in pages]
    assert page_numbers == [2, 3, 4]
    assert "Content of page 2" in pages[0]["text"]
    assert "Content of page 4" in pages[2]["text"]


def test_parse_and_save_pdf_with_sources_yaml_page_range(tmp_path: Path):
    """Проверка автоматического применения page_range из sources.yaml (G18)."""
    pdf_path = tmp_path / "shared_doc.pdf"
    doc = pymupdf.open()
    for idx in range(1, 6):
        p = doc.new_page()
        p.insert_text((50, 50), f"Shared content page {idx}")
    doc.save(pdf_path)
    doc.close()

    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        """
sources:
  - source_id: TEST-SUB-01
    title: "Тестовый подраздел"
    url: "https://example.com/test.pdf"
    page_range: [2, 3]
""",
        encoding="utf-8",
    )

    pages_dir = tmp_path / "pages"
    out_file = parse_and_save_pdf(pdf_path, "TEST-SUB-01", pages_dir, sources_file=sources_file)
    assert out_file.exists()

    assert get_page_text("TEST-SUB-01", 1, pages_dir) is None
    assert get_page_text("TEST-SUB-01", 2, pages_dir) is not None
    assert get_page_text("TEST-SUB-01", 3, pages_dir) is not None
    assert get_page_text("TEST-SUB-01", 4, pages_dir) is None


def test_sources_yaml_contains_page_ranges():
    """Проверка наличия корректных page_range в основном sources.yaml (G18)."""
    assert get_source_page_range("FRP-MATH-OOO-2025") == [1, 39]
    assert get_source_page_range("FRP-ALGEBRA-OOO-2025") == [40, 69]
    assert get_source_page_range("FRP-GEOMETRY-OOO-2025") == [70, 90]
