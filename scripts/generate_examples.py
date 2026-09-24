"""Генерирует материалы для сдачи в examples/ на РЕАЛЬНОЙ модели (нужен .env с LLM_API_KEY):

  python scripts/generate_examples.py
  python scripts/generate_examples.py --grade 5 --subject russian --subject-name "Русский язык" --topic "Имя существительное"

Пишет: examples/<slug>/work.json, Задания_*.docx, Методичка_*.docx, analysis.json/.docx (для math-3).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from socrat.config import get_settings  # noqa: E402
from socrat.contracts import ErrorAnalysisRequest, GenerationRequest, LevelChoice  # noqa: E402
from socrat.core import build_core  # noqa: E402
from socrat.export.docx_exporter import DocxExporter  # noqa: E402
from socrat.testing.fakes import FakeKnowledgeBase  # noqa: E402


def knowledge(settings):
    try:
        from socrat.knowledge import LocalKnowledgeBase

        return LocalKnowledgeBase(settings)
    except ImportError:
        print("! knowledge ещё не смёржен — используется справочник кейса (только математика 3 класс)")
        return FakeKnowledgeBase()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade", type=int, default=3)
    ap.add_argument("--subject", default="math")
    ap.add_argument("--subject-name", default="Математика")
    ap.add_argument("--topic", default="Умножение и деление в пределах 100 и задачи в одно-два действия")
    ap.add_argument("--level", default="all")
    ap.add_argument("--tasks", type=int, default=4)
    a = ap.parse_args()
    s = get_settings()
    gen, analyzer, _ = build_core(s, knowledge(s))
    req = GenerationRequest(
        grade=a.grade,
        subject_id=a.subject,
        subject_name=a.subject_name,
        topic=a.topic,
        level=LevelChoice(a.level),
        task_count=a.tasks,
    )

    async def progress(msg: str) -> None:
        print("  …", msg)

    check = await gen.check_request(req)
    for i in check.issues:
        print(f"[{i.severity.value}] {i.message_ru}")
    work = await gen.generate(req, progress)
    slug = re.sub(r"[^a-z0-9]+", "_", f"{a.subject}_{a.grade}_{a.level}").strip("_")
    out = ROOT / "examples" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "work.json").write_text(work.model_dump_json(indent=2), encoding="utf-8")
    ex = DocxExporter()
    fs, ft = ex.filenames(work)
    (out / fs).write_bytes(ex.student_docx(work))
    (out / ft).write_bytes(ex.teacher_docx(work))
    print(
        f"✓ {out}: {work.meta.llm_calls} вызовов, {work.meta.tokens_in}+{work.meta.tokens_out} ток., "
        f"${work.meta.cost_usd}, {work.meta.duration_s} c, починок {work.meta.repairs}"
    )
    for w in work.warnings:
        print(f"  [{w.severity.value}] {w.message_ru}")
    if a.subject == "math" and a.grade == 3:
        res = await analyzer.analyze(
            ErrorAnalysisRequest(
                grade=3,
                subject_id="math",
                subject_name="Математика",
                topic="Площадь и периметр прямоугольника",
                description="Дети перемножают стороны, когда нужно найти периметр, или складывают, когда нужно найти "
                "площадь. Формулы знают, но в задаче применяют механически.",
            )
        )
        (out / "analysis.json").write_text(res.model_dump_json(indent=2), encoding="utf-8")
        (out / "Анализ_ошибки.docx").write_bytes(ex.analysis_docx(res))
        print(
            f"✓ анализ ошибки: {len(res.hypotheses)} гипотез, приёмы {[t.technique_id for t in res.techniques]}"
        )
    (out / "summary.json").write_text(
        json.dumps(work.meta.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
