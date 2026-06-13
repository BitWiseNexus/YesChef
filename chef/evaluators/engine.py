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
        task_context: str = "",
    ) -> None:
        self._settings = settings
        self._deterministic = deterministic or DeterministicEvaluator()
        self._llm = llm
        if self._llm is None and settings.llm_enabled and settings.llm_api_key:
            self._llm = LLMEvaluator(settings)
        # The user's stated task, passed to Tier 2 as a hint. Falls back to
        # the value baked into settings (CHEF_TASK_CONTEXT) when not given.
        self._task_context = task_context or settings.task_context
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create the engine's private, long-lived event loop.

        One loop must be reused across all sync calls: the LLM client's
        connection pool is bound to the loop it first ran on, and resuming
        it from a different loop raises ``RuntimeError: Event loop is
        closed`` (which ``asyncio.run`` per call would guarantee).
        """
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def evaluate(self, command: str) -> Evaluation:
        """Synchronous facade for the pexpect event loop (which is blocking)."""
        return self._get_loop().run_until_complete(self.evaluate_async(command))

    async def evaluate_async(self, command: str) -> Evaluation:
        """Evaluate ``command`` through both tiers and audit the result."""
        result = self._deterministic.evaluate(command)

        if result.verdict is Verdict.UNKNOWN and self._llm is not None:
            logger.info("Tier 1 inconclusive; escalating to LLM: %r", command)
            result = await self._llm.evaluate(command, self._task_context)

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

    def close(self) -> None:
        """Synchronous teardown: close the LLM client on its own loop."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.run_until_complete(self.aclose())
            self._loop.close()
        else:
            asyncio.run(self.aclose())
        self._loop = None
