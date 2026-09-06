"""Business Automation Router (Phase 5, Step 3): stateful workflow graph.

Architecturally inspired by agentic graph frameworks: every incoming task
flows through explicit nodes and each transition is recorded in a trace.

Graph::

    incoming task
         |
    [Task Classifier Node] --> route in {tool, rag, slm} (+ mode hint),
         |                     or flags out-of-domain / contradictory input
         v
    [Risk Evaluation Node] --> low confidence, unknown or high-risk tools
         |                     divert to the human review queue
         v
    [slm | rag | tool] -----> leaf action nodes (constrained JSON decoding,
         |                    grounded RAG, sandboxed tool execution)
         v
    [Human Review Queue] ----> mock escalation sink

The router is exposed via ``POST /v1/workflow/execute`` in
:mod:`slm.serving.api`.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import torch

from slm.agents.team import ExtractionTeam
from slm.model.constrained import generate_constrained_json
from slm.model.model import ClassifierHead
from slm.rag import DEFAULT_REFUSAL, GroundingGate, Retriever
from slm.serving.cache import SemanticCache
from slm.serving.tracing import workflow_span
from slm.tokenizer.bpe import BPETokenizer
from slm.tools import ToolExecutor, build_default_registry

EXTRACTION_SCHEMA: dict[str, str] = {
    "invoice_id": "str",
    "customer": "str",
    "amount": "str",
    "currency": "str",
    "missing_data": "bool",
}
CATEGORIES = ["billing", "shipping", "technical_support", "sales"]

# ------------------------------------------------------------------ intents

_TOOL_INTENTS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(?:create|issue|raise|generate|file|submit|make)\s+(?:me\s+)?"
            r"(?:an?\s+)?invoice\b",
            re.IGNORECASE,
        ),
        "create_invoice",
    ),
    (re.compile(r"\bescalate\b|\bhuman\s+(?:agent|operator|review)\b", re.IGNORECASE),
     "escalate_to_human"),
    (re.compile(r"\blog\s+(?:a\s+)?note\b", re.IGNORECASE), "log_note"),
]

# Out-of-domain scope guard: creative/off-topic requests never reach the SLM.
_OOD_MARKERS = (
    "haiku", "joke", "hamlet", "football", "lasagna", "swallow",
    "sing me a song", "recipe", "stock should i buy", "airspeed velocity",
)

# Contradiction screen: inconsistent records require human reconciliation.
_CONTRADICTION_MARKERS = (
    " and also ", " but also ", "is both", "are both", "not been sent yet",
    "refuses to pay", "zero outstanding balance",
)

_EXTRACTION_RE = re.compile(
    r"\binv(?:oice)?[ -]?(?:no\.?|#)?\s?\w*\d|\binvoice\b.*\b(?:issued|total|due|amount)\b",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(r"\?")
_KNOWLEDGE_NOUNS = (
    "policy", "payment terms", "refund", "delivery", "warranty",
    "contract", "sla", "lead time", "return",
)
_PERSONAL_RE = re.compile(r"\b(?:i|my|me|we|our)\b", re.IGNORECASE)
_JSON_OBJ_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
_URGENCY_WORDS = ("urgent", "immediately", "asap", "escalate", "furious", "blocked")


@dataclass(slots=True)
class WorkflowResult:
    """Full outcome of one workflow execution, trace included."""

    task: str
    route: str  # "slm" | "rag" | "tool" | "human_review"
    response: Any
    confidence: float
    grounded: bool | None = None
    latency_ms: float = 0.0
    trace: list[dict] = field(default_factory=list)
    workflow_id: str | None = None  # set when paused for human review (Phase 10)

    def to_dict(self) -> dict:
        return asdict(self)


def extract_inline_json(text: str) -> str | None:
    """Return the first ``{...}`` block in ``text`` if any (for tool args)."""
    match = _JSON_OBJ_RE.search(text)
    return match.group(0) if match else None


class WorkflowRouter:
    """Routes business tasks across SLM, RAG, tools, and human review."""

    def __init__(
        self,
        runtime,  # slm.inference.runtime.CPURuntime-compatible
        tokenizer: BPETokenizer,
        *,
        retriever: Retriever | None = None,
        executor: ToolExecutor | None = None,
        grounding_gate: GroundingGate | None = None,
        confidence_threshold: float = 0.55,
        rag_top_k: int = 2,
        agent_team: ExtractionTeam | None = None,
        escalation_log_path: str | Path | None = "artifacts/escalations.jsonl",
        state_store=None,               # slm.agents.state.DurableWorkflowStore
        cache: "SemanticCache | None" = None,
    ) -> None:
        self.runtime = runtime
        self.tokenizer = tokenizer
        self.retriever = retriever or Retriever()
        self.executor = executor or ToolExecutor(build_default_registry())
        self.gate = grounding_gate or GroundingGate()
        self.confidence_threshold = confidence_threshold
        self.rag_top_k = rag_top_k
        self.agent_team = agent_team  # multi-agent peer review (Phase 9)
        self.state_store = state_store  # durable pause/resume (Phase 10)
        self.cache = cache              # semantic response cache (Phase 10)
        self.escalation_log_path = (
            Path(escalation_log_path) if escalation_log_path else None
        )
        self.review_queue: list[dict] = []

    # -------------------------------------------------- node 1: classification

    def classify_task(self, task: str) -> tuple[str, float, str, str]:
        """Return ``(route, confidence, mode, reason)`` from lexical intents."""
        lowered = f" {task.lower()} "

        if extract_inline_json(task):
            return "tool", 0.90, "explicit_payload", "inline JSON arguments detected"

        for pattern, tool_name in _TOOL_INTENTS:
            if pattern.search(task):
                return "tool", 0.90, tool_name, f"matched intent for '{tool_name}'"

        if any(marker in lowered for marker in _OOD_MARKERS):
            return "human_review", 0.95, "out_of_domain", "request outside business scope"

        if any(marker in lowered for marker in _CONTRADICTION_MARKERS):
            return "human_review", 0.85, "contradiction", "inconsistent facts need reconciliation"

        if _EXTRACTION_RE.search(task):
            return "slm", 0.80, "extract", "document-extraction pattern detected"

        is_question = bool(_QUESTION_RE.search(task)) or lowered.strip().startswith(
            ("what", "where", "when", "who", "why", "which", "how",
             "do", "does", "is", "are", "can")
        )
        mentions_knowledge = any(noun in lowered for noun in _KNOWLEDGE_NOUNS)
        if is_question and mentions_knowledge:
            return "rag", 0.75, "knowledge_lookup", "knowledge question matched retrieval scope"
        if is_question:
            return "slm", 0.60, "classify", "customer question handled by SLM"

        if _PERSONAL_RE.search(task):
            return "slm", 0.70, "classify", "customer-service phrasing detected"

        return "slm", 0.45, "classify", "no strong signal; defaulting with low confidence"

    # ----------------------------------------------- node 2: risk evaluation

    def evaluate_risk(
        self, route: str, confidence: float, tool_name: str | None = None
    ) -> tuple[bool, list[str]]:
        """Decide whether the task must be diverted to human review."""
        reasons: list[str] = []
        if confidence < self.confidence_threshold:
            reasons.append("low_confidence")
        if route == "tool" and tool_name:
            level = self.executor.registry.risk_of(tool_name)
            if level is None:
                reasons.append("unknown_tool")
            elif level >= 2:
                reasons.append("high_risk_tool")
        return bool(reasons), reasons

    # ------------------------------------------------------------ orchestration

    def execute(
        self, task: str, *, json_payload: str | dict | None = None, approved: bool = False
    ) -> WorkflowResult:
        with workflow_span(
            "workflow.execute", task_chars=len(task), payload_given=json_payload is not None
        ):
            # ---- Phase 10: semantic cache (idempotent routes only) ----------
            cache_key = json.dumps({"task": task, "payload": json_payload},
                                   sort_keys=True, default=str)
            if self.cache is not None:
                hit, cached = self.cache.get(cache_key)
                if hit and isinstance(cached, dict):
                    cached = dict(cached)
                    cached["trace"] = list(cached.get("trace", [])) + [
                        {"node": "semantic_cache", "hit": True}
                    ]
                    return WorkflowResult(**{
                        **cached,
                        "task": cached.get("task", task),
                    })

            result = self._execute_inner(task, json_payload=json_payload, approved=approved)

            # Never cache side-effectful tool runs or escalations.
            if self.cache is not None and result.route in {"slm", "rag"}:
                self.cache.put(cache_key, result.to_dict())
            return result

    def _execute_inner(self, task: str, *, json_payload: str | dict | None = None,
                       approved: bool = False) -> WorkflowResult:
        started = time.perf_counter()
        trace: list[dict] = []
        self._last_workflow_id = None  # never leak a previous escalation id

        route, confidence, mode, reason = self.classify_task(task)
        trace.append({
            "node": "task_classifier", "route": route,
            "confidence": confidence, "mode": mode, "reason": reason,
        })

        tool_name = mode if route == "tool" else None
        needs_human, risk_reasons = self.evaluate_risk(route, confidence, tool_name)
        trace.append({
            "node": "risk_evaluation", "diverted": needs_human, "reasons": risk_reasons,
        })

        grounded: bool | None = None
        divert = (needs_human or route == "human_review") and not approved
        if divert:
            final_route = "human_review"
            response = self._run_human(task, risk_reasons or [reason])
        elif route == "rag":
            final_route = route
            response, grounded = self._run_rag(task, trace)
        elif route == "tool":
            final_route = route
            response = self._run_tool(task, tool_name or "", json_payload, trace)
        elif route == "human_review":
            # Approved continuation of an escalated out-of-scope request.
            final_route = "slm"
            response = self._run_slm(task, "generate", trace)
        else:
            final_route = route
            response = self._run_slm(task, mode, trace)

        latency = (time.perf_counter() - started) * 1000
        return WorkflowResult(
            task=task, route=final_route, response=response, confidence=confidence,
            grounded=grounded, latency_ms=round(latency, 2), trace=trace,
            workflow_id=getattr(self, "_last_workflow_id", None),
        )

    # ------------------------------------------------------------------- nodes

    def _budget_ids(self, text: str, reserve: int) -> list[int]:
        budget = int(getattr(self.runtime.cfg, "max_position_embeddings", 512)) - reserve
        return self.tokenizer.encode(text)[:max(8, budget)]

    def _run_human(self, task: str, reasons: list[str]) -> dict:
        position = len(self.review_queue) + 1
        record = {
            "ts": time.time(), "task": task, "reasons": reasons,
            "position": position,
        }
        self.review_queue.append(record)
        # ---- Phase 10: durable pause -- serialize state for later resume ----
        workflow_id: str | None = None
        if self.state_store is not None:
            workflow_id = f"wf-{uuid.uuid4().hex[:12]}"
            try:
                self.state_store.save(workflow_id, {
                    "task": task,
                    "reasons": reasons,
                    "status": "awaiting_human_review",
                    "created_ts": record["ts"],
                })
                record["workflow_id"] = workflow_id
            except Exception as exc:  # noqa: BLE001 - durability must not break flow
                record["workflow_error"] = str(exc)
        # Persist for the offline distillation pipeline (best-effort append).
        if self.escalation_log_path is not None:
            try:
                self.escalation_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.escalation_log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError:
                pass  # never let logging break the request path
        self._last_workflow_id = workflow_id
        return {
            "status": "queued_for_human_review",
            "queue_position": position,
            "reasons": reasons,
            **({"workflow_id": workflow_id} if workflow_id else {}),
        }

    def resume(self, workflow_id: str, decision: str) -> WorkflowResult:
        """Continue a paused (human-review) workflow after a reviewer verdict.

        Loads the serialized graph state from the durable store, injects the
        human's decision into the trace, and continues execution. ``approve``
        bypasses further risk diversion; ``reject`` terminates the workflow.
        """
        if self.state_store is None:
            raise RuntimeError("no durable state store configured")
        state = self.state_store.load(workflow_id)
        if state is None:
            raise KeyError(f"unknown workflow_id: {workflow_id}")

        decision_norm = decision.strip().lower()
        with workflow_span(
            "workflow.resume", workflow_id=workflow_id, decision=decision_norm
        ):
            if decision_norm in {"reject", "rejected", "deny"}:
                self.state_store.update_status(
                    workflow_id, "rejected_by_reviewer"
                )
                return WorkflowResult(
                    task=state.get("task", ""),
                    route="human_review",
                    response={
                        "status": "rejected_by_reviewer",
                        "workflow_id": workflow_id,
                    },
                    confidence=1.0,
                    trace=[
                        {"node": "resume", "state": "restored"},
                        {"node": "human_decision", "decision": decision_norm},
                    ],
                )
            # Approved (or custom note): continue the original intent without
            # another risk diversion -- a human has already signed off.
            result = self.execute(state.get("task", ""), approved=True)
            result.trace.insert(0, {"node": "resume", "state": "restored"})
            result.trace.append(
                {"node": "human_decision", "decision": decision_norm}
            )
            self.state_store.update_status(workflow_id, "completed")
            result.workflow_id = workflow_id
            return result

    def _run_slm(self, task: str, mode: str, trace: list[dict]) -> dict:
        if mode == "extract":
            if self.agent_team is not None:
                # Multi-agent peer review: Extractor -> Validator (bounded revisions).
                verdict = self.agent_team.run(task)
                stages = [s["node"] + ("" if s["ok"] else " (rejected)") for s in verdict.stages]
                trace.append({
                    "node": "agent_team", "status": verdict.route_meta,
                    "attempts": verdict.attempts,
                    "latency_ms": verdict.total_latency_ms, "stages": stages,
                })
                return {
                    "extraction": verdict.record or {},
                    "team_status": verdict.route_meta,
                    "violations": verdict.violations,
                }

            ids = self._budget_ids(task, reserve=160)
            raw = generate_constrained_json(
                self.runtime.model, self.tokenizer, ids, EXTRACTION_SCHEMA,
                max_new_tokens=160,
            )
            data = json.loads(raw)  # state machine guarantees validity
            trace.append({"node": "slm_extract", "schema_keys": list(EXTRACTION_SCHEMA)})
            return {"extraction": data}

        if mode == "classify":
            ids = self._budget_ids(task, reserve=0)
            head = ClassifierHead(self.runtime.model, num_classes=len(CATEGORIES))
            head.eval()
            with torch.no_grad():
                probs = torch.softmax(head(torch.tensor([ids]))[0], dim=-1)
            scores = {cat: round(float(p), 4) for cat, p in zip(CATEGORIES, probs.tolist())}
            category = max(scores, key=scores.get)
            lowered = task.lower()
            priority = "high" if any(w in lowered for w in _URGENCY_WORDS) else "normal"
            trace.append({"node": "slm_classify", "scores": scores})
            return {
                "classification": {"category": category, "priority": priority},
                "scores": scores,
            }

        ids = self._budget_ids(task, reserve=48)
        new_ids = self.runtime.generate_ids(ids, max_new_tokens=48)[0]
        trace.append({"node": "slm_generate", "new_tokens": len(new_ids)})
        return {"generation": self.tokenizer.decode(new_ids)}

    def _run_rag(self, task: str, trace: list[dict]) -> tuple[Any, bool]:
        contexts = self.retriever.retrieve_texts(task, k=self.rag_top_k)
        trace.append({"node": "retriever", "contexts": len(contexts)})
        if not contexts:
            return {"status": "no_retrieval_data"}, False

        # Attempt generative answering, verified by the grounding gate...
        ids = self._budget_ids(
            f"Context: {contexts[0]}\nQuestion: {task}\nAnswer:", reserve=48
        )
        candidate = self.tokenizer.decode(
            self.runtime.generate_ids(ids, max_new_tokens=48)[0]
        )
        answer, result = self.gate.guard(candidate, contexts)

        # ...fall back to an extractive sentence if the gate rejects it.
        if not result.grounded:
            fallback = self._extractive_answer(task, contexts)
            answer, result = self.gate.guard(fallback, contexts)

        if not result.grounded:
            trace.append({"node": "grounding_gate", "outcome": "suppressed"})
            return {"answer": DEFAULT_REFUSAL, "grounded": False}, False

        trace.append({"node": "grounding_gate", "support": result.support})
        return {"answer": answer, "context_doc": contexts[0][:120]}, True

    @staticmethod
    def _extractive_answer(query: str, contexts: list[str]) -> str:
        """Best-overlapping sentence from the retrieved evidence."""
        qterms = set(re.findall(r"[a-z0-9]+", query.lower()))
        best_sentence, best_score = "", -1
        for ctx in contexts:
            for sentence in re.split(r"(?<=[.!?])\s+", ctx):
                terms = set(re.findall(r"[a-z0-9]+", sentence.lower()))
                overlap = len(qterms & terms)
                if overlap > best_score:
                    best_sentence, best_score = sentence, overlap
        return best_sentence

    def _run_tool(
        self, task: str, tool_name: str, payload: str | dict | None, trace: list[dict]
    ) -> dict:
        spec = self.executor.registry.get(tool_name)
        if spec is None:
            return {"status": "rejected", "error": "unknown_tool", "tool": tool_name}
        args = payload if payload is not None else extract_inline_json(task)
        if args is None:
            # No explicit arguments: constrained decoding fills the schema.
            ids = self._budget_ids(task, reserve=96)
            args = generate_constrained_json(
                self.runtime.model, self.tokenizer, ids, spec.schema, max_new_tokens=96
            )
        result = self.executor.execute_json(tool_name, args)
        trace.append({
            "node": "tool_execution", "tool": tool_name,
            "ok": result.ok, "error": result.error,
        })
        if result.ok:
            return {"status": "executed", "tool": tool_name, "output": result.output}
        return {"status": "rejected", "tool": tool_name,
                "error": result.error, "detail": result.detail}


__all__ = ["WorkflowResult", "WorkflowRouter", "extract_inline_json"]