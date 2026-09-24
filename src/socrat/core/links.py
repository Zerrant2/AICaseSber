"""Строка «карты результатов»: страница/раздел/URL берутся из каталога, не из LLM."""

from __future__ import annotations

import re

from socrat.contracts import KnowledgeBase, Outcome, OutcomeLink


def _norm(s: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]+", " ", s.lower().replace("ё", "е")).strip()


def quote_on_page(quote: str, page: str) -> bool:
    q, p = _norm(quote), _norm(page)
    if not q:
        return False
    if q in p:
        return True
    head = " ".join(q.split()[:8])
    return bool(head) and head in p


def make_link(knowledge: KnowledgeBase, o: Outcome, rationale: str) -> OutcomeLink:
    src = knowledge.get_source(o.source_id)
    verified, note = True, "ID, раздел и страница взяты из каталога нормативной базы."
    if not o.quote_is_verbatim:
        note = "Методический пересказ из фиксированного справочника (не цитата); страница — по нумерации PDF."
    else:
        page = knowledge.get_page_text(o.source_id, o.page)
        if page:
            verified = quote_on_page(o.text, page)
            note = (
                "Формулировка найдена на указанной странице."
                if verified
                else "Формулировка на указанной странице не найдена — проверьте ссылку."
            )
    return OutcomeLink(
        outcome_id=o.outcome_id,
        type=o.type,
        text=o.text,
        source_id=o.source_id,
        source_title=src.title if src else o.source_id,
        section=o.section,
        page=o.page,
        source_url=o.source_url,
        rationale=(rationale or "").strip() or "Задание проверяет этот результат.",
        verified=verified,
        verification_note=note,
    )
