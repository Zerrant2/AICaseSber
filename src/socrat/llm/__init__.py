"""Модуль llm."""

from .client import LLMError, OpenAICompatibleClient, extract_json

__all__ = ["LLMError", "OpenAICompatibleClient", "extract_json"]
