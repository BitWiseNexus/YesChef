"""Two-tier evaluation orchestrator with a deny-by-default fail-safe."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from chef.config import ChefSettings
from chef.core.logger import audit
from chef.evaluators.deterministic import DeterministicEvaluator
from chef.evaluators.llm import LLMEvaluator
from chef.evaluators.models import Evaluation, Tier, Verdict

logger = logging.getLogger("chef.evaluators.engine")


class EvaluationEngine:
    """Routes an intercepted command through the evaluation tiers.

    Order of operations:
        1. **Tier 1 (deterministic)** — instant regex whitelist/blacklist.
        2. **Tier 2 (LLM)** — heuristic fallback for UNKNOWN commands,
           skipped when disabled via ``CHEF_LLM_ENABLED=false``.
        3. **Fail-safe** — anything still UNKNOWN is denied.

    Every decision (whatever the tier) is written to the JSON audit log.
    """

    def __init__(
        self,
        settings: ChefSettings,
        deterministic: Optional[DeterministicEvaluator] = None,
        llm: Optional[LLMEvaluator] = None,
    ) -> None:
        self._settings = settings
        self._deterministic = deterministic or DeterministicEvaluator()
        self._llm = llm
        if self._llm is None and settings.llm_enabled and settings.llm_api_key:
            self._llm = LLMEvaluator(settings)

    def evaluate(self, command: str) -> Evaluation:
        """Synchronous facade for the pexpect event loop (which is blocking)."""
        return asyncio.run(self.evaluate_async(command))

    async def evaluate_async(self, command: str) -> Evaluation:
        """Evaluate ``command`` through both tiers and audit the result."""
        result = self._deterministic.evaluate(command)

        if result.verdict is Verdict.UNKNOWN and self._llm is not None:
            logger.info("Tier 1 inconclusive; escalating to LLM: %r", command)
            result = await self._llm.evaluate(command)

        if result.verdict is Verdict.UNKNOWN:
            result = Evaluation(
                command=command,
                verdict=Verdict.UNSAFE,
                tier=Tier.FAIL_SAFE,
                reason=(
                    "No tier produced a confident verdict; "
                    "denying by default. (" + result.reason + ")"
                ),
            )

        audit(result)
        return result

    async def aclose(self) -> None:
        """Dispose of tier resources (LLM connection pool)."""
        if self._llm is not None:
            await self._llm.aclose()
