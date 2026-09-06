"""Production HTTP API over the CPU inference runtime.

Endpoints:
- ``GET  /health``      : uptime, version, RAM footprint.
- ``POST /v1/generate`` : plain text completion.
- ``POST /v1/extract``  : constrained decoding -> guaranteed-valid JSON.
- ``POST /v1/classify`` : ClassifierHead routing with label scores.

The runtime loads eagerly in :func:`create_app` so misconfiguration fails fast.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

import torch
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from slm.data.flywheel import FlywheelManager
from slm.inference.runtime import CPURuntime, ram_rss_mb
from slm.model.config import BASELINE_3_6M  # noqa: F401  (version provenance)
from slm.model.constrained import generate_constrained_json
from slm.model.model import ClassifierHead
from slm.rag import Retriever
from slm.agents.state import DurableWorkflowStore
from slm.serving.ab_router import ABOrchestrator, TrafficSplitter
from slm.serving.cache import SemanticCache
from slm.serving.router import WorkflowRouter
from slm.serving.security import guard_payload, require_api_key
from slm.serving.telemetry import (
    LatencyMiddleware,
    metrics_response,
    observe_extraction_failure,
    observe_generation,
    observe_router_decision,
)
from slm.serving.tracing import setup_tracing
from slm.tokenizer.bpe import BPETokenizer
from slm.tools import ToolExecutor, build_default_registry

VERSION = "0.1.0"
DEFAULT_CHECKPOINT = os.environ.get(
    "SLM_CHECKPOINT", "artifacts/smoke_run/run/checkpoints/final.pt"
)
DEFAULT_TOKENIZER = os.environ.get("SLM_TOKENIZER", "artifacts/smoke_run/tokenizer")
DEFAULT_CORPUS = os.environ.get(
    "SLM_CORPUS", "artifacts/smoke_run/corpus/business_notes.txt"
)
_START_TIME = time.time()


# ------------------------------------------------------------------- schemas

class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    max_new_tokens: int = Field(default=48, ge=1, le=256)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_k: int | None = Field(default=None, ge=1)


class GenerateResponse(BaseModel):
    text: str
    new_tokens: int


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1)
    schema_: dict[str, Literal["str", "num", "bool", "null"]] = Field(alias="schema")
    max_new_tokens: int = Field(default=160, ge=8, le=512)

    model_config = {"populate_by_name": True}


class ExtractResponse(BaseModel):
    data: dict
    raw: str


class ClassifyRequest(BaseModel):
    text: str = Field(min_length=1)
    labels: list[str] = Field(min_length=2)


class ClassifyResponse(BaseModel):
    label: str
    scores: dict[str, float]


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    model_params: int
    quantized: bool
    ram_rss_mb: float


class WorkflowRequest(BaseModel):
    task: str = Field(min_length=1)
    json_payload: dict | None = None


class FeedbackRequest(BaseModel):
    """User feedback powering the Data Flywheel (Phase 9)."""

    task: str = Field(min_length=1)
    route: str | None = None
    original_response: dict | str | None = None
    corrected_output: dict | str | None = None
    rating: Literal["up", "down"] | None = None


class FeedbackResponse(BaseModel):
    accepted: bool
    record_id: str
    kind: str


class WorkflowResponse(BaseModel):
    route: str
    response: dict | str | None
    confidence: float
    grounded: bool | None = None
    latency_ms: float
    trace: list[dict]


# ------------------------------------------------------------------ app factory

def create_app(
    checkpoint: str | Path | None = None,
    *,
    tokenizer_dir: str | Path | None = None,
    quantize: bool = False,
) -> FastAPI:
    """Build the FastAPI app; loads model + tokenizer eagerly."""
    runtime = CPURuntime(checkpoint or DEFAULT_CHECKPOINT, quantize=quantize)
    tokenizer = BPETokenizer.load(tokenizer_dir or DEFAULT_TOKENIZER)

    # Phase 9: distributed tracing (JSON-lines to stdout) + Data Flywheel store.
    setup_tracing()
    flywheel = FlywheelManager(os.environ.get(
        "SLM_FLYWHEEL_PATH", "artifacts/flywheel_dataset.jsonl"
    ))

    # Phase 10: semantic cache + durable workflow state.
    cache = SemanticCache(
        similarity_threshold=float(os.environ.get("SLM_CACHE_THRESHOLD", "0.98")),
        ttl_seconds=float(os.environ.get("SLM_CACHE_TTL_SECONDS", "3600")),
    )
    state_store = DurableWorkflowStore(os.environ.get(
        "SLM_STATE_DB", "artifacts/workflow_state.db"
    ))

    # Workflow graph: RAG index over the business corpus + default toolset.
    retriever = Retriever()
    corpus = Path(DEFAULT_CORPUS)
    if corpus.exists():
        paragraphs = [
            p.strip() for p in corpus.read_text(encoding="utf-8").split("\n\n")
            if len(p.strip()) >= 40
        ][:200]
        if paragraphs:
            retriever.add_documents([
                {"doc_id": f"corpus-{i:03d}", "text": p}
                for i, p in enumerate(paragraphs)
            ])
    workflow_router = WorkflowRouter(
        runtime, tokenizer, retriever=retriever,
        executor=ToolExecutor(build_default_registry(), audit_path="artifacts/tool_audit.jsonl"),
        cache=cache, state_store=state_store,
    )

    # Phase 10: optional candidate router for A/B testing / shadow mode.
    ab_orchestrator: ABOrchestrator | None = None
    candidate_ckpt = os.environ.get("SLM_CANDIDATE_CHECKPOINT", "")
    if candidate_ckpt and Path(candidate_ckpt).exists():
        try:
            cand_runtime = CPURuntime(candidate_ckpt)
            cand_router = WorkflowRouter(
                cand_runtime, tokenizer, retriever=retriever,
                executor=ToolExecutor(build_default_registry()),
            )
            ab_orchestrator = ABOrchestrator(
                workflow_router, cand_router,
                splitter=TrafficSplitter(
                    mode=os.environ.get("SLM_AB_MODE", "off"),
                    candidate_share=float(os.environ.get("SLM_AB_SPLIT", "0.1")),
                ),
                shadow_log_path=os.environ.get(
                    "SLM_AB_SHADOW_LOG", "artifacts/ab_shadow_log.jsonl"
                ),
            )
            print(f"[ab] candidate loaded: {candidate_ckpt} "
                  f"(mode={ab_orchestrator.splitter.mode})")
        except Exception as exc:  # noqa: BLE001 - A/B must never break serving
            print(f"[ab] candidate disabled ({exc})")
            ab_orchestrator = None

    app = FastAPI(title="SLM Business Engine", version=VERSION)
    app.add_middleware(LatencyMiddleware)

    def _ids_or_400(text: str, extra_tokens: int) -> list[int]:
        ids = tokenizer.encode(text)
        budget = runtime.cfg.max_position_embeddings
        if len(ids) + extra_tokens > budget:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"context length exceeded: prompt {len(ids)} tokens "
                    f"+ {extra_tokens} requested > {budget}"
                ),
            )
        return ids

    # ---------------------------------------------------------------- routes

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=VERSION,
            uptime_seconds=round(time.time() - _START_TIME, 2),
            model_params=runtime.num_params,
            quantized=runtime.quantized,
            ram_rss_mb=round(ram_rss_mb(), 2),
        )

    @app.get("/metrics")
    def metrics() -> Response:
        payload, content_type = metrics_response()
        return Response(content=payload, media_type=content_type)

    @app.post(
        "/v1/generate",
        response_model=GenerateResponse,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def generate(req: GenerateRequest) -> GenerateResponse:
        ids = _ids_or_400(req.prompt, req.max_new_tokens)
        t_start = time.perf_counter()
        ttft: float | None = None
        new_ids: list[int] = []
        try:
            for tid in runtime.generate_ids_iter(
                ids, max_new_tokens=req.max_new_tokens,
                temperature=req.temperature, top_k=req.top_k, eos_id=EOS_ID,
            ):
                if ttft is None:
                    ttft = time.perf_counter() - t_start
                new_ids.append(tid)
        except ValueError as exc:  # e.g. sequence longer than context
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        elapsed = time.perf_counter() - t_start
        observe_generation(len(new_ids), elapsed, ttft)
        return GenerateResponse(
            text=tokenizer.decode(new_ids), new_tokens=len(new_ids)
        )

    @app.post(
        "/v1/extract",
        response_model=ExtractResponse,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def extract(req: ExtractRequest) -> ExtractResponse:
        ids = _ids_or_400(req.text, req.max_new_tokens)
        raw = generate_constrained_json(
            runtime.model, tokenizer, ids, dict(req.schema_),
            max_new_tokens=req.max_new_tokens,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:  # pragma: no cover - machine guarantees
            observe_extraction_failure("invalid_json")
            raise HTTPException(status_code=500, detail=f"decoder produced invalid JSON: {exc}") from exc
        missing = [k for k in req.schema_ if k not in data]
        if missing:
            observe_extraction_failure("schema_violation")
        return ExtractResponse(data=data, raw=raw)

    @app.post(
        "/v1/classify",
        response_model=ClassifyResponse,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def classify(req: ClassifyRequest) -> ClassifyResponse:
        ids = _ids_or_400(req.text, 0)
        head = ClassifierHead(runtime.model, num_classes=len(req.labels))
        head.eval()
        idx = torch.tensor([ids], dtype=torch.long)
        with torch.no_grad():
            logits = head(idx)[0]
            probs = torch.softmax(logits, dim=-1)
        scores = {label: round(float(p), 4) for label, p in zip(req.labels, probs)}
        best = max(scores, key=scores.get)
        return ClassifyResponse(label=best, scores=scores)

    @app.post(
        "/v1/workflow/execute",
        response_model=WorkflowResponse,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def workflow_execute(req: WorkflowRequest) -> WorkflowResponse:
        if ab_orchestrator is not None:
            result, _variant = ab_orchestrator.execute(
                req.task, json_payload=req.json_payload
            )
        else:
            result = workflow_router.execute(req.task, json_payload=req.json_payload)
        observe_router_decision(result.route)
        if result.route == "tool":
            trace_tool = next(
                (t for t in result.trace if t.get("node") == "tool_execution"), {}
            )
            if trace_tool.get("error") == "schema_violation":
                observe_extraction_failure("schema_violation")
            elif trace_tool.get("error") not in (None, "unknown_tool", "unauthorized_tool"):
                observe_extraction_failure(trace_tool["error"])
        payload = result.to_dict()
        response = payload["response"]
        if not isinstance(response, (dict, str)):
            response = str(response)
        return WorkflowResponse(
            route=payload["route"],
            response=response,
            confidence=payload["confidence"],
            grounded=payload["grounded"],
            latency_ms=payload["latency_ms"],
            trace=payload["trace"],
        )

    @app.post(
        "/v1/feedback",
        response_model=FeedbackResponse,
        status_code=202,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def feedback(req: FeedbackRequest) -> FeedbackResponse:
        """Data Flywheel: accept corrections / ratings for future LoRA runs."""
        try:
            record = flywheel.submit(
                req.task,
                route=req.route,
                original_output=req.original_response,
                corrected_output=req.corrected_output,
                rating=req.rating,
                source="user",
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FeedbackResponse(accepted=True, record_id=record.id, kind=record.kind)

    class ResumeRequest(BaseModel):
        decision: str = Field(min_length=1)  # "approve" / "reject" / custom note

    @app.post(
        "/v1/workflow/resume/{workflow_id}",
        response_model=WorkflowResponse,
        dependencies=[Depends(require_api_key), Depends(guard_payload)],
    )
    def workflow_resume(workflow_id: str, req: ResumeRequest) -> WorkflowResponse:
        """Phase 10: resume a durable human-review pause after a decision."""
        try:
            result = workflow_router.resume(workflow_id, req.decision)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        observe_router_decision(result.route)
        payload = result.to_dict()
        response = payload["response"]
        if not isinstance(response, (dict, str)):
            response = str(response)
        return WorkflowResponse(
            route=payload["route"],
            response=response,
            confidence=payload["confidence"],
            grounded=payload["grounded"],
            latency_ms=payload["latency_ms"],
            trace=payload["trace"],
        )

    return app


EOS_ID = 2  # slm.tokenizer.bpe.EOS

__all__ = ["create_app", "VERSION"]

