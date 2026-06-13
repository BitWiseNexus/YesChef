"""Tier 1: deterministic regex-based whitelist / blacklist evaluation.

The blacklist is always checked first — a command matching both lists is
denied. Commands matching neither list yield :attr:`Verdict.UNKNOWN`, which
signals the engine to escalate to Tier 2.
"""

from __future__ import annotations

import re
from typing import List, Optional, Pattern, Sequence

from chef.evaluators.models import Evaluation, Tier, Verdict

#: Read-only / harmless commands that may be approved without escalation.
#: Patterns are matched with ``re.search`` against the *normalised* command
#: (whitespace collapsed), anchored at the start where appropriate.
DEFAULT_WHITELIST: Sequence[str] = (
    r"^cat\s",
    r"^head(\s|$)",
    r"^tail(\s|$)",
    r"^less(\s|$)",
    r"^ls(\s|$)",
    r"^pwd$",
    r"^echo\s(?!.*[>|])",  # echo is safe unless it redirects or pipes
    r"^wc(\s|$)",
    r"^which\s",
    r"^file\s",
    r"^stat\s",
    r"^du(\s|$)",
    r"^df(\s|$)",
    r"^env$",
    r"^date(\s|$)",
    r"^whoami$",
    r"^uname(\s|$)",
    r"^grep\s(?!.*[>|])",
    r"^rg\s(?!.*[>|])",
    r"^find\s(?!.*(-delete|-exec))",
    r"^git\s+(status|diff|log|show|branch|remote|stash\s+list|blame)(\s|$)",
    r"^npm\s+(ls|list|view|outdated)(\s|$)",
    r"^pip\s+(list|show|freeze)(\s|$)",
    r"^python(3)?\s+--version$",
    r"^node\s+--version$",
    # Gemini CLI tool dialogs: file reads/writes are confined to the
    # sandboxed /workspace mount, so they are safe to auto-approve.
    r"^writefile\s+writing\s+to\s",
    r"^readfile\b",
    r"^readfolder\b",
    r"^findfiles\b",
    r"^searchtext\b",
)

#: Destructive or privilege-escalating commands that are always denied.
DEFAULT_BLACKLIST: Sequence[str] = (
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+",  # rm -rf and friends
    r"\brm\s+.*\*",                            # wildcard deletes
    r"\bmkfs(\.|\s)",
    r"\bdd\s+.*of=",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bsudo\b",
    r"\bsu\s+-?\b",
    r"\bchmod\s+([0-7]*777|-R)\b",
    r"\bchown\s+-R\b",
    r":\(\)\s*\{.*\};\s*:",                    # fork bomb
    r"\bcurl\b.*\|\s*(ba)?sh",                 # curl | sh
    r"\bwget\b.*\|\s*(ba)?sh",
    r">\s*/dev/sd[a-z]",
    r"\bgit\s+push\s+.*(--force|-f)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bDROP\s+(TABLE|DATABASE)\b",
    r"\btruncate\s+-s\s*0\b",
    r"\bkill\s+-9\s+1\b",
    r"\b(iptables|nft|ufw)\b",
    r"\bcrontab\b",
    r"\bbase64\s+(-d|--decode).*\|\s*(ba)?sh",  # obfuscated execution
)


class DeterministicEvaluator:
    """Regex lookup engine for instantly classifiable commands.

    Args:
        whitelist: Override/extend patterns for auto-approved commands.
        blacklist: Override/extend patterns for auto-denied commands.
        extend_defaults: When True (default), the provided patterns are added
            on top of the built-in lists instead of replacing them.
    """

    def __init__(
        self,
        whitelist: Optional[Sequence[str]] = None,
        blacklist: Optional[Sequence[str]] = None,
        extend_defaults: bool = True,
    ) -> None:
        wl: List[str] = list(DEFAULT_WHITELIST) if extend_defaults or whitelist is None else []
        bl: List[str] = list(DEFAULT_BLACKLIST) if extend_defaults or blacklist is None else []
        wl.extend(whitelist or [])
        bl.extend(blacklist or [])
        self._whitelist: List[Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in wl]
        self._blacklist: List[Pattern[str]] = [re.compile(p, re.IGNORECASE) for p in bl]

    @staticmethod
    def _normalise(command: str) -> str:
        """Collapse whitespace so regexes see a canonical single-line form."""
        return re.sub(r"\s+", " ", command).strip()

    def evaluate(self, command: str) -> Evaluation:
        """Classify ``command`` as SAFE, UNSAFE, or UNKNOWN.

        Blacklist matches win over whitelist matches. Compound shell commands
        (joined with ``&&``, ``;`` or ``|``) are only whitelisted if *every*
        segment independently matches the whitelist.
        """
        normalised = self._normalise(command)

        for pattern in self._blacklist:
            if pattern.search(normalised):
                return Evaluation(
                    command=command,
                    verdict=Verdict.UNSAFE,
                    tier=Tier.DETERMINISTIC,
                    reason=f"Matched blacklist pattern: {pattern.pattern!r}",
                )

        segments = re.split(r"\s*(?:&&|\|\||;)\s*", normalised)
        if segments and all(self._matches_whitelist(seg) for seg in segments if seg):
            return Evaluation(
                command=command,
                verdict=Verdict.SAFE,
                tier=Tier.DETERMINISTIC,
                reason="All command segments matched the read-only whitelist.",
            )

        return Evaluation(
            command=command,
            verdict=Verdict.UNKNOWN,
            tier=Tier.DETERMINISTIC,
            reason="No deterministic rule matched; escalation required.",
        )

    def _matches_whitelist(self, segment: str) -> bool:
        return any(p.search(segment) for p in self._whitelist)
