"""Pexpect-based process wrapper: spawn, observe, intercept, respond.

The wrapper runs the child CLI inside a pseudo-terminal, mirrors its output
to the user's terminal in real time, and watches for permission prompts.
When a prompt fires it extracts the command being requested from the
preceding output, asks the :class:`~chef.evaluators.engine.EvaluationEngine`
for a verdict, and answers the prompt on the child's stdin.

Failure handling:
    * ``pexpect.TIMEOUT`` — tolerated while the child is alive (it may simply
      be thinking); after ``max_idle_timeouts`` consecutive silent intervals
      the session is terminated gracefully.
    * ``pexpect.EOF`` — the child exited; its exit status is propagated.
    * Any crash path runs through :meth:`SessionResult` so callers always get
      a structured outcome instead of an unhandled exception.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Pattern, Sequence

import pexpect

from chef.config import ChefSettings
from chef.evaluators.engine import EvaluationEngine
from chef.evaluators.models import Evaluation

logger = logging.getLogger("chef.core.wrapper")

#: Regexes tried (in order) against the output surrounding a prompt to recover
#: the command awaiting approval. Each must expose one capture group.
COMMAND_EXTRACTION_PATTERNS: Sequence[str] = (
    r"Allow execution of:?\s*['\"]([^'\"]+)['\"]",   # Gemini CLI shell prompt
    r"(WriteFile\s+Writing to\s+\S+)",               # Gemini CLI write-file dialog
    r"About to (?:run|execute):?\s*`([^`]+)`",       # mock CLI / generic tools
    r"(?:Run|Execute)\s+`([^`]+)`",                  # "Run `git push`?"
    r"Bash command[\s\S]*?\n\s{2,}(\S[^\n]*)",       # Claude Code tool panel
    r"(?:Shell|shell)\s*\(([^)]+)\)",                # Codex/Gemini tool-call line
    r"\$\s+(\S[^\n]*)\s*$",                          # trailing "$ cmd" line
)

#: ANSI escape sequences (CSI, OSC, DCS/SOS/PM/APC, and bare ESC+final) that
#: TUI children emit; stripped before prompt-context analysis.
ANSI_ESCAPE_RE: Pattern[str] = re.compile(
    r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]"          # CSI ... final byte
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"        # OSC ... BEL or ST
    r"|[PX^_][^\x1b]*\x1b\\"                 # DCS / SOS / PM / APC ... ST
    r"|[@-Z\\-_])"                           # two-byte escapes
)

#: Box-drawing, block, geometric and braille-spinner characters that TUI
#: frames are drawn with (escaped: box drawing U+2500-25FF, braille U+2800-28FF,
#: check marks and misc tool glyphs).
TUI_DECOR_RE: Pattern[str] = re.compile(r"[─-◿⠀-⣿✓⊶]")


class SessionStatus(str, Enum):
    """How the wrapped session ended."""

    COMPLETED = "completed"        # child exited on its own (EOF)
    IDLE_TIMEOUT = "idle_timeout"  # child went silent and was terminated
    CRASHED = "crashed"            # spawn failure or unexpected error


@dataclass
class SessionResult:
    """Structured outcome of one wrapped session."""

    status: SessionStatus
    exit_code: int
    prompts_handled: int = 0
    decisions: List[Evaluation] = field(default_factory=list)


class ProcessWrapper:
    """Wraps an interactive CLI and answers its permission prompts.

    Args:
        settings: Runtime configuration (command, prompt regex, timeouts).
        engine: The two-tier evaluation engine consulted for each prompt.
        dry_run: When True, decisions are evaluated and audited but every
            prompt is *denied*, making rule changes safe to rehearse.
    """

    def __init__(
        self,
        settings: ChefSettings,
        engine: EvaluationEngine,
        dry_run: bool = False,
    ) -> None:
        self._settings = settings
        self._engine = engine
        self._dry_run = dry_run
        self._prompt_re: Pattern[str] = re.compile(settings.prompt_pattern)
        # User-supplied patterns take precedence over the built-ins so a
        # tool-specific format can override the generic fallbacks.
        self._extractors: List[Pattern[str]] = [
            re.compile(p, re.MULTILINE)
            for p in (*settings.extra_command_patterns, *COMMAND_EXTRACTION_PATTERNS)
        ]
        # (command, monotonic timestamp) of the last answered prompt, used to
        # ignore TUI redraws of a dialog that was already answered.
        self._last_prompt: Optional[tuple[str, float]] = None

    def run(self, command: Optional[str] = None, args: Optional[Sequence[str]] = None) -> SessionResult:
        """Spawn the child and pump its output until it exits or stalls.

        Args:
            command: Executable to spawn; defaults to ``settings.child_command``.
            args: Arguments for the executable; defaults to ``settings.child_args``.

        Returns:
            A :class:`SessionResult` describing how the session ended.
        """
        cmd = command or self._settings.child_command
        argv = list(args if args is not None else self._settings.child_args)
        result = SessionResult(status=SessionStatus.CRASHED, exit_code=1)

        logger.info("Spawning child: %s %s", cmd, " ".join(argv))
        try:
            child = pexpect.spawn(
                cmd,
                argv,
                cwd=self._settings.child_cwd,
                encoding="utf-8",
                codec_errors="replace",
                timeout=self._settings.expect_timeout,
                dimensions=(40, 120),
            )
        except pexpect.ExceptionPexpect as exc:
            logger.error("Failed to spawn %r: %s", cmd, exc)
            return result

        child.logfile_read = sys.stdout  # mirror child output live

        try:
            result = self._pump(child)
        except Exception:  # noqa: BLE001 - last-resort guard, always audited
            logger.exception("Unexpected error while pumping child output")
            result.status = SessionStatus.CRASHED
        finally:
            self._shutdown(child)
            sys.stdout.flush()
        return result

    # ------------------------------------------------------------------ #
    # internals                                                          #
    # ------------------------------------------------------------------ #

    def _pump(self, child: pexpect.spawn) -> SessionResult:
        """Main expect loop: dispatch prompts, EOF, and idle timeouts."""
        result = SessionResult(status=SessionStatus.COMPLETED, exit_code=0)
        idle_timeouts = 0

        while True:
            try:
                index = child.expect([self._prompt_re, pexpect.EOF, pexpect.TIMEOUT])
            except pexpect.ExceptionPexpect as exc:
                logger.error("pexpect failure: %s", exc)
                result.status = SessionStatus.CRASHED
                result.exit_code = 1
                return result

            if index == 0:  # permission prompt detected
                idle_timeouts = 0
                evaluation = self._handle_prompt(child)
                if evaluation is not None:  # None = ignored TUI redraw
                    result.prompts_handled += 1
                    result.decisions.append(evaluation)

            elif index == 1:  # EOF — child exited
                child.close()
                result.status = SessionStatus.COMPLETED
                result.exit_code = self._exit_code(child)
                logger.info(
                    "Child exited with code %d after %d prompt(s)",
                    result.exit_code, result.prompts_handled,
                )
                return result

            else:  # TIMEOUT — no output this interval
                if not child.isalive():
                    logger.warning("Child died without EOF; reaping")
                    child.close()
                    result.status = SessionStatus.COMPLETED
                    result.exit_code = self._exit_code(child)
                    return result
                idle_timeouts += 1
                logger.debug(
                    "Idle timeout %d/%d",
                    idle_timeouts, self._settings.max_idle_timeouts,
                )
                if idle_timeouts >= self._settings.max_idle_timeouts:
                    logger.error(
                        "Child silent for %.0fs; terminating session",
                        idle_timeouts * self._settings.expect_timeout,
                    )
                    result.status = SessionStatus.IDLE_TIMEOUT
                    result.exit_code = 124  # conventional timeout exit code
                    return result

    def _handle_prompt(self, child: pexpect.spawn) -> Optional[Evaluation]:
        """Evaluate the intercepted command and answer the prompt.

        Returns ``None`` when the match was a TUI redraw of a prompt that
        was already answered (no response is sent again).
        """
        # Include the matched prompt text itself: some tools (e.g. Gemini
        # CLI's "Allow execution of: 'cmd'?") embed the command inside the
        # prompt rather than in the output preceding it.
        matched: str = child.match.group(0) if isinstance(child.match, re.Match) else ""
        context: str = self._clean((child.before or "") + matched)
        command = self._extract_command(context)

        if self._is_redraw(command):
            logger.debug("Ignoring redraw of already-answered prompt: %r", command)
            return None

        evaluation = self._engine.evaluate(command)

        approve = evaluation.approved and not self._dry_run
        response = (
            self._settings.approve_response if approve else self._settings.deny_response
        )
        logger.info(
            "%s %r (%s: %s)%s",
            "APPROVED" if approve else "DENIED",
            command,
            evaluation.tier.value,
            evaluation.reason,
            " [dry-run]" if self._dry_run and evaluation.approved else "",
        )
        child.sendline(response)
        return evaluation

    def _is_redraw(self, command: str) -> bool:
        """True if this prompt is a TUI re-render of the one just answered.

        Full-screen TUIs (ink/react-based) repaint their approval dialog many
        times — on every keystroke and resize — so the same prompt text can
        match repeatedly in the stream. Answering twice would leak stray
        keystrokes into the child, so identical commands seen within
        ``prompt_dedupe_window`` seconds are ignored.
        """
        now = time.monotonic()
        previous = self._last_prompt
        self._last_prompt = (command, now)
        return (
            previous is not None
            and previous[0] == command
            and (now - previous[1]) < self._settings.prompt_dedupe_window
        )

    @staticmethod
    def _clean(text: str) -> str:
        """Strip ANSI escape sequences and TUI frame glyphs from output."""
        return TUI_DECOR_RE.sub(" ", ANSI_ESCAPE_RE.sub("", text))

    def _extract_command(self, context: str) -> str:
        """Recover the command awaiting approval from pre-prompt output.

        Tries each extraction pattern against the tail of the output; falls
        back to the last non-empty line so the evaluator always receives
        *something* attributable (which the fail-safe will deny if it is
        not recognisable).
        """
        tail = context[-2000:]  # prompts reference nearby text only
        for pattern in self._extractors:
            matches = pattern.findall(tail)
            if matches:
                return matches[-1].strip()

        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        return lines[-1] if lines else "<unidentified command>"

    @staticmethod
    def _exit_code(child: pexpect.spawn) -> int:
        """Map pexpect's exit/signal status to a shell-style exit code."""
        if child.exitstatus is not None:
            return int(child.exitstatus)
        if child.signalstatus is not None:
            return 128 + int(child.signalstatus)
        return 1

    @staticmethod
    def _shutdown(child: pexpect.spawn) -> None:
        """Terminate the child gracefully, escalating to SIGKILL if needed."""
        if child.isalive():
            logger.info("Terminating child process (pid=%s)", child.pid)
            child.terminate(force=False)
            if child.isalive():
                child.terminate(force=True)
        if not child.closed:
            child.close()
