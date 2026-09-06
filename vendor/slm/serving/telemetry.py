"""Production telemetry: Prometheus metrics for the inference ecosystem.

Lightweight, open-source only (``prometheus_client``) -- no APM agents.
Metrics exposed on ``GET /metrics``:

- ``slm_request_latency_seconds``  -- per-endpoint HTTP latency histogram.
- ``slm_router_decisions_total``   -- counter by route (slm/rag/tool/human_review).
- ``slm_inference_tokens_per_second`` -- generation throughput histogram.
- ``slm_ttft_seconds``             -- time-to-first-token histogram.
- ``slm_extraction_failures_total``-- constrained-JSON / schema failures.

All metrics live in the default registry as singletons, so multiple app
instances within one process share them safely.
"""

from __future__ import annotations

import time

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

ROUTER_DECISIONS = Counter(
    "slm_router_decisions_total",
    "WorkflowRouter route selections",
    ["route"],
)
TOKENS_PER_SECOND = Histogram(
    "slm_inference_tokens_per_second",
    "Generation throughput observed at the API layer",
    buckets=(1, 5, 10, 15, 25, 40, 60, 100, 200),
)
TTFT_SECONDS = Histogram(
    "slm_ttft_seconds",
    "Time to first generated token",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)
EXTRACTION_FAILURES = Counter(
    "slm_extraction_failures_total",
    "Constrained JSON / schema validation failures",
    ["reason"],
)
REQUEST_LATENCY = Histogram(
    "slm_request_latency_seconds",
    "HTTP request latency by endpoint and status",
    ["endpoint", "status"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def observe_router_decision(route: str) -> None:
    ROUTER_DECISIONS.labels(route=route).inc()


def observe_generation(new_tokens: int, elapsed_seconds: float, ttft_seconds: float | None) -> None:
    """Record throughput and (optionally) TTFT for one generation call."""
    if new_tokens > 0 and elapsed_seconds > 0:
        TOKENS_PER_SECOND.observe(new_tokens / elapsed_seconds)
    if ttft_seconds is not None:
        TTFT_SECONDS.observe(ttft_seconds)


def observe_extraction_failure(reason: str) -> None:
    EXTRACTION_FAILURES.labels(reason=reason).inc()


def metrics_response() -> tuple[bytes, str]:
    """Render the Prometheus exposition payload for ``GET /metrics``."""
    return generate_latest(), CONTENT_TYPE_LATEST


class LatencyMiddleware:
    """ASGI middleware recording ``slm_request_latency_seconds`` per request."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "unknown")
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            # Skip the scrape endpoint itself to avoid feedback loops.
            if path != "/metrics":
                REQUEST_LATENCY.labels(
                    endpoint=path, status=str(status_holder["status"])
                ).observe(elapsed)


__all__ = [
    "EXTRACTION_FAILURES",
    "REQUEST_LATENCY",
    "ROUTER_DECISIONS",
    "TTFT_SECONDS",
    "TOKENS_PER_SECOND",
    "LatencyMiddleware",
    "metrics_response",
    "observe_extraction_failure",
    "observe_generation",
    "observe_router_decision",
]