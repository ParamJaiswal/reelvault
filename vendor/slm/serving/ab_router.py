"""A/B testing and shadow-mode traffic splitting (Phase 10, Step 3).

Safely evaluates flywheel-generated candidate models against production:

- **Split mode** -- a deterministic hash-bucket splitter routes a configured
  share of traffic to the candidate (stable per task text, so retries land
  on the same variant).
- **Shadow mode** -- 100% of users get the production answer while every
  request is *asynchronously* replayed on the candidate; output differences,
  route decisions and latency deltas are appended to an audit JSONL.
- **Safety**: any candidate exception or malformed result falls back to the
  production router -- users are never exposed to candidate failures.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_MODES = {"off", "shadow", "split"}


@dataclass(slots=True)
class TrafficSplitter:
    """Deterministic percentage-based task-to-variant assignment."""

    candidate_share: float = 0.0   # fraction 0.0..1.0 routed to candidate
    mode: str = "off"
    salt: str = "slm-ab-v1"

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
        if not 0.0 <= self.candidate_share <= 1.0:
            raise ValueError("candidate_share must be within [0, 1]")

    def pick(self, task: str) -> str:
        if self.mode != "split" or self.candidate_share <= 0.0:
            return "production"
        digest = hashlib.sha256(f"{self.salt}:{task}".encode()).hexdigest()
        bucket = int(digest[:16], 16) % 10_000 / 10_000.0
        return "candidate" if bucket < self.candidate_share else "production"


class ShadowDiffLogger:
    """Append-only JSONL log of production-vs-candidate differences."""

    def __init__(self, path: str | Path = "artifacts/ab_shadow_log.jsonl") -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def log(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    @staticmethod
    def summarize_diff(prod_result, cand_result) -> dict[str, Any]:
        return {
            "route_match": prod_result.route == cand_result.route,
            "prod_route": prod_result.route,
            "cand_route": cand_result.route,
            "latency_delta_ms": round(
                cand_result.latency_ms - prod_result.latency_ms, 2
            ),
            "output_match": _outputs_equal(prod_result.response, cand_result.response),
        }


def _outputs_equal(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, default=str) == \
               json.dumps(b, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - comparison must never throw
        return False


class ABOrchestrator:
    """Routes tasks between production/candidate routers with safety fallback."""

    def __init__(
        self,
        production_router,
        candidate_router=None,
        *,
        splitter: TrafficSplitter | None = None,
        shadow_log_path: str | Path = "artifacts/ab_shadow_log.jsonl",
        max_shadow_workers: int = 4,
    ) -> None:
        self.production = production_router
        self.candidate = candidate_router
        self.splitter = splitter or TrafficSplitter()
        self.shadow_logger = ShadowDiffLogger(shadow_log_path)
        self._semaphore = threading.Semaphore(max_shadow_workers)
        self.stats = {
            "served_production": 0,
            "served_candidate": 0,
            "shadow_runs": 0,
            "fallbacks": 0,
            "shadow_diffs": 0,
        }
        self._stats_lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def execute(
        self, task: str, *, json_payload: str | dict | None = None
    ) -> tuple[Any, str]:
        """Execute via the appropriate router; returns ``(result, served_variant)``."""
        if self.candidate is None or self.splitter.mode == "off":
            result = self.production.execute(task, json_payload=json_payload)
            self._bump("served_production")
            return result, "production"

        if self.splitter.mode == "shadow":
            result = self.production.execute(task, json_payload=json_payload)
            self._bump("served_production")
            self._spawn_shadow(task, json_payload, prod_result=result)
            return result, "production"

        # ---- split mode -----------------------------------------------------
        variant = self.splitter.pick(task)
        if variant == "production":
            result = self.production.execute(task, json_payload=json_payload)
            self._bump("served_production")
            return result, "production"

        started = time.perf_counter()
        try:
            cand_result = self.candidate.execute(task, json_payload=json_payload)
            elapsed = (time.perf_counter() - started) * 1000
            if not isinstance(cand_result.route, str) or not cand_result.task:
                raise ValueError("candidate returned malformed result")
            self._bump("served_candidate")
            self.shadow_logger.log({
                "ts": time.time(), "variant": "candidate_served",
                "task_chars": len(task), "route": cand_result.route,
                "latency_ms": round(elapsed, 2),
            })
            return cand_result, "candidate"
        except Exception as exc:  # noqa: BLE001 - mandatory safety fallback
            self._bump("fallbacks")
            self.shadow_logger.log({
                "ts": time.time(), "variant": "candidate_failed_fallback",
                "task_chars": len(task), "error": str(exc)[:300],
            })
            result = self.production.execute(task, json_payload=json_payload)
            self._bump("served_production")
            return result, "production"

    # ---------------------------------------------------------------- internal

    def _spawn_shadow(self, task: str, json_payload: Any, prod_result) -> None:
        def _run_shadow() -> None:
            if not self._semaphore.acquire(timeout=5):
                return  # drop rather than pile up unboundedly
            error: str | None = None
            cand_result = None
            started = time.perf_counter()
            try:
                cand_result = self.candidate.execute(task, json_payload=json_payload)
            except Exception as exc:  # noqa: BLE001 - shadow failures logged only
                error = str(exc)[:300]
            finally:
                self._semaphore.release()
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            with self._stats_lock:
                self.stats["shadow_runs"] += 1
            record: dict[str, Any] = {
                "ts": time.time(), "variant": "shadow",
                "task_chars": len(task), "cand_latency_ms": latency_ms,
            }
            if error:
                record["error"] = error
            elif cand_result is not None:
                with self._stats_lock:
                    self.stats["shadow_diffs"] += 1
                record.update(
                    self.shadow_logger.summarize_diff(prod_result, cand_result)
                )
            self.shadow_logger.log(record)

        threading.Thread(target=_run_shadow, name="ab-shadow", daemon=True).start()

    def _bump(self, key: str) -> None:
        with self._stats_lock:
            self.stats[key] += 1


__all__ = ["ABOrchestrator", "ShadowDiffLogger", "TrafficSplitter", "VALID_MODES"]