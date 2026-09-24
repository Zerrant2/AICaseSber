"""CLI интерфейс модуля knowledge: python -m socrat.knowledge ..."""

from __future__ import annotations

import argparse
import asyncio

from socrat.config import get_settings
from socrat.contracts import Chunk
from socrat.knowledge.base import LocalKnowledgeBase
from socrat.knowledge.catalog import build_all_catalogs
from socrat.knowledge.chunker import chunk_pages_file, save_chunks
from socrat.knowledge.downloader import download_all
from socrat.knowledge.index import KnowledgeIndex
from socrat.knowledge.parser import parse_and_save_pdf


async def main() -> None:
    parser = argparse.ArgumentParser(description="Управление нормативной базой «Сократ»")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download
    subparsers.add_parser("download", help="Скачать документы по sources.yaml")

    # status
    subparsers.add_parser("status", help="Статус нормативной базы")

    # build
    subparsers.add_parser("build", help="Спарсить PDF и пересобрать каталог")

    # outcomes
    p_out = subparsers.add_parser("outcomes", help="Показать планируемые результаты")
    p_out.add_argument("--grade", type=int, required=True, help="Класс (1..11)")
    p_out.add_argument("--subject", type=str, required=True, help="ID предмета, напр. math")
    p_out.add_argument("--query", type=str, default=None, help="Поисковый запрос")
    p_out.add_argument("-k", type=int, default=20, help="Количество")

    # check-topic
    p_topic = subparsers.add_parser("check-topic", help="Проверить тему учителя на соответствие программе")
    p_topic.add_argument("--grade", type=int, required=True, help="Класс (1..11)")
    p_topic.add_argument("--subject", type=str, required=True, help="ID предмета")
    p_topic.add_argument("topic", type=str, help="Название темы")

    # search
    p_search = subparsers.add_parser("search", help="Поиск по нормативной базе")
    p_search.add_argument("query", type=str, help="Поисковый запрос")
    p_search.add_argument("--grade", type=int, default=None, help="Класс")
    p_search.add_argument("--subject", type=str, default=None, help="Предмет")

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "download":
        print("Скачивание источников...")

        async def _prog(msg: str) -> None:
            print(f" -> {msg}")

        report = await download_all(settings, progress=_prog)
        print(
            f"Готово. Скачано: {len(report.downloaded)}, Без изменений: {len(report.unchanged)}, Ошибок: {len(report.failed)}"
        )
        if report.failed:
            print("Ошибки:", report.failed)

    elif args.command == "build":
        print("1. Парсинг PDF...")
        raw_dir = settings.knowledge_dir / "raw"
        pages_dir = settings.knowledge_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for pdf in sorted(raw_dir.glob("*.pdf")):
            print(f"  -> Парсинг {pdf.name}...")
            parse_and_save_pdf(pdf, pdf.stem, pages_dir, sources_file=settings.sources_file)

        print("2. Нарезка на фрагменты (чанкинг)...")
        chunks_dir = settings.knowledge_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        subj_map = {
            "FRP-MATH-2025": "math",
            "FRP-RUSSIAN-NOO-2025": "russian",
            "FRP-LITERARY-READING-NOO-2025": "literary_reading",
            "FRP-WORLD-NOO-2025": "world",
        }
        all_chunks: list[Chunk] = []
        for p_file in sorted(pages_dir.glob("*.jsonl")):
            sid = p_file.stem
            subj = subj_map.get(sid)
            print(f"  -> Нарезка {sid}...")
            chunks = chunk_pages_file(p_file, sid, subj)
            save_chunks(chunks, chunks_dir / f"{sid}.jsonl")
            all_chunks.extend(chunks)

        print("3. Сборка каталогов результатов...")
        catalogs = build_all_catalogs(settings)
        total_outcomes = sum(len(v) for v in catalogs.values())
        print(f"  -> Каталоги собраны: {total_outcomes} результатов.")

        print("4. Построение поискового индекса BM25...")
        index_dir = settings.knowledge_dir / "index"
        index = KnowledgeIndex(all_chunks)
        index.save(index_dir)
        print(f"  -> Индекс сохранён: {len(all_chunks)} фрагментов.")

    elif args.command == "status":
        kb = LocalKnowledgeBase(settings)
        st = kb.status()
        print("Статус KnowledgeBase:")
        print(f"  Ready: {st.ready}")
        print(f"  Документов: {len(st.documents)}")
        print(f"  Результатов в каталоге: {st.outcomes_total}")
        if st.problems:
            print("  Проблемы:", st.problems)

    elif args.command == "outcomes":
        kb = LocalKnowledgeBase(settings)
        outs = kb.get_outcomes(args.grade, args.subject, query=args.query, k=args.k)
        print(f"Найдено {len(outs)} результатов для {args.subject} ({args.grade} кл.):")
        for o in outs:
            print(f"[{o.outcome_id}] ({o.type.value}, стр. {o.page}): {o.text}")

    elif args.command == "check-topic":
        kb = LocalKnowledgeBase(settings)
        res = kb.check_topic(args.grade, args.subject, args.topic)
        print(f"Тема: {args.topic}")
        print(f"В программе: {res.in_program} (confidence: {res.confidence:.2f})")
        if res.matched_topics:
            print("Найденные темы:", res.matched_topics)
        if res.suggestions:
            print("Предложения:", res.suggestions)

    elif args.command == "search":
        kb = LocalKnowledgeBase(settings)
        hits = kb.search(args.query, grade=args.grade, subject_id=args.subject)
        print(f"Найдено {len(hits)} совпадений по запросу '{args.query}':")
        for h in hits:
            print(
                f"[{h.chunk.source_id}, стр. {h.chunk.page}] (score: {h.score:.2f}): {h.chunk.text[:100]}..."
            )


if __name__ == "__main__":
    asyncio.run(main())
