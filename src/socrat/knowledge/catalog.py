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
from typing import Any

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

GRADE_WORDS: dict[str, int] = {
    "1": 1,
    "первом": 1,
    "первый": 1,
    "2": 2,
    "втором": 2,
    "второй": 2,
    "3": 3,
    "третьем": 3,
    "третий": 3,
    "4": 4,
    "четвертом": 4,
    "четвёртом": 4,
    "четвертый": 4,
    "четвёртый": 4,
    "5": 5,
    "пятом": 5,
    "пятый": 5,
    "6": 6,
    "шестом": 6,
    "шестой": 6,
    "7": 7,
    "седьмом": 7,
    "седьмой": 7,
    "8": 8,
    "восьмом": 8,
    "восьмой": 8,
    "9": 9,
    "девятом": 9,
    "девятый": 9,
    "10": 10,
    "десятом": 10,
    "десятый": 10,
    "11": 11,
    "одиннадцатом": 11,
    "одиннадцатый": 11,
}

SUBHEAD_PREFIXES: tuple[str, ...] = (
    "1)",
    "2)",
    "3)",
    "4)",
    "5)",
    "6)",
    "1.",
    "2.",
    "3.",
    "4.",
    "5.",
    "6.",
    "Базовые логические",
    "Базовые исследовательские",
    "Работа с информацией",
    "Универсальные учебные познавательные",
    "Универсальные учебные коммуникативные",
    "Универсальные учебные регулятивные",
    "Самоорганизация",
    "Самоконтроль",
    "Общение",
    "Совместная деятельность",
    "Гражданско-патриотического",
    "Духовно-нравственного",
    "Эстетического",
    "Физического",
    "Трудового",
    "Экологического",
    "Ценности научного",
)


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


