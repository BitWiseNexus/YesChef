"""Chef — an autonomous (y/n) prompt arbiter for interactive CLI tools.

Chef wraps an interactive child process (e.g. the Claude Code CLI) inside a
pseudo-terminal, intercepts permission prompts, evaluates the command being
requested through a two-tiered safety engine (deterministic rules first, an
LLM fallback second), and answers the prompt automatically.

Designed to run exclusively inside a Docker sandbox.
"""

__version__ = "1.0.0"
