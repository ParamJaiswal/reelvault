# Phase Report — Phases 0–2 complete (Sep 6, 2026)

Direction: `plan.md` merged strategy + restored `AGENTS.md` v0.1 plan
(preserve architecture, fix correctness, freeze peripherals, prove on real
data). The lean-rebuild plan is superseded.

---

## Phase 0 — Verify baseline ✅

| Check | Result |
|---|---|
| `pytest tests -q --ignore=tests/test_ai_eval.py` | 40 passed (pre-change baseline) |
| `curl :8756/healthz` | No response — app not running at check time |
| `curl :8091/v1/models` | No response — llama-server not running |
| WSL `docker_build3.log` | Empty / exit 1 — Docker never built |

Conclusion: Aug 26 "verified live" claims could not be re-confirmed live
(services simply not started); suite green; Docker still untested.

## Phase 1 — Correctness fixes ✅ (8 new tests)

| # | Fix | File | Regression test |
|---|---|---|---|
| 1 | Minimum quote floor (`MIN_QUOTE_CHARS = 15`): a probe shorter than the floor returns zero similarity instead of riding `a in b` to 1.0 | `app/knowledge/evidence.py` | `test_short_quote_cannot_score_full_similarity` |
| 2 | Confidence no longer fabricates the 0.25 timestamp weight: `has_timestamp` param, default `False`; caller passes `bool(span and span.t_s is not None)` | `app/knowledge/evidence.py`, `app/pipeline/stages.py` | `test_confidence_without_timestamp_is_lower`, `test_confidence_with_timestamp_keeps_bonus` |
| 3 | Bootstrap token with empty users table returns `None` (→ 401) instead of `AttributeError` (→ 500) | `app/core/auth.py` | `test_bootstrap_token_with_empty_users_returns_none` |
| 4 | Queue claim UPDATE re-checks `heartbeat < stale_cutoff`, closing the steal race between worker B's SELECT and UPDATE; stale reclaim still works | `app/db/queue.py` | `test_queue_claim_guard_blocks_steal_of_refreshed_job`, `test_queue_claim_no_steal_of_fresh_job_and_stale_reclaim` |

## Phase 2 — Configuration portability + security minimum ✅ (6 new tests)

| # | Fix | File | Verification |
|---|---|---|---|
| 1 | `models_dir` / `media_dir` / `backup_dir` defaults now derive from `APP_ROOT` (env overrides unchanged: `RV_MODELS_DIR`, `RV_MEDIA_DIR`, `RV_BACKUP_DIR`) | `app/core/config.py` | `test_default_paths_are_repo_relative` |
| 2 | `WATCH_FOLDER` no longer hardcodes `D:/reelvault/watch`; derives from `settings.media_dir` | `app/api/main.py` | `grep -rn "D:/" app --include="*.py"` → **zero hits** |
| 3 | Startup path validation: raises `RuntimeError` naming the unusable path when data/media/DB dirs are missing or unwritable; warns when `models_dir` is absent | `app/core/config.py` (`validate_startup_paths`) | `test_validate_startup_paths_raises_on_unwritable_db_dir`, `test_validate_startup_paths_warns_on_missing_models` |
| 4 | CORS wired for real (middleware existed nowhere before — dead config): honors `RV_CORS_ORIGINS`; default restricted to `http://127.0.0.1:8756,http://localhost:8756` instead of `*` | `app/core/config.py`, `app/api/main.py` | `test_cors_default_not_wildcard` |
| 5 | Owner-credentials staleness warning at startup when `data/.owner_credentials.txt` still exists | `app/core/config.py`, `app/api/main.py` | `test_owner_credentials_warning_when_file_present`, `test_no_owner_credentials_warning_when_absent` |

## Final verification

```
pytest tests -q --ignore=tests/test_ai_eval.py  →  54 passed (40 baseline + 14 new)
grep -rn "D:/" app --include="*.py"             →  zero hits
```

