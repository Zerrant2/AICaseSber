"""Ядро «Сократа»: генерация работ, проверки кодом, анализ ошибок, разбор ответов."""

from __future__ import annotations

from socrat.config import Settings
from socrat.contracts import (
    ErrorAnalyzer,
    FeedbackRepository,
    KnowledgeBase,
    LLMClient,
    ResponseObserver,
    UsageRepository,
    WorkGenerator,
)

from .analysis import LLMErrorAnalyzer
from .generator import LLMWorkGenerator
from .guardrails import Guardrails
from .observation import RuleResponseObserver
from .techniques import TechniqueLibrary


def build_core(
    settings: Settings,
    knowledge: KnowledgeBase,
    feedback: FeedbackRepository | None = None,
    usage: UsageRepository | None = None,
    llm: LLMClient | None = None,
) -> tuple[WorkGenerator, ErrorAnalyzer, ResponseObserver]:
    """Точка сборки для socrat.app. Учёт расходов пишет бот по work.meta, поэтому usage здесь не нужен."""
    if llm is None:
        from socrat.llm import OpenAICompatibleClient

        llm = OpenAICompatibleClient(settings)
    techniques = TechniqueLibrary.load()
    generator = LLMWorkGenerator(settings, knowledge, llm, feedback=feedback, techniques=techniques)
    analyzer = LLMErrorAnalyzer(settings, knowledge, llm, techniques=techniques)
    observer = RuleResponseObserver()
    _ = usage
    return generator, analyzer, observer


__all__ = [
    "build_core",
    "Guardrails",
    "LLMErrorAnalyzer",
    "LLMWorkGenerator",
    "RuleResponseObserver",
    "TechniqueLibrary",
]
