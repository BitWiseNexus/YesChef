"""Integration tests: ProcessWrapper driving the mock CLI through pexpect.

These tests exercise the full interception pipeline (spawn → detect prompt →
extract command → evaluate → respond) deterministically, with Tier 2 disabled
so no external API is ever contacted.

pexpect requires a POSIX pty, so the suite is skipped on Windows — run it
inside the Docker test stage (`docker build --target test .`).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, List

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="pexpect requires a POSIX pty (run in Docker)"
)

MOCK_CLI = str(Path(__file__).parent / "mock_cli.py")


def make_wrapper(make_settings: Callable, **settings_overrides):
    from chef.core.wrapper import ProcessWrapper
    from chef.evaluators.engine import EvaluationEngine

    settings = make_settings(**settings_overrides)
    return ProcessWrapper(settings, EvaluationEngine(settings))


def run_mock(wrapper, commands: List[str], extra_args: List[str] | None = None):
    return wrapper.run(command=sys.executable, args=[MOCK_CLI, *commands, *(extra_args or [])])


def test_whitelisted_command_is_approved(make_settings: Callable, capfd) -> None:
    wrapper = make_wrapper(make_settings)
    result = run_mock(wrapper, ["cat README.md"])

    from chef.core.wrapper import SessionStatus

    assert result.status is SessionStatus.COMPLETED
    assert result.exit_code == 0
    assert result.prompts_handled == 1
    assert result.decisions[0].approved

    out, _ = capfd.readouterr()
    assert "EXECUTED: cat README.md" in out


def test_blacklisted_command_is_denied(make_settings: Callable, capfd) -> None:
    wrapper = make_wrapper(make_settings)
    result = run_mock(wrapper, ["rm -rf /"])

    assert result.prompts_handled == 1
    assert not result.decisions[0].approved

    out, _ = capfd.readouterr()
    assert "SKIPPED: rm -rf /" in out


def test_unknown_command_hits_fail_safe(make_settings: Callable, capfd) -> None:
    """No LLM configured → unknown command must be denied, not approved."""
    wrapper = make_wrapper(make_settings)
    result = run_mock(wrapper, ["make deploy"])

    from chef.evaluators.models import Tier

    assert result.decisions[0].tier is Tier.FAIL_SAFE
    out, _ = capfd.readouterr()
    assert "SKIPPED: make deploy" in out


def test_mixed_session_handles_every_prompt(make_settings: Callable, capfd) -> None:
    wrapper = make_wrapper(make_settings)
    result = run_mock(wrapper, ["git status", "rm -rf /", "cat notes.txt"])

    assert result.prompts_handled == 3
    assert [d.approved for d in result.decisions] == [True, False, True]

    out, _ = capfd.readouterr()
    assert "done (2/3 executed)" in out


def test_dry_run_never_approves(make_settings: Callable, capfd) -> None:
    from chef.core.wrapper import ProcessWrapper
    from chef.evaluators.engine import EvaluationEngine

    settings = make_settings()
    wrapper = ProcessWrapper(settings, EvaluationEngine(settings), dry_run=True)
    result = run_mock(wrapper, ["cat README.md"])

    assert result.decisions[0].approved  # evaluation says SAFE...
    out, _ = capfd.readouterr()
    assert "SKIPPED: cat README.md" in out  # ...but dry-run still answered 'n'


def test_child_crash_is_handled_gracefully(make_settings: Callable) -> None:
    """An abrupt child exit (EOF) must surface its exit code, not raise."""
    from chef.core.wrapper import SessionStatus

    wrapper = make_wrapper(make_settings)
    result = run_mock(wrapper, ["cat a.txt", "cat b.txt"], extra_args=["--crash-after", "1"])

    assert result.status is SessionStatus.COMPLETED
    assert result.exit_code == 7
    assert result.prompts_handled == 1


def test_hung_child_triggers_idle_timeout(make_settings: Callable) -> None:
    """A silent child is terminated after max_idle_timeouts intervals."""
    from chef.core.wrapper import SessionStatus

    wrapper = make_wrapper(make_settings, expect_timeout=0.5, max_idle_timeouts=2)
    result = run_mock(wrapper, ["cat a.txt", "cat b.txt"], extra_args=["--hang-after", "1"])

    assert result.status is SessionStatus.IDLE_TIMEOUT
    assert result.exit_code == 124
    assert result.prompts_handled == 1
