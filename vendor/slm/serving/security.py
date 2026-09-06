"""API security guardrails: API keys, payload limits, injection scanning.

Three independent layers, all fail-closed where it matters and configurable
via environment so local development stays friction-free:

1. **API key auth** (:func:`require_api_key`) -- enabled only when
   ``SLM_API_KEY`` is set (comma-separated list accepted). Requests without a
   valid ``X-API-Key`` header are rejected with ``401``. Unset => open mode,
   preserving backwards compatibility for local runs.
2. **Payload size limit** -- requests whose declared body exceeds
   ``SLM_MAX_BODY_BYTES`` (default 262144) are rejected with ``413`` before
   parsing, preventing CPU/memory exhaustion via giant payloads.
3. **Prompt-injection scanner** (:func:`scan_prompt_injection`) -- heuristic
   blocklist over the decoded JSON body; suspicious payloads are rejected
   with ``400`` before reaching the model or the RAG pipeline.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import HTTPException, Request

MAX_BODY_BYTES_DEFAULT = 262_144  # 256 KB -- generous for business text
_API_KEY_HEADER = "x-api-key"

# Conservative heuristics tuned to avoid false positives on normal business
# language while catching the common instruction-hijack families.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts?|rules)",
        r"disregard\s+(?:all\s+|any\s+)?(?:previous|prior|your)\s+(?:instructions|prompts?|rules)",
        r"(?:reveal|show|print|repeat|output)\s+.{0,24}(?:system\s+)?(?:prompt|instructions)",
        r"you\s+are\s+now\s+(?:a|an|the)\b",
        r"pretend\s+(?:that\s+)?you\s+are",
        r"act\s+as\s+(?:if\s+you\s+(?:are|were)|a|an|the)\b",
        r"\bsystem\s*:\s*",
        r"<\|?\s*(?:im_start|im_end|endoftext|system)\s*\|?>",
        r"\bdeveloper\s+mode\b",
        r"\benable\s+dAN\b|\bDAN\s+mode\b",
        r"\bexecute\s+(?:arbitrary|shell)\s+(?:code|commands?)\b",
        r"\bdrop\s+table\b|\bunion\s+select\b",
    )
)


def configured_api_keys() -> set[str]:
    """API keys from ``SLM_API_KEY`` (comma-separated). Empty set = open mode."""
    raw = os.environ.get("SLM_API_KEY", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def max_body_bytes() -> int:
    raw = os.environ.get("SLM_MAX_BODY_BYTES", "")
    try:
        return max(1024, int(raw)) if raw else MAX_BODY_BYTES_DEFAULT
    except ValueError:
        return MAX_BODY_BYTES_DEFAULT


async def require_api_key(request: Request) -> None:
    """FastAPI dependency: enforce ``X-API-Key`` when keys are configured."""
    keys = configured_api_keys()
    if not keys:  # open mode -- no keys configured
        return
    provided = request.headers.get(_API_KEY_HEADER, "")
    if provided not in keys:
        raise HTTPException(status_code=401, detail="missing or invalid API key")


def _iter_strings(value: Any) -> "list[str]":
    """Collect every string in an arbitrarily nested JSON body."""
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_iter_strings(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_iter_strings(v))
    return found


def scan_text_for_injection(text: str) -> str | None:
    """Return the matched pattern family if ``text`` looks like an injection."""
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            return pattern.pattern
    return None


async def guard_payload(request: Request) -> None:
    """FastAPI dependency: size limit + injection scan over the JSON body.

    - Body larger than :func:`max_body_bytes` => ``413``.
    - Malformed JSON is left to Pydantic (=> 422); only *valid* JSON bodies
      are scanned.
    - Injection hit => ``400`` with the matched family.
    """
    limit = max_body_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"payload too large: {declared} bytes > {limit} limit",
        )
    body = await request.body()
    if len(body) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"payload too large: {len(body)} bytes > {limit} limit",
        )
    try:
        parsed = json.loads(body) if body else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return  # let pydantic produce the 422
    if parsed is None:
        return
    for text in _iter_strings(parsed):
        hit = scan_text_for_injection(text)
        if hit:
            raise HTTPException(
                status_code=400,
                detail=f"request blocked by prompt-injection guard (pattern: {hit[:48]}...)",
            )


__all__ = [
    "guard_payload",
    "max_body_bytes",
    "require_api_key",
    "configured_api_keys",
    "scan_text_for_injection",
]