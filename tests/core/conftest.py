"""Фикстуры для тестов ядра."""

from __future__ import annotations

import pytest

from socrat.config import Settings
from socrat.core import build_core
from socrat.testing.fakes import FakeKnowledgeBase
from socrat.testing.scripted import ScriptedLLM, variant_draft  # noqa: F401


@pytest.fixture
def settings():
    return Settings(_env_file=None, llm_max_repairs=2)


@pytest.fixture
def knowledge():
    return FakeKnowledgeBase()


@pytest.fixture
def core_factory(settings, knowledge):
    def make(llm):
        return build_core(settings, knowledge, llm=llm)

    return make
