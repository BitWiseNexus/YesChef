"""Unit tests for main.py helpers (no child process spawned)."""

from __future__ import annotations

from main import derive_context


def test_derive_context_picks_longest_non_flag() -> None:
    assert derive_context(["-i", "delete NOTES.md"]) == "delete NOTES.md"


def test_derive_context_ignores_flag_tokens() -> None:
    """Tokens starting with '-' are never treated as the task."""
    assert derive_context(["--yolo", "--model"]) == ""


def test_derive_context_is_best_effort_with_flag_values() -> None:
    """Known limitation: a flag's *value* can't be told from a task, so the
    longest non-flag token wins. The real prompt is normally the longest."""
    assert derive_context(["-m", "gpt", "refactor the auth module"]) == (
        "refactor the auth module"
    )


def test_derive_context_empty() -> None:
    assert derive_context(None) == ""
    assert derive_context([]) == ""


def test_derive_context_prefers_the_real_prompt() -> None:
    args = ["-i", "fix the bug in buggy.py and run it to verify", "--model", "x"]
    assert derive_context(args) == "fix the bug in buggy.py and run it to verify"
