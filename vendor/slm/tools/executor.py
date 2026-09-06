"""ToolExecutor: validate SLM JSON output, execute safely, audit everything.

Execution contract for every call:
1. Tool must exist in the registry (else ``unknown_tool``).
2. Caller must be authorized for the tool when an allow-list is configured
   (else ``unauthorized_tool``).
3. Raw JSON payloads are parsed strictly; non-object JSON is rejected.
4. Arguments are validated against the tool schema: every field required,
   no extras, types enforced with ``str``/``num``/``bool`` semantics
   (mirrors :mod:`slm.model.constrained` so constrained-decoder output is a
   drop-in input).
5. The function runs inside try/except -- user-facing code never sees raw
   tracebacks.
6. Every attempt (success or failure) is appended to the audit log as JSONL.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from slm.tools.registry import ToolRegistry


@dataclass(slots=True)
class ToolResult:
    """Outcome envelope returned to the workflow router."""

    ok: bool
    tool: str
    output: Any = None
    error: str | None = None
    detail: str | None = None


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "str":
        return isinstance(value, str)
    if expected == "num":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "bool":
        return isinstance(value, bool)
    return False  # pragma: no cover - registry validates schemas upfront


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        *,
        allowed_tools: list[str] | None = None,
        audit_path: str | Path | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self.audit_path = Path(audit_path) if audit_path else None
        self.history: list[ToolResult] = []

    # ------------------------------------------------------------------ audit

    def _audit(self, name: str, result: ToolResult, elapsed_ms: float) -> None:
        record = {"ts": time.time(), "elapsed_ms": round(elapsed_ms, 3), **asdict(result)}
        self.history.append(result)
        if not self.audit_path:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    # -------------------------------------------------------------- execution

    def execute(self, name: str, args: dict) -> ToolResult:
        """Validate and execute ``name`` with a plain dict of arguments."""
        return self.execute_json(name, args)

    def execute_json(self, name: str, raw_json: str | dict) -> ToolResult:
        """Validate and execute ``name`` with a JSON string or dict payload."""
        started = time.perf_counter()
        spec = self.registry.get(name)

        def finish(result: ToolResult) -> ToolResult:
            self._audit(str(name), result, (time.perf_counter() - started) * 1000)
            return result

        if spec is None:
            known = ", ".join(self.registry.names()) or "<none>"
            return finish(ToolResult(False, str(name), error="unknown_tool", detail=known))

        if self.allowed_tools is not None and spec.name not in self.allowed_tools:
            return finish(ToolResult(False, spec.name, error="unauthorized_tool",
                                     detail=f"allowed: {sorted(self.allowed_tools)}"))

        if isinstance(raw_json, str):
            try:
                args = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                return finish(ToolResult(False, spec.name, error="invalid_json", detail=str(exc)))
        else:
            args = raw_json
        if not isinstance(args, dict):
            return finish(ToolResult(False, spec.name, error="invalid_json",
                                     detail="payload must be a JSON object"))

        missing = [f for f in spec.schema if f not in args]
        if missing:
            return finish(ToolResult(False, spec.name, error="schema_violation",
                                     detail=f"missing fields: {missing}"))
        extras = sorted(set(args) - set(spec.schema))
        if extras:
            return finish(ToolResult(False, spec.name, error="schema_violation",
                                     detail=f"unexpected fields: {extras}"))
        bad_types = [
            f"{field}: expected {spec.schema[field]}, got {type(args[field]).__name__}"
            for field in spec.schema
            if not _type_ok(args[field], spec.schema[field])
        ]
        if bad_types:
            return finish(ToolResult(False, spec.name, error="schema_violation",
                                     detail="; ".join(bad_types)))

        try:
            output = spec.func(**args)
        except Exception as exc:  # noqa: BLE001 - sandbox boundary
            return finish(ToolResult(False, spec.name, error="execution_error", detail=str(exc)))
        return finish(ToolResult(True, spec.name, output=output))
