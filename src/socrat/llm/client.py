"""OpenAI-совместимый LLM-клиент: OpenRouter, Ollama, vLLM, любой /v1/chat/completions.

Особенности:
  * JSON-режимы: json_schema → json_object → prompt (схема в тексте + извлечение JSON).
    В режиме `auto` клиент сам откатывается на более простой режим, если провайдер/модель
    не поддерживает structured outputs, и запоминает это для модели.
  * Некоторые модели (напр. Gemma у части провайдеров) не принимают system-роль —
    клиент автоматически переносит system в user и запоминает это.
  * Повторы на 429/5xx/таймаутах, учёт токенов и стоимости.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import openai
from openai import AsyncOpenAI
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from socrat.config import Settings
from socrat.contracts import LLMResponse, LLMUsage

logger = logging.getLogger(__name__)

_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.InternalServerError,
)

JSON_ONLY_INSTRUCTION = "\n\nОТВЕТ: верни ТОЛЬКО один JSON-объект, без пояснений, без markdown-блоков ```."


class LLMError(Exception):
    """Ошибка обращения к LLM, понятная для логов. Для учителя показывается общий текст."""


def extract_json(text: str) -> Any | None:
    """Достаёт первый JSON-объект/массив из текста модели. Терпит ```json-обёртки,
    текст до/после и висячие запятые. Возвращает None, если распарсить не удалось."""
    if not text:
        return None
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (t.find("{"), t.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    opener = t[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                chunk = t[start : i + 1]
                for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
                return None
    return None


def schema_hint(schema: dict) -> str:
    """Компактное описание JSON-схемы для режима prompt (меньше токенов, чем полная схема)."""
    return json.dumps(schema, ensure_ascii=False, separators=(",", ":"))


class OpenAICompatibleClient:
    """Реализует socrat.contracts.LLMClient."""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self.settings = settings
        self.model = settings.llm_model
        self._is_openrouter = "openrouter.ai" in settings.llm_base_url
        headers = {}
        if self._is_openrouter:
            headers = {"HTTP-Referer": settings.openrouter_app_url, "X-Title": settings.openrouter_app_name}
        self._client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() or "not-set",
            timeout=settings.llm_timeout_s,
            default_headers=headers or None,
            max_retries=0,  # повторы делаем сами (tenacity)
        )
        mode = settings.llm_json_mode.lower()
        self._json_mode = mode if mode in {"json_schema", "json_object", "prompt"} else "json_schema"
        self._auto = mode == "auto" or mode not in {"json_schema", "json_object", "prompt"}
        self._merge_system = False
        self._emb_client: AsyncOpenAI | None = None

    # ------------------------------------------------------------------ public

    async def complete(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        temperature = self.settings.llm_temperature if temperature is None else temperature
        max_tokens = max_tokens or self.settings.llm_max_tokens
        for _ in range(4):  # не больше 4 откатов режимов
            mode = self._json_mode if json_schema is not None else "text"
            try:
                resp = await self._call(system, user, mode, json_schema, temperature, max_tokens)
            except openai.BadRequestError as e:
                if self._handle_bad_request(e, mode):
                    continue
                raise LLMError(f"LLM отклонила запрос: {e}") from e
            except _RETRYABLE as e:
                raise LLMError(f"LLM недоступна: {e.__class__.__name__}") from e
            except openai.APIStatusError as e:
                raise LLMError(f"LLM вернула ошибку {e.status_code}") from e
            return self._to_response(resp, json_schema is not None)
        raise LLMError("Не удалось подобрать режим запроса к модели")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        s = self.settings
        if not (s.embeddings_base_url and s.embeddings_model):
            raise NotImplementedError("Эмбеддинги не настроены (EMBEDDINGS_BASE_URL / EMBEDDINGS_MODEL)")
        if self._emb_client is None:
            self._emb_client = AsyncOpenAI(
                base_url=s.embeddings_base_url,
                api_key=s.embeddings_api_key.get_secret_value() or "not-set",
                timeout=s.llm_timeout_s,
            )
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            r = await self._emb_client.embeddings.create(model=s.embeddings_model, input=texts[i : i + 64])
            out.extend(d.embedding for d in r.data)
        return out

    # ----------------------------------------------------------------- internals

    def _messages(self, system: str, user: str) -> list[dict]:
        if self._merge_system:
            return [{"role": "user", "content": f"{system}\n\n---\n\n{user}"}]
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    async def _call(self, system, user, mode, schema, temperature, max_tokens):
        kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": False, "schema": schema},
            }
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
            user = user + "\n\nJSON-схема ответа:\n" + schema_hint(schema) + JSON_ONLY_INSTRUCTION
        elif mode == "prompt":
            user = user + "\n\nJSON-схема ответа:\n" + schema_hint(schema) + JSON_ONLY_INSTRUCTION
        if self._is_openrouter:
            extra: dict[str, Any] = {"usage": {"include": True}}
            effort = (self.settings.llm_reasoning or "").strip().lower()
            if effort in {"off", "none", "false", "0"}:
                extra["reasoning"] = {"enabled": False}
            elif effort in {"low", "medium", "high"}:
                extra["reasoning"] = {"effort": effort}
            kwargs["extra_body"] = extra
        kwargs["messages"] = self._messages(system, user)

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1.5, min=2, max=20),
            reraise=True,
        ):
            with attempt:
                return await self._client.chat.completions.create(**kwargs)
        raise AssertionError("unreachable")

    def _handle_bad_request(self, e: openai.BadRequestError, mode: str) -> bool:
        """True — поменяли режим, можно повторить."""
        msg = str(e).lower()
        if not self._merge_system and ("system" in msg or "developer instruction" in msg):
            logger.warning("Model %s rejects system role; merging system into user", self.model)
            self._merge_system = True
            return True
        if self._auto and mode == "json_schema":
            logger.warning("Model %s: json_schema unsupported, falling back to json_object", self.model)
            self._json_mode = "json_object"
            return True
        if self._auto and mode == "json_object":
            logger.warning("Model %s: json_object unsupported, falling back to prompt", self.model)
            self._json_mode = "prompt"
            return True
        return False

    def _to_response(self, resp: Any, want_json: bool) -> LLMResponse:
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""
        u = resp.usage
        tin = getattr(u, "prompt_tokens", 0) or 0
        tout = getattr(u, "completion_tokens", 0) or 0
        cost = None
        extra = getattr(u, "model_extra", None) or {}
        if isinstance(extra, dict) and extra.get("cost") is not None:
            try:
                cost = float(extra["cost"])
            except (TypeError, ValueError):
                cost = None
        if cost is None:
            cost = self.estimate_cost(tin, tout)
        details = getattr(u, "completion_tokens_details", None)
        reasoning = getattr(details, "reasoning_tokens", None) if details is not None else None
        finish = getattr(choice, "finish_reason", None) if choice else None
        logger.info(
            "LLM %s: in=%s out=%s reasoning=%s finish=%s cost=%s",
            self.model,
            tin,
            tout,
            reasoning,
            finish,
            cost,
        )
        data = extract_json(text) if want_json else None
        if want_json and data is None:
            logger.warning(
                "LLM returned non-JSON answer (%d chars, finish=%s%s)",
                len(text),
                finish,
                " — ответ обрезан, увеличьте LLM_MAX_TOKENS" if finish == "length" else "",
            )
        return LLMResponse(
            text=text,
            data=data,
            usage=LLMUsage(
                model=getattr(resp, "model", None) or self.model,
                tokens_in=tin,
                tokens_out=tout,
                cost_usd=cost,
            ),
        )

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float | None:
        pin, pout = self.settings.llm_price_in_per_1m, self.settings.llm_price_out_per_1m
        if pin is None and pout is None:
            return None
        return round(tokens_in / 1e6 * (pin or 0) + tokens_out / 1e6 * (pout or 0), 6)
