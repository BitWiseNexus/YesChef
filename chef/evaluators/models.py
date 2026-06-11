"""Typed result models shared across evaluation tiers."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    """Outcome of evaluating a single intercepted command."""

    SAFE = "SAFE"
    UNSAFE = "UNSAFE"
    UNKNOWN = "UNKNOWN"  # The tier could not decide; escalate or fail safe.


class Tier(str, Enum):
    """Which layer of the engine produced the verdict."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    FAIL_SAFE = "fail_safe"


class Evaluation(BaseModel):
    """Final, auditable decision for one intercepted command."""

    command: str = Field(description="The exact command text that was evaluated.")
    verdict: Verdict = Field(description="SAFE approves the prompt; anything else denies it.")
    tier: Tier = Field(description="The tier that produced this verdict.")
    reason: str = Field(description="Human-readable justification for the verdict.")

    @property
    def approved(self) -> bool:
        """True only for an explicit SAFE verdict (deny-by-default)."""
        return self.verdict is Verdict.SAFE


class LLMVerdict(BaseModel):
    """Strict schema the LLM is forced to emit (Tier 2 response contract)."""

    decision: Verdict = Field(description='Either "SAFE" or "UNSAFE".')
    reason: str = Field(description="One-sentence justification.")
