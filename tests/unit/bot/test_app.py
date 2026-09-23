"""The composition root uses fixtures for peers and SQL for local persistence."""

from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document

from socrat.app import build_services, close_services
from socrat.config import get_settings
from socrat.contracts import Exporter, GenerationRequest, LevelChoice, Role, Services
from socrat.testing.fakes import FakeErrorAnalyzer, FakeKnowledgeBase, FakeWorkGenerator


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET", "test-only-app-secret")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("USE_FAKES", "true")
    monkeypatch.setenv("LLM_PRICE_IN_PER_1M", "0")
    monkeypatch.setenv("LLM_PRICE_OUT_PER_1M", "0")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


async def test_build_services_uses_fakes_and_real_storage(settings) -> None:
    services = await build_services(settings)
    try:
        assert isinstance(services, Services)
        assert isinstance(services.knowledge, FakeKnowledgeBase)
        assert isinstance(services.generator, FakeWorkGenerator)
        assert isinstance(services.analyzer, FakeErrorAnalyzer)
        assert isinstance(services.exporter, Exporter)

        credentials = await services.teachers.create("Учитель", Role.TEACHER)
        assert await services.teachers.authenticate(credentials.plain_password) == credentials.account

        work = await services.generator.generate(
            GenerationRequest(
                grade=3,
                subject_id="math",
                subject_name="Математика",
                topic="Умножение и деление",
                level=LevelChoice.ALL,
                task_count=2,
            )
        )
        student_docx = services.exporter.student_docx(work)
        document = Document(BytesIO(student_docx))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "Ответ:" not in text
        assert "Вариант А" in text
    finally:
        await close_services(services)
