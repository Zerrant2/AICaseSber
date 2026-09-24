import json

import httpx
from openai import AsyncOpenAI

from socrat.config import Settings
from socrat.llm import OpenAICompatibleClient, extract_json


def _ok(content: str, cost: float | None = None) -> httpx.Response:
    usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    if cost is not None:
        usage["cost"] = cost
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
            "usage": usage,
        },
    )


def make_client(handler, **overrides) -> OpenAICompatibleClient:
    s = Settings(_env_file=None, llm_base_url="https://openrouter.ai/api/v1", llm_api_key="k", **overrides)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    oc = AsyncOpenAI(base_url=s.llm_base_url, api_key="k", http_client=http, max_retries=0)
    return OpenAICompatibleClient(s, client=oc)


def test_extract_json_variants():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Вот ответ: {"a": [1, 2,], "b": "}"} спасибо') == {"a": [1, 2], "b": "}"}
    assert extract_json("нет json") is None


async def test_json_schema_and_cost_from_openrouter():
    seen = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append(body)
        return _ok('{"x": 5}', cost=0.0012)

    c = make_client(handler)
    r = await c.complete("sys", "user", json_schema={"type": "object"})
    assert r.data == {"x": 5}
    assert r.usage.cost_usd == 0.0012 and r.usage.tokens_in == 100
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["messages"][0]["role"] == "system"


async def test_fallbacks_system_and_json_mode():
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append(body)
        if body["messages"][0]["role"] == "system":
            return httpx.Response(400, json={"error": {"message": "Developer instruction is not enabled"}})
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        return _ok('{"ok": true}')

    c = make_client(handler, llm_price_in_per_1m=1.0, llm_price_out_per_1m=2.0)
    r = await c.complete("sys", "user", json_schema={"type": "object"})
    assert r.data == {"ok": True}
    assert calls[-1]["response_format"]["type"] == "json_object"
    assert calls[-1]["messages"][0]["role"] == "user" and "sys" in calls[-1]["messages"][0]["content"]
    assert r.usage.cost_usd == round(100 / 1e6 * 1 + 50 / 1e6 * 2, 6)
