# Chef

**Chef** is a sandboxed CLI wrapper that autonomously answers the `(y/n)` permission
prompts of interactive AI coding agents — [Claude Code](https://docs.anthropic.com/en/docs/claude-code),
[Codex CLI](https://developers.openai.com/codex/cli), and
[Gemini CLI](https://github.com/google-gemini/gemini-cli) are installed in the
sandbox out of the box, and any other prompt-driven CLI works via configuration.
It spawns the child inside a pseudo-terminal, intercepts every permission prompt,
evaluates the requested command through a two-tiered safety engine, and answers
on your behalf — with every decision written to a JSON audit trail.

> ⚠️ **Chef approves terminal commands without a human in the loop.**
> It is designed to run **only inside the provided Docker sandbox**, where the
> blast radius is confined to the mounted `dummy_workspace/` folder.

## Architecture

```
┌──────────────────────── Docker sandbox ────────────────────────┐
│                                                                │
│  main.py ──► ProcessWrapper (pexpect pty)                      │
│                  │  mirrors output, detects prompt regex       │
│                  ▼                                             │
│            EvaluationEngine                                    │
│              ├─ Tier 1: DeterministicEvaluator                 │
│              │     regex whitelist (read-only) / blacklist     │
│              ├─ Tier 2: LLMEvaluator (OpenAI-compatible API,   │
│              │     strict JSON schema, retries + backoff)      │
│              └─ Fail-safe: UNKNOWN ⇒ DENY                      │
│                  │                                             │
│                  ├──► audit.log (newline-delimited JSON)       │
│                  └──► child stdin ("y" / "n")                  │
│                                                                │
│  child CLI (claude) ── cwd: /workspace ◄── ./dummy_workspace   │
└────────────────────────────────────────────────────────────────┘
```

**Decision policy (deny-by-default):**

1. **Blacklist** match → `UNSAFE`, denied (checked first; wins over everything).
2. **Whitelist** match (every segment of a compound command) → `SAFE`, approved.
3. Otherwise → escalate to the **LLM** (`{"decision": "SAFE"|"UNSAFE", "reason": "..."}`, schema-forced).
4. LLM unavailable, exhausted, or malformed → **fail-safe denies**.

## Project layout

```
chef/
├── config.py              # pydantic-settings: all tunables from .env (CHEF_* vars)
├── core/
│   ├── logger.py          # JSON audit logging (audit.log) + stderr console logs
│   └── wrapper.py         # pexpect spawn/expect loop, TIMEOUT & EOF handling
└── evaluators/
    ├── models.py          # Verdict / Tier / Evaluation typed models
    ├── deterministic.py   # Tier 1: regex whitelist & blacklist
    ├── llm.py             # Tier 2: OpenAI-compatible client, retries, schema forcing
    └── engine.py          # orchestration + fail-safe + audit hook
main.py                    # CLI entrypoint
tests/                     # pytest suite incl. mock_cli.py for pexpect integration
```

## Quick start (Docker — the supported way)

```bash
cp .env.example .env       # fill in CHEF_LLM_API_KEY and ANTHROPIC_API_KEY
docker compose build
docker compose run --rm chef                  # wraps `claude` in /workspace
docker compose run --rm chef -- claude "fix the failing tests"
docker compose run --rm chef -- codex "add input validation"
docker compose run --rm chef -- gemini "write unit tests"
docker compose run --rm chef --dry-run        # evaluate + audit, never approve
```

The container drops all capabilities, forbids privilege escalation, runs as a
non-root user, and mounts **only** `./dummy_workspace` → `/workspace`.

## Supported tools

Claude Code, Codex CLI, and Gemini CLI are all installed in the image. The
default `CHEF_PROMPT_PATTERN` recognises all three tools' permission prompts;
what differs per tool is how to *answer* them. Pick a profile in `.env`:

| Tool | `CHEF_CHILD_COMMAND` | `CHEF_APPROVE_RESPONSE` | `CHEF_DENY_RESPONSE` | Credential |
|---|---|---|---|---|
| Claude Code | `claude` | `1` | `2` | `ANTHROPIC_API_KEY` |
| Codex CLI | `codex` | `y` | `n` | `OPENAI_API_KEY` |
| Gemini CLI | `gemini` | `1` | `3` | `GEMINI_API_KEY` |

Any other interactive CLI can be wrapped by setting `CHEF_PROMPT_PATTERN` to
match its prompt and, if its output format is unusual, adding a regex (with one
capture group) to `CHEF_EXTRA_COMMAND_PATTERNS` so Chef can extract the command
being requested. These TUIs evolve quickly — if prompts change format, run with
`--dry-run` first and watch the audit log to confirm interception still works
before letting Chef approve anything.

## Local development

```bash
./setup.sh                          # venv + deps + .env seed (POSIX)
source .venv/bin/activate
pytest                              # unit tests run anywhere;
                                    # pexpect integration tests need a POSIX pty
```

On Windows, run the full suite (including the pexpect integration tests) inside
the dedicated Docker test stage:

```bash
docker build --target test .        # the build fails if any test fails
```

## Configuration

Everything is environment-driven (see [.env.example](.env.example)); the prefix is `CHEF_`.

| Variable | Default | Purpose |
|---|---|---|
| `CHEF_CHILD_COMMAND` | `claude` | Executable to wrap |
| `CHEF_PROMPT_PATTERN` | `(y/n)`-style regex | What counts as a permission prompt |
| `CHEF_EXPECT_TIMEOUT` | `30` | Seconds of silence per pexpect interval |
| `CHEF_MAX_IDLE_TIMEOUTS` | `10` | Silent intervals tolerated before terminating |
| `CHEF_LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `CHEF_LLM_MODEL` | `gpt-4o-mini` | Tier 2 model |
| `CHEF_LLM_MAX_RETRIES` | `3` | Retries with exponential backoff + jitter |
| `CHEF_LLM_ENABLED` | `true` | `false` ⇒ unknown commands are simply denied |
| `CHEF_LOG_LEVEL` | `INFO` | Console verbosity |
| `CHEF_AUDIT_LOG_PATH` | `audit.log` | JSON decision trail |

## Audit trail

Each decision is one JSON line in `audit.log`:

```json
{"timestamp": "2026-06-11T10:15:42.123456+00:00", "level": "INFO", "logger": "chef.audit",
 "message": "decision: UNSAFE [deterministic] 'rm -rf /'", "event": "permission_decision",
 "command": "rm -rf /", "tier": "deterministic", "verdict": "UNSAFE",
 "approved": false, "reason": "Matched blacklist pattern: '\\\\brm\\\\s+...'"}
```

Query it with `jq`, e.g. everything that was auto-approved by the LLM tier:

```bash
jq 'select(.event == "permission_decision" and .approved and .tier == "llm")' audit.log
```
