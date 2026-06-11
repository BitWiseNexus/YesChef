"""Structured JSON logging and the append-only audit trail.

Two sinks are configured:
    * ``stderr`` — human-readable operational logs (kept off stdout so they
      never interleave with the mirrored child terminal output).
    * ``audit.log`` — newline-delimited JSON records, one per log event,
      suitable for ingestion by jq / Loki / ELK.

Audit records for permission decisions carry a stable set of fields:
``timestamp``, ``event``, ``command``, ``tier``, ``verdict``, ``reason``
and ``approved``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from chef.evaluators.models import Evaluation

AUDIT_LOGGER_NAME = "chef.audit"

#: Attributes every LogRecord has; anything else was passed via ``extra=``.
_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render every log record as a single JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge structured fields supplied through ``extra=``.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(log_level: str, audit_log_path: str) -> None:
    """Configure the root logger (stderr) and the JSON audit file handler.

    Safe to call multiple times — handlers are reset, not duplicated.

    Args:
        log_level: Root level name, e.g. ``"INFO"``.
        audit_log_path: File that receives newline-delimited JSON records.
    """
    root = logging.getLogger()
    root.setLevel(log_level.upper())
    root.handlers.clear()

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(console)

    audit_handler = logging.FileHandler(audit_log_path, encoding="utf-8")
    audit_handler.setFormatter(JsonFormatter())
    audit_handler.setLevel(logging.INFO)

    audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)
    audit_logger.handlers.clear()
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = True  # decisions also surface on stderr


def audit(evaluation: "Evaluation") -> None:
    """Write one structured decision record to the audit trail."""
    logging.getLogger(AUDIT_LOGGER_NAME).info(
        "decision: %s [%s] %r",
        evaluation.verdict.value,
        evaluation.tier.value,
        evaluation.command,
        extra={
            "event": "permission_decision",
            "command": evaluation.command,
            "tier": evaluation.tier.value,
            "verdict": evaluation.verdict.value,
            "approved": evaluation.approved,
            "reason": evaluation.reason,
        },
    )
