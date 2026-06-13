"""Unit tests for the Tier 2 LLM evaluator (httpx.MockTransport — no network)."""

from __future__ import annotations

import json
from typing import Callable, List

import httpx
import pytest

from chef.evaluators.llm import LLMEvaluator
from chef.evaluators.models import Tier, Verdict


def chat_completion(content: str, status_code: int = 200) -> httpx.Response:
    """Build a minimal OpenAI-compatible /chat/completions response."""
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"role": "assistant", "content": content}}]},
    )


def make_evaluator(make_settings: Callable, handler: Callable) -> LLMEvaluator:
    settings = make_settings(llm_enabled=True, llm_api_key="test-key", llm_max_retries=2)
    client = httpx.AsyncClient(
        base_url=settings.llm_base_url,
        transport=httpx.MockTransport(handler),
    )
    return LLMEvaluator(settings, client=client)


async def test_safe_verdict_is_parsed(make_settings: Callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"  # schema forcing
        return chat_completion(json.dumps({"decision": "SAFE", "reason": "read-only"}))

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("make build")
    assert result.verdict is Verdict.SAFE
    assert result.tier is Tier.LLM
    assert result.reason == "read-only"


async def test_unsafe_verdict_is_parsed(make_settings: Callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_completion(json.dumps({"decision": "UNSAFE", "reason": "deletes data"}))

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("rm important.db")
    assert result.verdict is Verdict.UNSAFE


async def test_task_context_is_sent_to_model(make_settings: Callable) -> None:
    """The user's task must reach the judge in the user message when provided."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user"] = json.loads(request.content)["messages"][1]["content"]
        return chat_completion(json.dumps({"decision": "SAFE", "reason": "matches task"}))

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("rm NOTES.md", task_context="delete NOTES.md")
    assert "delete NOTES.md" in captured["user"]
    assert "rm NOTES.md" in captured["user"]
    assert result.verdict is Verdict.SAFE


async def test_no_context_omits_task_line(make_settings: Callable) -> None:
    """Without a task, the user message carries only the command."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user"] = json.loads(request.content)["messages"][1]["content"]
        return chat_completion(json.dumps({"decision": "UNSAFE", "reason": "no"}))

    evaluator = make_evaluator(make_settings, handler)
    await evaluator.evaluate("rm NOTES.md")
    assert "User's task" not in captured["user"]


async def test_retries_transient_errors_then_succeeds(make_settings: Callable) -> None:
    calls: List[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="overloaded")
        return chat_completion(json.dumps({"decision": "SAFE", "reason": "ok"}))

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("pytest -q")
    assert len(calls) == 3
    assert result.verdict is Verdict.SAFE


async def test_exhausted_retries_return_unknown(make_settings: Callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout")

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("make deploy")
    assert result.verdict is Verdict.UNKNOWN  # engine fail-safe will deny


async def test_non_retryable_error_bails_immediately(make_settings: Callable) -> None:
    calls: List[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="invalid api key")

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("ls")
    assert len(calls) == 1  # no pointless retries on auth failure
    assert result.verdict is Verdict.UNKNOWN


async def test_malformed_json_payload_is_not_trusted(make_settings: Callable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return chat_completion("Sure! That command looks fine to me.")  # prose, not JSON

    evaluator = make_evaluator(make_settings, handler)
    result = await evaluator.evaluate("make install")
    assert result.verdict is Verdict.UNKNOWN
