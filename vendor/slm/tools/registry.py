"""ToolRegistry: named business functions with schemas and risk levels.

A tool is any plain Python callable annotated with:
- a JSON-ish ``schema`` mapping argument name -> ``"str" | "num" | "bool"``
  (same vocabulary as :mod:`slm.model.constrained`, so constrained decoding
  output can be validated directly), and
- a ``risk`` level among ``low | medium | high`` used by the workflow router
  to decide whether human approval is required before/after execution.

All schema fields are required; unexpected keys are rejected.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

RISK_LEVELS: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
VALID_TYPES = {"str", "num", "bool"}


@dataclass(slots=True)
class ToolSpec:
    """Registered tool metadata."""

    name: str
    description: str
    schema: dict[str, str]
    func: Callable[..., Any]
    risk: str = "low"

    def __post_init__(self) -> None:
        if self.risk not in RISK_LEVELS:
            raise ValueError(f"risk must be one of {list(RISK_LEVELS)}, got {self.risk!r}")
        bad = {k: v for k, v in self.schema.items() if str(v) not in VALID_TYPES}
        if bad:
            raise ValueError(f"unsupported schema types {bad}; use {VALID_TYPES}")


class ToolRegistry:
    """Central catalogue of executable tools."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    # ------------------------------------------------------------- registration

    def register(
        self,
        name: str,
        description: str,
        schema: dict[str, str],
        *,
        risk: str = "low",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator form: ``@registry.register("create_invoice", ..., {...})``."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.register_function(name, description, schema, func, risk=risk)
            return func

        return decorator

    def register_function(
        self,
        name: str,
        description: str,
        schema: dict[str, str],
        func: Callable[..., Any],
        *,
        risk: str = "low",
    ) -> ToolSpec:
        if not name or not str(name).isidentifier():
            raise ValueError(f"tool name must be a valid identifier, got {name!r}")
        if name in self._tools:
            raise ValueError(f"tool already registered: {name}")
        params = set(inspect.signature(func).parameters)
        missing_in_sig = set(schema) - params
        if missing_in_sig:
            raise ValueError(f"schema fields absent from function signature: {sorted(missing_in_sig)}")
        spec = ToolSpec(
            name=name, description=description, schema=dict(schema), func=func, risk=risk
        )
        self._tools[name] = spec
        return spec

    # ------------------------------------------------------------------ lookup

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(str(name))

    def __contains__(self, name: str) -> bool:
        return str(name) in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def risk_of(self, name: str) -> int | None:
        spec = self.get(name)
        return RISK_LEVELS[spec.risk] if spec else None


def build_default_registry() -> ToolRegistry:
    """Demo business toolset wired into the workflow router by default."""
    registry = ToolRegistry()

    @registry.register(
        "create_invoice",
        "Create an invoice record for a customer.",
        {"invoice_id": "str", "customer": "str", "amount": "num"},
        risk="medium",
    )
    def create_invoice(invoice_id: str, customer: str, amount: float) -> dict:
        return {
            "status": "created",
            "invoice_id": invoice_id,
            "customer": customer,
            "amount": round(float(amount), 2),
        }

    @registry.register(
        "log_note",
        "Append an internal note to the operations log.",
        {"note": "str"},
        risk="low",
    )
    def log_note(note: str) -> dict:
        return {"status": "logged", "chars": len(note)}

    @registry.register(
        "escalate_to_human",
        "Escalate the conversation to a human operator.",
        {"reason": "str", "priority": "str"},
        risk="high",
    )
    def escalate_to_human(reason: str, priority: str) -> dict:
        return {"status": "escalated", "reason": reason, "priority": priority}

    return registry