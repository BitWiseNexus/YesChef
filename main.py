#!/usr/bin/env python3
"""Chef CLI entrypoint.

Usage examples (inside the Docker sandbox)::

    # Wrap the default child command from .env (claude):
    python main.py

    # Wrap an explicit command:
    python main.py -- claude --dangerously-skip-permissions=false

    # Rehearse rules without ever approving anything:
    python main.py --dry-run -- ./some_tool.sh
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import List, Optional, Sequence

from dotenv import load_dotenv

from chef import __version__
from chef.config import get_settings
from chef.core.logger import setup_logging
from chef.core.wrapper import ProcessWrapper, SessionStatus
from chef.evaluators.engine import EvaluationEngine

logger = logging.getLogger("chef.main")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments. Everything after ``--`` is the child command."""
    parser = argparse.ArgumentParser(
        prog="chef",
        description="Autonomously answer (y/n) permission prompts of an interactive CLI.",
    )
    parser.add_argument("--version", action="version", version=f"chef {__version__}")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and audit every prompt but always answer 'no'.",
    )
    parser.add_argument(
        "child",
        nargs=argparse.REMAINDER,
        metavar="-- COMMAND [ARGS...]",
        help="Child command to wrap (defaults to CHEF_CHILD_COMMAND from .env).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Compose settings, logging, engine and wrapper; return an exit code."""
    load_dotenv()  # make .env visible before settings are constructed
    args = parse_args(argv)
    settings = get_settings()
    setup_logging(settings.log_level, settings.audit_log_path)

    child_argv: List[str] = [a for a in args.child if a != "--"]
    command: Optional[str] = child_argv[0] if child_argv else None
    command_args: Optional[List[str]] = child_argv[1:] if child_argv else None

    if settings.llm_enabled and not settings.llm_api_key:
        logger.warning(
            "CHEF_LLM_API_KEY is not set: Tier 2 is disabled and every command "
            "not on the whitelist will be denied by the fail-safe."
        )

    engine = EvaluationEngine(settings)
    wrapper = ProcessWrapper(settings, engine, dry_run=args.dry_run)

    try:
        result = wrapper.run(command=command, args=command_args)
    finally:
        engine.close()

    if result.status is not SessionStatus.COMPLETED:
        logger.error("Session ended abnormally: %s", result.status.value)
    logger.info(
        "Session summary: status=%s exit_code=%d prompts=%d approved=%d",
        result.status.value,
        result.exit_code,
        result.prompts_handled,
        sum(1 for d in result.decisions if d.approved),
    )
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
