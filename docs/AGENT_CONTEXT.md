# Agent Context Cache — ReelVault

**Purpose:** compacted session context for any agent (or human) resuming work.
Written after significant state changes. **Freshness rule:** anything here
older than the last `SESSION_HANDOFF.md` entry must be re-verified before
being trusted (AGENTS.md source-of-truth rules apply).

**Last updated:** 2026-09-07 (session: pipeline reliability + speed pass)

---

## 1. Snapshot

- Project: ReelVault — local-first reel/video → searchable evidence-backed knowledge base
- Repo: `D:\reelvault`, branch `v0-core`, tree clean
- History (linear): `139b26a` p3 proof → `0ff0088` p4 eval → `7290021` p5 corrections → `b642814` p6 setup → `0619b54` p7 selection → `7c35024` cleanup → `9adc2bf` p7 URL-ingest fix → `4e5057f` backups untracked → reliability/speed pass (latest)
- Authoritative docs: `AGENTS.md` (policy) · `docs/SESSION_HANDOFF.md` (verified state) · `docs/PHASE_REPORT.md` (phase evidence) · `docs/EVAL.md` (baseline metrics) · `docs/DECISIONS.md` (11 decisions)

## 2. Verified state (all command-verified, not assumed)

- Tests: **75 passed, 1 skipped** (`pytest tests -q --ignore=tests/test_ai_eval.py`)
- Live eval: **1 passed** (146s); field recall 0.824, malformed 0.0, unsupported-kept 0.157, deadline parse 1.0, ~11.7s/reel LLM
- URL ingest works end-to-end (real reel proven): download → artifacts → evidence → FTS
- Services: app `:8756` (uvicorn, started via `Start-Process` — bash `&` backgrounding dies silently), llama-server `:8091` (start command in AGENTS.md §5)
- venv python: `.venv/Scripts/python.exe` — **no pip module by default** (`ensurepip` works; faster-whisper installed via it)

## 3. Active phase

Phase 6 (usage window open — needs owner: 2 weeks, ≥20 real reels, fill `docs/V0_RETROSPECTIVE.md`) runs in background. Phase 7 = **URL ingest reliability** (selected, in progress). Remaining Phase 7 candidates: caption-less URL reels, bulk-ingest soak (20 reels).

## 4. Key architecture facts (learned the hard way)

- Queue stages: `ingest → media → transcribe → ocr → classify_extract → embed → finalize`; `enqueue()` defaults to `STAGES[1:]` — URL reels must enqueue `ingest` explicitly (adapter's `needs_download` flag drives this in `create_reel_from_request`)
- `PermanentJobError` (queue.py) = dead-letter on attempt 1; `PermanentMediaError` subclasses both it and `MediaError` (handler compat)
- On retryable failure, downstream jobs of same reel get `run_after` pushed to ≥ failed job's next attempt (retry-storm guard)
- `stage_media` purges stale artifacts keyed by bare reel id (audio/thumb/frames) — shared media dir across test DBs bit us once (stale `r6.wav`)
- Evidence floor: `MIN_QUOTE_CHARS=15`, drop below 0.45 similarity; user facts tagged `user_corrected=1` / `evidence_source='user'`
- faster-whisper needs CUDA DLLs from `llamacpp/` dir — `WhisperProvider._register_cuda_dlls()` handles it; without it CUDA fails AND poisons in-process CPU fallback
- Windows: bash `&` backgrounding of uvicorn dies — use PowerShell `Start-Process -PassThru`; `grep` tool include_pattern unreliable — use terminal `grep -rn`
- `backups/` is runtime output, gitignored
- Orphans removed: `v0/`, `tests_v0/`, `COMPLETION_PLAN.md` (do not resurrect)

## 5. Pitfalls (tried, failed)

- `confidence_score` 0.665 rounds to 0.66 — don't assert 0.67
- `jobs.created_at/updated_at/run_after` are NOT NULL — insert them in test fixtures
- `test_pipeline.py` leaks `settings.retry_backoff_s = 0` globally — pin settings with monkeypatch in queue tests
- StageCancelled vs PermanentJobError: `stage_media` converts permanent media errors to StageCancelled (after marking reel failed) — expect StageCancelled at that boundary
- Whisper on 440Hz sine/testsrc: VAD yields 0 segments on music-like content — fine, not a regression
- Console: use `python -X utf8` for emoji-laden output (segments contain 🎵)

## 6. Commands cheat sheet

```powershell
# suite (fast)
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_ai_eval.py
# live eval (needs llama-server up)
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_ai_eval.py -q -s
# pipeline bench
.venv\Scripts\python.exe -X utf8 scripts\bench_pipeline.py
# live proof (needs llama-server)
$env:RV_PHASE3=1; pytest tests/test_phase3_pipeline_proof.py -v
# services
curl http://127.0.0.1:8756/healthz
curl http://127.0.0.1:8091/v1/models
```

## 7. Do-not list (frozen until post-v0.1)

Android · Docker · Web Push · Playwright/IG login fallback expansion · Business-SLM anything (`RV_SLM_ENABLED=false`) · new auth features · vector-search expansion · bulk scraping · multi-user. Do not replace: SQLite WAL, durable queue, auth/sessions, llama.cpp/Qwen runtime, logging/redaction, backups, schema/migrations.
