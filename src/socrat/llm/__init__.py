"""Модуль llm. ВЛАДЕЛЕЦ: Claude. OpenAI-совместимый клиент (OpenRouter / Ollama / vLLM)."""

from .client import LLMError, OpenAICompatibleClient, extract_json

__all__ = ["LLMError", "OpenAICompatibleClient", "extract_json"]
