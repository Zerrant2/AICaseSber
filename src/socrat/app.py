"""Composition root for real and fixture-mode services."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from socrat.config import Settings
from socrat.contracts import Services
from socrat.export.fallback import FixtureExporter
from socrat.storage import (
    SqlFeedbackRepository,
    SqlTeacherRepository,
    SqlUsageRepository,
    SqlWorkRepository,
    create_engine,
    init_db,
    session_factory,
)
from socrat.testing.fakes import FakeErrorAnalyzer, FakeKnowledgeBase, FakeResponseObserver, FakeWorkGenerator

logger = logging.getLogger(__name__)


async def build_services(settings: Settings) -> Services:
    engine = create_engine(settings)
    try:
        await init_db(engine)
        sessions = session_factory(engine)
        teachers = SqlTeacherRepository(sessions, settings)
        works = SqlWorkRepository(sessions)
        feedback = SqlFeedbackRepository(sessions)
        usage = SqlUsageRepository(sessions)

        if settings.use_fakes:
            knowledge = FakeKnowledgeBase()
            generator = FakeWorkGenerator()
            analyzer = FakeErrorAnalyzer()
            observer = FakeResponseObserver()
        else:
            try:
                from socrat.knowledge import LocalKnowledgeBase
            except ImportError:
                logger.warning("Knowledge module is unavailable; using fixture knowledge")
                knowledge = FakeKnowledgeBase()
            else:
                knowledge = LocalKnowledgeBase(settings)
            try:
                from socrat.core import build_core
            except ImportError:
                logger.warning("Core module is unavailable; using fixture generation and analysis")
                generator = FakeWorkGenerator()
                analyzer = FakeErrorAnalyzer()
                observer = FakeResponseObserver()
            else:
                generator, analyzer, observer = build_core(settings, knowledge, feedback, usage)

        try:
            from socrat.export.docx_exporter import DocxExporter
        except ImportError:
            logger.warning("Full Word exporter is unavailable; using fixture exporter")
            exporter = FixtureExporter()
        else:
            exporter = DocxExporter()

        services = Services(
            knowledge=knowledge,
            generator=generator,
            analyzer=analyzer,
            observer=observer,
            exporter=exporter,
            teachers=teachers,
            works=works,
            feedback=feedback,
            usage=usage,
        )
        services._storage_engine = engine
        return services
    except Exception:
        await engine.dispose()
        raise


async def close_services(services: Services) -> None:
    engine: AsyncEngine | None = getattr(services, "_storage_engine", None)
    if engine is not None:
        await engine.dispose()
        delattr(services, "_storage_engine")