def extract_outcomes_from_frp(
    source_id: str,
    subject_id: str,
    doc_url: str,
    pages_dir: Path,
    level: str = "noo",
) -> tuple[list[Outcome], list[Outcome]]:
    """Извлекает предметные и метапредметные результаты из ФРП предмета.

    Возвращает (subject_outcomes, meta_outcomes).
    """
    p_file = pages_dir / f"{source_id}.jsonl"
    if not p_file.exists():
        logger.warning("Pages file not found: %s", p_file)
        return [], []

    pages: list[dict[str, Any]] = []
    with open(p_file, encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if line_str:
                pages.append(json.loads(line_str))

    # Определяем границы страниц для разделов
    p_personal: int | None = None
    p_subject: int | None = None
    p_thematic: int | None = None

    for item in pages:
        p = item["page"]
        for l_item in item.get("text", "").split("\n"):
            l_str = l_item.strip()
            if re.match(r"^\s*ЛИЧНОСТНЫЕ\s+РЕЗУЛЬТАТЫ\s*$", l_str, re.I) and p_personal is None:
                p_personal = p
            elif re.match(r"^\s*ПРЕДМЕТНЫЕ\s+РЕЗУЛЬТАТЫ\s*$", l_str, re.I) and p_subject is None:
                p_subject = p
            elif re.match(r"^\s*ТЕМАТИЧЕСКОЕ\s+ПЛАНИРОВАНИЕ\s*$", l_str, re.I) and p_thematic is None:
                p_thematic = p

    p_personal = p_personal or 18
    p_subject = p_subject or 21
    p_thematic = p_thematic or len(pages)

    # 1. Извлечение метапредметных и личностных результатов
    meta_annotated: list[tuple[int, str]] = []
    for p in range(p_personal, p_subject + 1):
        txt = get_page_text(source_id, p, pages_dir)
        if not txt:
            continue
        for l_line in txt.splitlines():
            l_strip = l_line.strip()
            if not l_strip or l_strip.isdigit():
                continue
            meta_annotated.append((p, l_strip))

    meta_items: dict[str, list[tuple[int, str]]] = {
        "PERSONAL": [],
        "COGNITIVE": [],
        "COMMUNICATIVE": [],
        "REGULATORY": [],
    }

    cur_sec: str | None = None
    cur_p: int | None = None
    cur_lines: list[str] = []

    def flush_meta() -> None:
        nonlocal cur_lines, cur_p, cur_sec
        if cur_lines and cur_sec:
            text = " ".join(cur_lines).strip()
            if text.endswith(";") or text.endswith("."):
                meta_items[cur_sec].append((cur_p or p_personal, text))
        cur_lines = []
        cur_p = None

    for p, line in meta_annotated:
        if line.startswith("ЛИЧНОСТНЫЕ РЕЗУЛЬТАТЫ"):
            flush_meta()
            cur_sec = "PERSONAL"
            continue
        if "Познавательные универсальные учебные действия" in line:
            flush_meta()
            cur_sec = "COGNITIVE"
            continue
        if "Коммуникативные универсальные учебные действия" in line:
            flush_meta()
            cur_sec = "COMMUNICATIVE"
            continue
        if "Регулятивные универсальные учебные действия" in line:
            flush_meta()
            cur_sec = "REGULATORY"
            continue
        if "ПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ" in line:
            flush_meta()
            cur_sec = None
            break

        if cur_sec is None:
            continue
        if any(line.startswith(sh) for sh in SUBHEAD_PREFIXES):
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

    meta_outcomes: list[Outcome] = []
    sec_map = {
        "PERSONAL": (OutcomeType.PERSONAL, "L", "Личностные результаты"),
        "COGNITIVE": (OutcomeType.COGNITIVE, "C", "Метапредметные результаты > Познавательные УУД"),
        "COMMUNICATIVE": (OutcomeType.COMMUNICATIVE, "K", "Метапредметные результаты > Коммуникативные УУД"),
        "REGULATORY": (OutcomeType.REGULATORY, "R", "Метапредметные результаты > Регулятивные УУД"),
    }
    for sec_key, (otype, code, sec_name) in sec_map.items():
        for idx, (p, text) in enumerate(meta_items[sec_key], start=1):
            meta_outcomes.append(
                Outcome(
                    outcome_id=f"{subject_id}-{level}-{code}-{idx:02d}",
                    type=otype,
                    text=text,
                    quote_is_verbatim=True,
                    grade=None,
                    subject_id=subject_id,
                    source_id=source_id,
                    section=sec_name,
                    page=p,
                    source_url=f"{doc_url}#page={p}",
                )
            )

    # 2. Извлечение предметных результатов
    subj_annotated: list[tuple[int, str]] = []
    for p in range(p_subject, p_thematic):
        txt = get_page_text(source_id, p, pages_dir)
        if not txt:
            continue
        for l_line in txt.splitlines():
            l_strip = l_line.strip()
            if not l_strip or l_strip.isdigit():
                continue
            subj_annotated.append((p, l_strip))

    grades_items: dict[int, list[tuple[int, str]]] = {1: [], 2: [], 3: [], 4: []}
    cur_grade: int | None = None
    cur_p = None
    cur_lines = []

    def flush_grade() -> None:
        nonlocal cur_lines, cur_p, cur_grade
        if cur_lines and cur_grade in grades_items:
            text = " ".join(cur_lines).strip()
            if text.endswith(";") or text.endswith("."):
                grades_items[cur_grade].append((cur_p or p_subject, text))
        cur_lines = []
        cur_p = None

    for p, line in subj_annotated:
        m = re.match(
            r"^\s*К\s+концу\s+обучения\s+в[о]?\s+(\d+|первом|втором|третьем|четв[её]ртом)\s+классе",
            line,
            re.I,
        )
        if m:
            flush_grade()
            cur_grade = GRADE_WORDS.get(m.group(1).lower())
            continue
        if re.match(r"^\s*ТЕМАТИЧЕСКОЕ\s+ПЛАНИРОВАНИЕ", line, re.I):
            flush_grade()
            break
        if (
            cur_grade is None
            or "обучающийся получит" in line
            or "обучающийся научится" in line
            or "по отдельным темам" in line
            or line == "ПРЕДМЕТНЫЕ РЕЗУЛЬТАТЫ"
        ):
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

    subject_outcomes: list[Outcome] = []
    for g, items in grades_items.items():
        for idx, (p, text) in enumerate(items, start=1):
            subject_outcomes.append(
                Outcome(
                    outcome_id=f"{subject_id}-{g}-P-{idx:02d}",
                    type=OutcomeType.SUBJECT,
                    text=text,
                    quote_is_verbatim=True,
                    grade=g,
                    subject_id=subject_id,
                    source_id=source_id,
                    section=f"Предметные результаты > {g} класс",
                    page=p,
                    source_url=f"{doc_url}#page={p}",
                )
            )

    return subject_outcomes, meta_outcomes


def extract_math_outcomes_from_frp(
    source_id: str,
    doc_url: str,
    total_pages: int,
    pages_dir: Path,
) -> tuple[list[Outcome], list[Outcome]]:
    """Обратная совместимость: извлечение результатов для math."""
    return extract_outcomes_from_frp(
        source_id=source_id,
        subject_id="math",
        doc_url=doc_url,
        pages_dir=pages_dir,
        level="noo",
    )


def build_catalog_math_noo(settings: Settings) -> list[Outcome]:
    """Строит каталог для math НОО (включая P01..L01 кейса SK01)."""
    catalog_dir = settings.knowledge_dir / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = settings.knowledge_dir / "pages"

    sk01_ref = load_sk01_reference_outcomes(settings.case_reference_dir)
    doc_url = "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_matematika_1-4.pdf"

    subject_outcomes, meta_outcomes = extract_outcomes_from_frp(
        source_id="FRP-MATH-2025",
        subject_id="math",
        doc_url=doc_url,
        pages_dir=pages_dir,
        level="noo",
    )

    full_math_outcomes = list(sk01_ref) + subject_outcomes + meta_outcomes

    math_catalog_path = catalog_dir / "noo_math.json"
    math_data = [o.model_dump() for o in full_math_outcomes]
    math_catalog_path.write_text(json.dumps(math_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d math outcomes to %s", len(full_math_outcomes), math_catalog_path)

    meta_catalog_path = catalog_dir / "noo_meta.json"
    meta_data = [o.model_dump() for o in meta_outcomes]
    meta_catalog_path.write_text(json.dumps(meta_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d meta outcomes to %s", len(meta_outcomes), meta_catalog_path)

    return full_math_outcomes


def build_all_catalogs(settings: Settings) -> dict[str, list[Outcome]]:
    """Строит каталоги для всех доступных предметов НОО:
    - noo_math.json (с эталонами SK01)
    - noo_russian.json
    - noo_literary_reading.json
    - noo_world.json
    - noo_meta.json (все мета и личностные результаты)
    """
    catalog_dir = settings.knowledge_dir / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    pages_dir = settings.knowledge_dir / "pages"

    sources_config = [
        (
            "FRP-MATH-2025",
            "math",
            "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_matematika_1-4.pdf",
        ),
        (
            "FRP-RUSSIAN-NOO-2025",
            "russian",
            "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_russkij-yazyk_1-4.pdf",
        ),
        (
            "FRP-LITERARY-READING-NOO-2025",
            "literary_reading",
            "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_literaturnoe-chtenie_1-4.pdf",
        ),
        (
            "FRP-WORLD-NOO-2025",
            "world",
            "https://edsoo.ru/wp-content/uploads/2025/07/2025_noo_frp_okruzhayushhij-mir_1-4.pdf",
        ),
    ]

    sk01_ref = load_sk01_reference_outcomes(settings.case_reference_dir)
    results: dict[str, list[Outcome]] = {}
    all_meta_outcomes: list[Outcome] = []

    for source_id, subject_id, url in sources_config:
        s_outcomes, m_outcomes = extract_outcomes_from_frp(
            source_id=source_id,
            subject_id=subject_id,
            doc_url=url,
            pages_dir=pages_dir,
            level="noo",
        )

        all_meta_outcomes.extend(m_outcomes)

        if subject_id == "math":
            full_subject = list(sk01_ref) + s_outcomes + m_outcomes
        else:
            full_subject = s_outcomes + m_outcomes

        results[subject_id] = full_subject
        subj_file = catalog_dir / f"noo_{subject_id}.json"
        subj_file.write_text(
            json.dumps([o.model_dump() for o in full_subject], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved %d outcomes for %s to %s", len(full_subject), subject_id, subj_file)

    # Сохраняем объединенный noo_meta.json
    meta_file = catalog_dir / "noo_meta.json"
    meta_file.write_text(
        json.dumps([o.model_dump() for o in all_meta_outcomes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Saved %d total meta outcomes to %s", len(all_meta_outcomes), meta_file)

    return results
