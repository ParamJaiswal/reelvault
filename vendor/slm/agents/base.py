"""BaseAgent: a role-prompted wrapper around the shared SLM runtime.

Design constraints (per the CPU-first ethos):
- No external agent frameworks; a thin, inspectable class hierarchy.
- Every ``run`` is wrapped in an OpenTelemetry span so multi-agent workflows
  produce one trace with one span per agent.
- Agents share a single CPURuntime + tokenizer (weights loaded once).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

from slm.tokenizer.bpe import BPETokenizer

tracer = trace.get_tracer("slm.agents")

ROLE_PROMPTS: dict[str, str] = {
    "extractor": (
        "You are a meticulous data-extraction clerk. From noisy business "
        "documents you output only strict JSON with exactly the requested "
        "fields. Never invent values: use empty/null and set missing_data."
    ),
    "validator": (
        "You are a senior auditor. You verify extracted JSON against company "
        "business rules and flag impossible or inconsistent records."
    ),
    "action": (
        "You are an operations dispatcher. You format tool calls as strict "
        "JSON matching each tool's argument schema and never invent tools."
    ),
}


@dataclass(slots=True)
class AgentResult:
    """Uniform envelope returned by every agent."""

    agent: str
    ok: bool
    output: Any = None
    error: str | None = None
    reasons: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


class BaseAgent:
    """Role-specialized wrapper over the shared SLM runtime."""

    name: str = "base"
    role_key: str = "base"

    def __init__(self, runtime, tokenizer: BPETokenizer) -> None:
        self.runtime = runtime
        self.tokenizer = tokenizer

    @property
    def role_prompt(self) -> str:
        return ROLE_PROMPTS.get(self.role_key, f"You are the {self.name} agent.")

    def _budget_ids(self, text: str, reserve: int) -> list[int]:
        budget = int(getattr(self.runtime.cfg, "max_position_embeddings", 512)) - reserve
        return self.tokenizer.encode(text)[:max(8, budget)]

    def run(self, task: str, *, context: dict[str, Any] | None = None) -> AgentResult:
        """Execute the agent's specialty under its own tracing span."""
        started = time.perf_counter()
        with tracer.start_as_current_span(
            f"agent.{self.name}",
            attributes={
                "agent.role": self.role_key,
                "agent.task_chars": len(task),
            },
        ) as span:
            try:
                result = self._run(task, context or {})
                result.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                if span:
                    span.set_attribute("agent.ok", result.ok)
                    if result.error:
                        span.set_attribute("agent.error", result.error)
                return result
            except Exception as exc:  # noqa: BLE001 - agent boundary
                if span:
                    span.record_exception(exc)
                    span.set_attribute("agent.ok", False)
                return AgentResult(
                    agent=self.name,
                    ok=False,
                    error=f"agent_crash: {exc}",
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                )

    def _run(self, task: str, context: dict[str, Any]) -> AgentResult:  # pragma: no cover
        raise NotImplementedError