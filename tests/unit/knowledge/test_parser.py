"""Тесты парсера PDF (G3)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from socrat.knowledge.parser import clean_page_text, find_repeating_lines, get_page_text, parse_and_save_pdf


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