## Honest caveats

- CORS middleware is NEW: previously no CORS headers were sent at all
  (browser same-origin default). Wiring it with the restricted default is
  strictly tighter than `*`; if you access the API from another origin,
  add it to `RV_CORS_ORIGINS`.
- Confidence scores for facts without a source timestamp are now lower
  (max ~0.75 vs 0.99). Existing DB rows are not rewritten; new extractions
  only.
- The quote floor (15 normalized chars) will drop very short legitimate
  quotes; `dropped_as_hallucination` count in extraction events is the
  place to watch for over-dropping.
- `test_ai_eval.py` (golden eval) is excluded from the suite run per
  AGENTS.md; the evidence floor will affect its metrics on next eval run.
- Lean-rebuild artifacts (`v0/`, `tests_v0/`, branch name `v0-core`) are
  orphaned — recommend deleting in a follow-up commit.

## Next (per plan)

Phase 3 — Core pipeline proof: five real local videos through upload →
durable queue → artifacts → clean failures. Requires starting
llama-server + the app, and `testmedia/` samples or fresh recordings.

## Phase 3 — Core pipeline proof ✅ (live proof test)

**Test:** `tests/test_phase3_pipeline_proof.py` — gated behind `RV_PHASE3=1`
(skipped in normal suite runs; it is a slow live run needing llama-server
and real video files).

**What it proves (6 uploads, ~3.5 min wall time):**
- 5 real reels (job/edu/tool/recipe/fitness) uploaded via
  `POST /api/reels/upload` → durable queue → all stages → `completed`
  with media, thumb, 12 frames, OCR results, transcript segments,
  non-empty summary, and evidence-backed facts (2/4/1/3/5 per reel).
- 1 corrupt upload (garbage bytes) → reel `failed` with readable error,
  its media job dead-lettered, downstream jobs dead-or-deleted (NOT
  `done`), worker stayed alive (verified via `/healthz` + a 7th tiny
  video processed successfully after the corrupt failure).
- FTS search retrieves each reel by its caption keyword.

**Fixes made during proof (uncovered by the live run):**
1. `stage_media` stale-artifact purge — artifacts were keyed by bare
   reel id (`media/audio/r6.wav`); a stale Aug-26 `r6.wav` made a corrupt
   upload inherit another reel's transcript/facts. Purge now removes
   `audio/r{id}.wav`, `thumb_r{id}.jpg`, `frames/{id}/` before extraction;
   fatal `MediaError` marks the reel failed immediately + raises
   `StageCancelled` (closes the retry-window race where downstream jobs
   could still run).
2. Queue dead-letter now marks the reel failed and deletes queued
   downstream jobs (`app/db/queue.py`).
3. `ensure_owner_user()` creates the first owner on a fresh DB instead
   of raising (`app/core/auth.py`).
4. Guard (`_guard`) at top of all 6 downstream stages: cancel if the
   reel is already failed/dead-lettered.
5. Proof-test bug: `_reel_state()` SELECT omitted `summary` and
   `error_message`, so the summary assertion could never pass. Fixed
   (test-code bug, not pipeline bug — DB inspection proved the pipeline
   green before the test fix).

**Verification:**
- `RV_PHASE3=1 pytest tests/test_phase3_pipeline_proof.py -v` →
  1 passed (202.59s), 6 uploads, all assertions green.
- `pytest tests -q --ignore=tests/test_ai_eval.py` → 56 passed, 1 skipped.

**Honest caveats:**
- Transcription used torch-whisper fallback (faster_whisper not
  installed in this venv) — see Phase 1 report caveat.
- Corrupt-input media stage took ~3 min (ffmpeg/ffprobe timeouts on
  garbage bytes) before failing — acceptable for v0.1, could be tuned
  later.
- Test reels: 3 scripted real recordings + 2 SAPI-TTS generated +
  1 ffmpeg-generated tiny video in `testmedia/`.
