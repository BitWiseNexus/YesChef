#!/usr/bin/env python3
"""Deterministic stand-in for an interactive CLI that asks (y/n) prompts.

Simulates a coding agent announcing commands and requesting permission, so
the pexpect interceptor can be integration-tested without any external API.

Each positional argument is one command to "request". For every command the
script prints::

    About to run: `<command>`
    Proceed? (y/n):

then reads a line from stdin and reports ``EXECUTED`` or ``SKIPPED``.

Flags:
    --style STYLE    Prompt dialect: generic (y/n), gemini (numbered menu),
                     or codex (Allow command?).
    --hang-after N   After N prompts, sleep forever (simulates a hung child).
    --crash-after N  After N prompts, exit abruptly with code 7.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("commands", nargs="*", help="Commands to request approval for.")
    parser.add_argument("--style", choices=("generic", "gemini", "codex"), default="generic")
    parser.add_argument("--hang-after", type=int, default=-1)
    parser.add_argument("--crash-after", type=int, default=-1)
    return parser.parse_args()


def show_prompt(style: str, command: str) -> None:
    """Print one approval prompt in the requested tool dialect."""
    if style == "gemini":
        print(f"Allow execution of: '{command}'?")
        print("  1) Yes, allow once  2) Yes, allow always  3) No, suggest changes ", end="")
    elif style == "codex":
        # NB: no "(y/n)" here — it would double-match the prompt regex,
        # and the real Codex TUI shows "Yes (y) / No (n)" options instead.
        print(f"$ {command}")
        print("Allow command?  Yes (y) / No (n): ", end="")
    else:
        print(f"About to run: `{command}`")
        print("Proceed? (y/n): ", end="")
    sys.stdout.flush()


def main() -> int:
    args = parse_args()
    commands: List[str] = args.commands or ["cat README.md", "rm -rf /", "make build"]

    print("mock-cli v1.0 — simulated agent session")
    sys.stdout.flush()

    executed = 0
    for index, command in enumerate(commands):
        if index == args.hang_after:
            time.sleep(3600)  # simulate a wedged child process
        if index == args.crash_after:
            sys.exit(7)       # simulate an abrupt crash

        show_prompt(args.style, command)

        answer = sys.stdin.readline().strip().lower()
        if answer in ("y", "yes", "1", "2"):  # 1/2 = gemini "allow once/always"
            print(f"EXECUTED: {command}")
            executed += 1
        else:
            print(f"SKIPPED: {command}")
        sys.stdout.flush()

    print(f"done ({executed}/{len(commands)} executed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
