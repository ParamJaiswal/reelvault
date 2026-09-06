"""Distributed tracing for the multi-agent workflow (Phase 9, Step 4).

OpenTelemetry with a **zero-infrastructure exporter**: finished spans are
printed to stdout as structured JSON lines (one JSON object per line), ready
to be collected by any cluster log pipeline (Fluent Bit, Vector, Loki...) or
shipped onward by a log-based OTLP forwarder. Swapping in a real OTLP
exporter later is a one-line change in :func:`setup_tracing`.

Usage::

    from slm.serving.tracing import setup_tracing, workflow_span

    setup_tracing()                      # once at app startup
    with workflow_span("workflow.execute", task=task) as span_ctx:
        ...                              # child spans via agents' tracer
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

_SERVICE_NAME = "slm-inference"
_tracing_ready = False


class JsonStdoutSpanExporter(SpanExporter):
    """Exports each finished span as one structured-JSON line on stdout."""

    def export(self, spans):
        for span in spans:
            record = {
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%S", time.gmtime()
                ),
                "trace_id": format(span.get_span_context().trace_id, "032x"),
                "span_id": format(span.get_span_context().span_id, "016x"),
                "parent_span_id": format(span.parent.span_id, "016x")
                if span.parent
                else None,
                "name": span.name,
                "kind": str(span.kind).split(".")[-1],
                "start_unix_nano": span.start_time,
                "end_unix_nano": span.end_time,
                "duration_ms": round(
                    (span.end_time - span.start_time) / 1_000_000, 3
                ),
                "attributes": dict(span.attributes or {}),
                "status": str(span.status.status_code).split(".")[-1],
            }
            sys.stdout.write(json.dumps(record, default=str) + "\n")
        sys.stdout.flush()
        return SpanExportResult.SUCCESS


def setup_tracing(*, service_name: str = _SERVICE_NAME, batch: bool = False) -> None:
    """Install the tracer provider with the JSON-stdout exporter (idempotent)."""
    global _tracing_ready
    if _tracing_ready:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    exporter = JsonStdoutSpanExporter()
    provider.add_span_processor(
        BatchSpanProcessor(exporter) if batch else SimpleSpanProcessor(exporter)
    )
    trace.set_tracer_provider(provider)
    _tracing_ready = True


@contextmanager
def workflow_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Root span for one end-to-end request through the workflow graph."""
    tracer = trace.get_tracer("slm.workflow")
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span