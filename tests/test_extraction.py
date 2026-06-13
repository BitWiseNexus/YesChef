"""Unit tests for command extraction across tool dialects.

These exercise ``ProcessWrapper._extract_command`` directly with captured
output snippets, so they run on any platform (no pty needed).
"""

from __future__ import annotations

from typing import Callable

from chef.core.wrapper import ProcessWrapper
from chef.evaluators.engine import EvaluationEngine


def make_wrapper(make_settings: Callable, **overrides) -> ProcessWrapper:
    settings = make_settings(**overrides)
    return ProcessWrapper(settings, EvaluationEngine(settings))


def test_claude_code_panel_style(make_settings: Callable) -> None:
    context = (
        "Bash command\n\n"
        "  git push origin main\n\n"
        "Do you want to proceed?"
    )
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "git push origin main"


def test_gemini_allow_execution_style(make_settings: Callable) -> None:
    context = "Some agent chatter...\nAllow execution of: 'rm -rf /tmp/x'?"
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "rm -rf /tmp/x"


def test_codex_dollar_line_style(make_settings: Callable) -> None:
    context = "I'll check the repo state.\n$ git status\nAllow command? "
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "git status"


def test_shell_tool_call_style(make_settings: Callable) -> None:
    context = "→ Shell(npm test)\nproceed?"
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "npm test"


def test_backtick_style(make_settings: Callable) -> None:
    context = "Run `cargo build` now?"
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "cargo build"


def test_fallback_uses_last_nonempty_line(make_settings: Callable) -> None:
    context = "something unrecognisable\n\n   make deploy   \n"
    wrapper = make_wrapper(make_settings)
    assert wrapper._extract_command(context) == "make deploy"


def test_extra_patterns_take_precedence(make_settings: Callable) -> None:
    """User-supplied patterns from settings beat the built-ins."""
    context = 'EXEC-REQUEST "drop table users" — About to run: `ls`'
    wrapper = make_wrapper(
        make_settings,
        extra_command_patterns=[r'EXEC-REQUEST "([^"]+)"'],
    )
    assert wrapper._extract_command(context) == "drop table users"


def test_ansi_and_tui_decor_are_stripped(make_settings: Callable) -> None:
    """Real Gemini CLI output: ANSI colours + box-drawing around the dialog."""
    raw = (
        "\x1b[37m│\x1b[39m ? WriteFile  Writing to NOTES.md \x1b[37m│\x1b[39m\r\n"
        "\x1b[37m│\x1b[39m \x1b[97mApply this change?\x1b[39m\r\n"
        "● 1. Allow once\r\n"
    )
    wrapper = make_wrapper(make_settings)
    cleaned = wrapper._clean(raw)
    assert "\x1b" not in cleaned
    assert "│" not in cleaned
    assert wrapper._extract_command(cleaned) == "WriteFile  Writing to NOTES.md"


def test_gemini_writefile_is_whitelisted_end_to_end(make_settings: Callable) -> None:
    """The extracted WriteFile action must be approved by Tier 1 (no LLM)."""
    from chef.evaluators.models import Tier

    engine = EvaluationEngine(make_settings())
    result = engine.evaluate("WriteFile  Writing to NOTES.md")
    assert result.approved
    assert result.tier is Tier.DETERMINISTIC


def test_redraw_dedupe_window(make_settings: Callable) -> None:
    """Identical prompts in quick succession are treated as TUI redraws."""
    wrapper = make_wrapper(make_settings, prompt_dedupe_window=60.0)
    assert not wrapper._is_redraw("WriteFile Writing to NOTES.md")  # first: answer
    assert wrapper._is_redraw("WriteFile Writing to NOTES.md")      # redraw: skip
    assert not wrapper._is_redraw("python3 buggy.py")               # new prompt
    assert not wrapper._is_redraw("WriteFile Writing to NOTES.md")  # different again


def test_dedupe_disabled_with_zero_window(make_settings: Callable) -> None:
    wrapper = make_wrapper(make_settings, prompt_dedupe_window=0.0)
    assert not wrapper._is_redraw("cmd")
    assert not wrapper._is_redraw("cmd")


def test_default_prompt_pattern_matches_all_tools(make_settings: Callable) -> None:
    """The default prompt regex must fire on every supported tool's prompt."""
    wrapper = make_wrapper(make_settings)
    for prompt in (
        "Proceed? (y/n): ",                       # generic
        "Do you want to proceed?",                # Claude Code
        "Allow execution of: 'python3'?",         # Gemini CLI
        "Apply this change?",                     # Gemini CLI (edits)
        "Allow command?",                         # Codex
        "Would you like to run this command?",    # Codex (variant)
    ):
        assert wrapper._prompt_re.search(prompt), f"pattern missed: {prompt!r}"
