"""Tests for the two-tier orchestration and fail-safe behaviour."""

from __future__ import annotations

import json
from typing import Callable

import httpx

from chef.core.logger import setup_logging
from chef.evaluators.engine import EvaluationEngine
from chef.evaluators.llm import LLMEvaluator
from chef.evaluators.models import Tier, Verdict


def make_llm(make_settings: Callable, decision: str) -> LLMEvaluator:
    """An LLM evaluator whose API always answers with ``decision``."""
    settings = make_settings(llm_enabled=True, llm_api_key="test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps({"decision": decision, "reason": f"llm says {decision}"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    client = httpx.AsyncClient(
        base_url=settings.llm_base_url, transport=httpx.MockTransport(handler)
    )
    return LLMEvaluator(settings, client=client)


async def test_tier1_safe_skips_llm(make_settings: Callable) -> None:
    """Whitelisted commands must never reach Tier 2."""
    settings = make_settings()
    llm = make_llm(make_settings, "UNSAFE")  # would contradict Tier 1 if consulted
    engine = EvaluationEngine(settings, llm=llm)

    result = await engine.evaluate_async("git diff")
    assert result.verdict is Verdict.SAFE
    assert result.tier is Tier.DETERMINISTIC


async def test_unknown_escalates_to_llm(make_settings: Callable) -> None:
    settings = make_settings()
    engine = EvaluationEngine(settings, llm=make_llm(make_settings, "SAFE"))

    result = await engine.evaluate_async("make build")
    assert result.verdict is Verdict.SAFE
    assert result.tier is Tier.LLM


async def test_fail_safe_denies_without_llm(make_settings: Callable) -> None:
    """With Tier 2 disabled, unknown commands are denied — never approved."""
    engine = EvaluationEngine(make_settings(llm_enabled=False))

    result = await engine.evaluate_async("make build")
    assert result.verdict is Verdict.UNSAFE
    assert result.tier is Tier.FAIL_SAFE
    assert not result.approved


def test_sync_facade(make_settings: Callable) -> None:
    """`evaluate` (sync) must work from blocking code like the pexpect loop."""
    engine = EvaluationEngine(make_settings())
    assert engine.evaluate("cat x.txt").approved


async def test_decisions_are_audited_as_json(make_settings: Callable, tmp_path) -> None:
    audit_path = tmp_path / "audit.log"
    settings = make_settings(audit_log_path=str(audit_path))
    setup_logging("INFO", str(audit_path))

    engine = EvaluationEngine(settings)
    await engine.evaluate_async("git status")
    await engine.evaluate_async("rm -rf /")

    records = [json.loads(line) for line in audit_path.read_text().splitlines()]
    decisions = [r for r in records if r.get("event") == "permission_decision"]
    assert len(decisions) == 2

    first, second = decisions
    assert first["command"] == "git status"
    assert first["verdict"] == "SAFE"
    assert first["tier"] == "deterministic"
    assert first["approved"] is True
    assert "timestamp" in first

    assert second["command"] == "rm -rf /"
    assert second["verdict"] == "UNSAFE"
    assert second["approved"] is False
