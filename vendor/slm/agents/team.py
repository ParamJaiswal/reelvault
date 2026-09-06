"""ExtractionTeam: delegated Extractor -> Validator pipeline (peer review).

Mimics an internal review process:

1. The **ExtractorAgent** produces a structured record.
2. The **ValidatorAgent** audits it against business rules.
3. On failure, the extractor gets one bounded revision attempt (validator
   violations are fed back as context); if the revision still fails, the
   record is returned flagged ``needs_review`` instead of silently accepted.

Every stage is a traced span (see :mod:`slm.serving.tracing`), so the whole
peer-review process shows up as one trace with child spans per agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace

from slm.agents.action import ActionAgent
from slm.agents.base import AgentResult, BaseAgent
from slm.agents.extractor import ExtractorAgent
from slm.agents.validator import ValidatorAgent
from slm.tokenizer.bpe import BPETokenizer
from slm.tools import ToolExecutor

tracer = trace.get_tracer("slm.agents.team")


@dataclass(slots=True)
class TeamVerdict:
    """Outcome of the full extraction team pipeline."""

    ok: bool                 # True only when validator approved the final record
    record: dict | None
    route_meta: str          # "validated" | "needs_review"
    violations: list[str] = field(default_factory=list)
    attempts: int = 0
    total_latency_ms: float = 0.0
    stages: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "record": self.record,
            "status": self.route_meta,
            "violations": self.violations,
            "attempts": self.attempts,
            "total_latency_ms": self.total_latency_ms,
            "stages": self.stages,
        }


class ExtractionTeam:
    """Extractor + Validator with a bounded revision loop."""

    def __init__(
        self,
        runtime,
        tokenizer: BPETokenizer,
        *,
        max_revisions: int = 1,
        schema: dict[str, str] | None = None,
    ) -> None:
        self.extractor = ExtractorAgent(runtime, tokenizer, schema=schema)
        self.validator = ValidatorAgent(runtime, tokenizer)
        self.max_revisions = max_revisions

    @staticmethod
    def _stage(agent: BaseAgent, result: AgentResult) -> dict:
        return {
            "node": f"agent.{result.agent}",
            "ok": result.ok,
            "latency_ms": result.latency_ms,
            "error": result.error,
            "reasons": result.reasons[:6],
        }

    def run(self, document: str) -> TeamVerdict:
        started = __import__("time").perf_counter()
        stages: list[dict] = []
        record: dict | None = None
        violations: list[str] = []
        attempts = 0

        with tracer.start_as_current_span(
            "team.extraction",
            attributes={"team.members": 2, "document_chars": len(document)},
        ) as span:
            while attempts <= self.max_revisions:
                attempts += 1
                # ---- extraction (possibly a revision informed by violations)
                context: dict[str, Any] = {"document": document}
                if violations:
                    context["prior_violations"] = list(violations)
                    span and span.set_attribute("team.revision", attempts - 1)

                ext = self.extractor.run(document, context=context)
                stages.append(self._stage(self.extractor, ext))
                if not ext.ok:
                    break
                record = ext.output

                # ---- validation (peer review)
                val = self.validator.run(document, context={"record": record})
                stages.append(self._stage(self.validator, val))
                if val.ok:
                    violations = []
                    break
                violations = val.reasons
                record = None  # rejected; retry or give up below

            total_ms = (__import__("time").perf_counter() - started) * 1000
            approved = record is not None
            status = "validated" if approved else "needs_review"
            if span:
                span.set_attribute("team.status", status)
                span.set_attribute("team.attempts", attempts)
            return TeamVerdict(
                ok=approved,
                record=record,
                route_meta=status,
                violations=violations,
                attempts=attempts,
                total_latency_ms=round(total_ms, 2),
                stages=stages,
            )


def build_action_agent(
    runtime, tokenizer: BPETokenizer, executor: ToolExecutor
) -> ActionAgent:
    """Convenience factory keeping tool wiring in one place."""
    return ActionAgent(runtime, tokenizer, executor)