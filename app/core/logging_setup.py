"""Structured JSON logging. Secrets are never passed to loggers by callers;
this module additionally redacts common secret-looking keys just in case."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone

REDACT_RE = re.compile(
    r"(access_token|client_secret|api[_-]?key|authorization|password|secret)\s*[=:]\s*\S+",
    re.IGNORECASE,
)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        return REDACT_RE.sub(r"\1=<redacted>", msg)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(h)

    # uvicorn noise down
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def event(logger: logging.Logger, kind: str, **fields) -> None:
    """One-line structured event for observability tooling."""
    payload = {"ts": datetime.now(timezone.utc).isoformat(), "event": kind, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False, default=str))
