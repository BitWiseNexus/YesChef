"""Centralised configuration for Chef.

All tunables are ingested from the environment (or a ``.env`` file) via
``pydantic-settings``. Nothing is hardcoded at call sites — modules receive a
:class:`ChefSettings` instance and read what they need from it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ChefSettings(BaseSettings):
    """Runtime settings, sourced from environment variables / ``.env``.

    Every field can be overridden with a ``CHEF_``-prefixed environment
    variable, e.g. ``CHEF_LOG_LEVEL=DEBUG`` or ``CHEF_CHILD_COMMAND=claude``.
    """

    model_config = SettingsConfigDict(
        env_prefix="CHEF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Child process                                                      #
    # ------------------------------------------------------------------ #
    child_command: str = Field(
        default="claude",
        description="Executable to wrap (the interactive CLI under automation).",
    )
    child_args: List[str] = Field(
        default_factory=list,
        description="Default arguments passed to the child command.",
    )
    child_cwd: str = Field(
        default="/workspace",
        description="Working directory for the child process (the sandboxed mount).",
    )
    prompt_pattern: str = Field(
        default=(
            r"\(y/n\)|\(Y/n\)|\[y/N\]|\[Y/n\]"
            r"|Do you want to proceed\?"             # Claude Code
            r"|Allow execution of:?\s*'[^']*'\??"    # Gemini CLI (shell)
            r"|Apply this change\?"                  # Gemini CLI (edits)
            r"|Allow command\?"                      # Codex CLI
            r"|Would you like to run th(?:is|e following) command\?"
        ),
        description="Regex that identifies a permission prompt in child output.",
    )
    extra_command_patterns: List[str] = Field(
        default_factory=list,
        description=(
            "Additional regexes (each with exactly one capture group) tried "
            "BEFORE the built-ins when extracting the command awaiting "
            "approval from the output around a prompt. JSON list in the env."
        ),
    )
    approve_response: str = Field(
        default="y",
        description="Text sent to the child to approve a prompt.",
    )
    deny_response: str = Field(
        default="n",
        description="Text sent to the child to deny a prompt.",
    )
    prompt_dedupe_window: float = Field(
        default=2.0,
        ge=0,
        description=(
            "Seconds within which an identical prompt (same extracted "
            "command) is treated as a TUI redraw and not answered again."
        ),
    )
    expect_timeout: float = Field(
        default=30.0,
        gt=0,
        description="Seconds pexpect waits for output before raising TIMEOUT.",
    )
    max_idle_timeouts: int = Field(
        default=10,
        ge=1,
        description=(
            "Number of consecutive pexpect TIMEOUTs tolerated while the child "
            "is alive but silent, before Chef terminates the session."
        ),
    )

    # ------------------------------------------------------------------ #
    # LLM evaluator (Tier 2)                                             #
    # ------------------------------------------------------------------ #
    llm_api_key: str = Field(
        default="",
        description="API key for the OpenAI-compatible endpoint.",
    )
    llm_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL of the OpenAI-compatible API.",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Model identifier used for heuristic safety evaluation.",
    )
    llm_timeout: float = Field(
        default=15.0,
        gt=0,
        description="Per-request timeout (seconds) for the LLM API.",
    )
    llm_max_retries: int = Field(
        default=3,
        ge=0,
        description="Retry attempts for transient LLM API failures.",
    )
    llm_backoff_base: float = Field(
        default=1.0,
        gt=0,
        description="Base delay (seconds) for exponential backoff between retries.",
    )
    llm_enabled: bool = Field(
        default=True,
        description=(
            "If false, Tier 2 is skipped entirely and unknown commands are "
            "denied by the fail-safe."
        ),
    )

    # ------------------------------------------------------------------ #
    # Logging                                                            #
    # ------------------------------------------------------------------ #
    log_level: str = Field(
        default="INFO",
        description="Root log level (DEBUG, INFO, WARNING, ERROR).",
    )
    audit_log_path: str = Field(
        default="audit.log",
        description="Destination file for structured JSON audit records.",
    )


@lru_cache(maxsize=1)
def get_settings() -> ChefSettings:
    """Return the process-wide settings singleton (cached)."""
    return ChefSettings()
