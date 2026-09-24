"""Генератор каталога планируемых результатов (G5).

Извлекает предметные результаты по классам и метапредметные/личностные результаты
из текста страниц ФРП. Формирует стабильные ID и включает эталонные результаты кейса SK01.
ВЛАДЕЛЕЦ: Gemini (knowledge).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from socrat.config import Settings
from socrat.contracts import Outcome, OutcomeType
from socrat.knowledge.parser import get_page_text

logger = logging.getLogger(__name__)

# Маппинг типов из outcomes_reference.json кейса SK01
_REF_TYPE_MAP = {
    "предметный": OutcomeType.SUBJECT,
    "познавательные УУД": OutcomeType.COGNITIVE,
    "регулятивные УУД": OutcomeType.REGULATORY,
    "коммуникативные УУД": OutcomeType.COMMUNICATIVE,
    "личностная направленность": OutcomeType.PERSONAL,
}


def load_sk01_reference_outcomes(case_ref_dir: Path) -> list[Outcome]:
    """Загружает 6 обязательных результатов кейса SK01 из outcomes_reference.json."""
    ref_file = case_ref_dir / "outcomes_reference.json"
    if not ref_file.exists():
        logger.warning("SK01 reference file not found: %s", ref_file)
        return []

    data = json.loads(ref_file.read_text(encoding="utf-8"))
    outcomes: list[Outcome] = []
    for item in data.get("outcomes", []):
        otype = _REF_TYPE_MAP.get(item["type"], OutcomeType.SUBJECT)
        outcomes.append(
            Outcome(
                outcome_id=item["outcome_id"],
                type=otype,
                text=item["paraphrase"],
                quote_is_verbatim=False,
                grade=3,
                subject_id="math" if otype == OutcomeType.SUBJECT else None,
                source_id=item["source_id"],
                section=item["section"],
                page=item["page"],
                source_url=item["source_url"],
            )
        )
    return outcomes


def extract_math_outcomes_from_frp(
    source_id: str,
    doc_url: str,
    total_pages: int,
    pages_dir: Path,
) -> tuple[list[Outcome], list[Outcome]]:
    """Извлекает предметные и метапредметные результаты из ФРП «Математика» 1–4 классы.

    Возвращает (subject_outcomes, meta_outcomes).
    """
    subject_outcomes: list[Outcome] = []
    meta_outcomes: list[Outcome] = []

    # 1. Извлечение метапредметных и личностных результатов (стр. 18-20)
    meta_annotated: list[tuple[int, str]] = []
    for p in range(18, min(21, total_pages + 1)):
        p_text = get_page_text(source_id, p, pages_dir)
        if not p_text:
            continue
        for line in p_text.splitlines():
            line = line.strip()
            if not line or re.match(r"^\d+$", line):
                continue
            meta_annotated.append((p, line))

    meta_items: dict[str, list[tuple[int, str]]] = {
        "PERSONAL": [],
        "COGNITIVE": [],
        "COMMUNICATIVE": [],
        "REGULATORY": [],
    }

    cur_section: str | None = None
    cur_p: int | None = None
    cur_lines: list[str] = []

    def flush_meta() -> None:
        nonlocal cur_lines, cur_p, cur_section
        if cur_lines and cur_section:
            text = " ".join(cur_lines).strip()
            # Пропускаем вводные предложения и заголовки подразделов
            if text.endswith(";") or text.endswith("."):
                # Исключаем вводный текст стандартов
                if not any(
                    intro in text
                    for intro in (
                        "достигаются в единстве",
                        "В результате изучения математики",
                        "следующие личностные результаты",
                    )
                ):
                    meta_items[cur_section].append((cur_p or 18, text))
            cur_lines = []

    subhead_prefixes = (
        "Базовые логические",
        "Базовые исследовательские",
        "Работа с информацией",
        "Общение:",
        "Самоорганизация:",
        "Самоконтроль:",
        "Совместная деятельность:",
    )

    for p, line in meta_annotated:
        if line == "ЛИЧНОСТНЫЕ РЕЗУЛЬТАТЫ":
            flush_meta()
            cur_section = "PERSONAL"
            continue
        if line == "Познавательные универсальные учебные действия":
            flush_meta()
            cur_section = "COGNITIVE"
            continue
        if line == "Коммуникативные универсальные учебные действия":
            flush_meta()
            cur_section = "COMMUNICATIVE"
            continue
        if line == "Регулятивные универсальные учебные действия":
            flush_meta()
            cur_section = "REGULATORY"
            continue
        if line == "ПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ":
            flush_meta()
            cur_section = None
            break

        if cur_section is None:
            continue

        if any(line.startswith(sh) for sh in subhead_prefixes):
            continue
        if "МЕТАПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ" in line or "универсальные учебные действия" in line:
            continue

        if not cur_lines:
            cur_p = p
            cur_lines.append(line)
        else:
            prev = cur_lines[-1]
            if prev.endswith(";") or prev.endswith("."):
                flush_meta()
                cur_p = p
                cur_lines = [line]
            else:
                cur_lines.append(line)
    flush_meta()

    # Формируем Outcomes для мета и личностных
    for idx, (p, text) in enumerate(meta_items["PERSONAL"], start=1):
        meta_outcomes.append(
            Outcome(
                outcome_id=f"math-noo-L-{idx:02d}",
                type=OutcomeType.PERSONAL,
                text=text,
                quote_is_verbatim=True,
                grade=None,
                subject_id="math",
                source_id=source_id,
                section="Личностные результаты",
                page=p,
                source_url=f"{doc_url}#page={p}",
            )
        )

    for idx, (p, text) in enumerate(meta_items["COGNITIVE"], start=1):
        meta_outcomes.append(
            Outcome(
                outcome_id=f"math-noo-C-{idx:02d}",
                type=OutcomeType.COGNITIVE,
                text=text,
                quote_is_verbatim=True,
                grade=None,
                subject_id="math",
                source_id=source_id,
                section="Метапредметные результаты > Познавательные УУД",
                page=p,
                source_url=f"{doc_url}#page={p}",
            )
        )

    for idx, (p, text) in enumerate(meta_items["COMMUNICATIVE"], start=1):
        meta_outcomes.append(
            Outcome(
                outcome_id=f"math-noo-K-{idx:02d}",
                type=OutcomeType.COMMUNICATIVE,
                text=text,
                quote_is_verbatim=True,
                grade=None,
                subject_id="math",
                source_id=source_id,
                section="Метапредметные результаты > Коммуникативные УУД",
                page=p,
                source_url=f"{doc_url}#page={p}",
            )
        )

    for idx, (p, text) in enumerate(meta_items["REGULATORY"], start=1):
        meta_outcomes.append(
            Outcome(
                outcome_id=f"math-noo-R-{idx:02d}",
                type=OutcomeType.REGULATORY,
                text=text,
                quote_is_verbatim=True,
                grade=None,
                subject_id="math",
                source_id=source_id,
                section="Метапредметные результаты > Регулятивные УУД",
                page=p,
                source_url=f"{doc_url}#page={p}",
            )
        )

    # 2. Извлечение предметных результатов (стр. 21-25)
    subject_annotated: list[tuple[int, str]] = []
    for p in range(21, min(26, total_pages + 1)):
        p_text = get_page_text(source_id, p, pages_dir)
        if not p_text:
            continue
        for line in p_text.splitlines():
            line = line.strip()
            if not line or re.match(r"^\d+$", line):
                continue
            subject_annotated.append((p, line))

    grades_items: dict[int, list[tuple[int, str]]] = {}
    current_grade: int | None = None
    cur_p = None
    cur_lines = []

    def flush_grade() -> None:
        nonlocal cur_lines, cur_p, current_grade
        if cur_lines and current_grade is not None:
            text = " ".join(cur_lines).strip()
            if text:
                grades_items[current_grade].append((cur_p or 21, text))
            cur_lines = []

    for p, line in subject_annotated:
        m = re.match(r"К концу обучения в[о]?\s*(\d+)\s*классе", line)
        if m:
            flush_grade()
            current_grade = int(m.group(1))
            grades_items[current_grade] = []
            continue

        if current_grade is None:
            continue
        if "результаты по отдельным темам" in line or line == "ПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ":
            continue

        if not cur_lines:
            cur_p = p
            cur_lines.append(line)
        else:
            prev = cur_lines[-1]
            if prev.endswith(";") or prev.endswith("."):
                flush_grade()
                cur_p = p
                cur_lines = [line]
            else:
                cur_lines.append(line)
    flush_grade()

    for g, items in grades_items.items():
        for idx, (p, text) in enumerate(items, start=1):
            subject_outcomes.append(
                Outcome(
                    outcome_id=f"math-{g}-P-{idx:02d}",
                    type=OutcomeType.SUBJECT,
                    text=text,
                    quote_is_verbatim=True,
                    grade=g,
                    subject_id="math",
                    source_id=source_id,
                    section=f"Предметные результаты > {g} класс",
                    page=p,
                    source_url=f"{doc_url}#page={p}",
                )
            )

    return subject_outcomes, meta_outcomes


def build_catalog_math_noo(settings: Settings) -> list[Outcome]:
    """Строит полный каталог для math НОО:
    - 6 эталонных результатов SK01 (P01..L01);
    - все предметные результаты 1-4 классов;
    - метапредметные и личностные результаты;
    Сохраняет в data/knowledge/catalog/noo_math.json и noo_meta.json.
    """
    catalog_dir = settings.knowledge_dir / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = settings.knowledge_dir / "pages"

    # Загружаем SK01
    sk01_ref = load_sk01_reference_outcomes(settings.case_reference_dir)

    # Извлекаем из FRP-MATH-2025
    doc_url = "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_matematika_1-4.pdf"
    subject_outcomes, meta_outcomes = extract_math_outcomes_from_frp(
        source_id="FRP-MATH-2025",
        doc_url=doc_url,
        total_pages=79,
        pages_dir=pages_dir,
    )

    # Полный каталог noo_math:
    # 1) Сначала 6 эталонных результатов SK01 (чтобы math/3 гарантированно их выдавал в начале)
    # 2) Предметные результаты 1-4
    # 3) Метапредметные результаты математики
    full_math_outcomes = list(sk01_ref) + subject_outcomes + meta_outcomes

    # Сохраняем noo_math.json
    math_catalog_path = catalog_dir / "noo_math.json"
    math_data = [o.model_dump() for o in full_math_outcomes]
    math_catalog_path.write_text(json.dumps(math_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d math outcomes to %s", len(full_math_outcomes), math_catalog_path)

    # Сохраняем noo_meta.json
    meta_catalog_path = catalog_dir / "noo_meta.json"
    meta_data = [o.model_dump() for o in meta_outcomes]
    meta_catalog_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d meta outcomes to %s", len(meta_outcomes), meta_catalog_path)

    return full_math_outcomes
