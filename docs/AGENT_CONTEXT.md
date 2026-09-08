# Agent Context Cache — ReelVault

**Purpose:** compacted session context for any agent (or human) resuming work.
Written after significant state changes. **Freshness rule:** anything here
older than the last `SESSION_HANDOFF.md` entry must be re-verified before
being trusted (AGENTS.md source-of-truth rules apply).

**Last updated:** 2026-09-07 (session: bulk-ingest soak + semantic dup fix)

---

## 1. Snapshot

- Project: ReelVault — local-first reel/video → searchable evidence-backed knowledge base
- Repo: `D:\reelvault`, branch `v0-core`, tree clean, HEAD `e877429`
- History (linear): `139b26a` p3 proof → `0ff0088` p4 eval → `7290021` p5 corrections → `b642814` p6 setup → `0619b54` p7 selection → `9adc2bf` p7 URL-ingest fix → `2acf022` reliability+speed → `6cfef0d` soak+dup-guard → `e877429` docs
- Authoritative docs: `AGENTS.md` (policy) · `docs/SESSION_HANDOFF.md` (verified state) · `docs/PHASE_REPORT.md` (phase evidence) · `docs/EVAL.md` (baseline metrics) · `docs/DECISIONS.md` (11 decisions)

## 2. Verified state (all command-verified, not assumed)

- Tests: **76 passed, 1 skipped** (`pytest tests -q --ignore=tests/test_ai_eval.py`)
- Live eval: **1 passed** (146s, faster-whisper active); field recall 0.824, malformed 0.0, unsupported-kept 0.157, deadline parse 1.0, ~11.7s/reel LLM
- URL ingest works end-to-end (real reel proven); bulk soak: 20/20 completed, 0 locks, ~9s/reel, worker alive
- Semantic dup false-merge FIXED: `semantic_duplicate_check` skips summary <40 chars (soak observed 19/20 false merges on content-free clips; was plan.md's flagged 0.93 risk, now measured)
- Services: app `:8756` (restart via PowerShell `Start-Process` — bash `&` backgrounding dies silently), llama-server `:8091` (start cmd in AGENTS.md §5)
- venv python: `.venv/Scripts/python.exe` — pip via `ensurepip` (faster-whisper 1.2.1 installed)

## 3. Active phase

Phase 6 (usage window open — owner task: 2 weeks, ≥20 real reels, fill `docs/V0_RETROSPECTIVE.md`). Phase 7 = URL ingest reliability: core fix + soak DONE. Leftover (optional, only if hit with real URLs): caption-less URL reels. DB has ~50 test/soak reels (ids 1–50) — owner may want purge before real use.

## 4. Key architecture facts (learned the hard way)

- Queue stages: `ingest → media → transcribe → ocr → classify_extract → embed → finalize`; `enqueue()` defaults to `STAGES[1:]` — URL reels must enqueue `ingest` explicitly (adapter's `needs_download` flag drives this in `create_reel_from_request`)
- `PermanentJobError` (queue.py) = dead-letter on attempt 1; `PermanentMediaError` subclasses both it and `MediaError` (handler compat)
- On retryable failure, downstream jobs of same reel get `run_after` pushed to ≥ failed job's next attempt (retry-storm guard)
- `stage_media` purges stale artifacts keyed by bare reel id (audio/thumb/frames) — shared media dir across test DBs bit us once (stale `r6.wav`)
- Evidence floor: `MIN_QUOTE_CHARS=15`, drop below 0.45 similarity; user facts tagged `user_corrected=1` / `evidence_source='user'`
- faster-whisper needs CUDA DLLs from `llamacpp/` dir — `WhisperProvider._register_cuda_dlls()` handles it; without it CUDA fails AND poisons in-process CPU fallback. RTX 3050: 15.8s reel audio transcribes warm in 0.3–2s
- ffmpeg `drawtext` access-violates (0xC0000005) under python subprocess on this fontconfig-less setup — use `-vf hue=...` for clip variety instead
- Windows: bash `&` backgrounding of uvicorn dies — use PowerShell `Start-Process -PassThru`; `grep` tool include_pattern unreliable — use terminal `grep -rn`
- `backups/` is runtime output, gitignored; `.owner_credentials.txt` in data/ holds owner login (read into scripts, never print)
- Orphans removed: `v0/`, `tests_v0/`, `COMPLETION_PLAN.md` (do not resurrect)

## 5. Pitfalls (tried, failed)

- `confidence_score` 0.665 rounds to 0.66 — don't assert 0.67
- `jobs.created_at/updated_at/run_after` are NOT NULL — insert them in test fixtures; `embeddings` needs owner_id/text_used/dim/model too
- `test_pipeline.py` leaks `settings.retry_backoff_s = 0` globally — pin settings with monkeypatch in queue tests
- StageCancelled vs PermanentJobError: `stage_media` converts permanent media errors to StageCancelled (after marking reel failed) — expect StageCancelled at that boundary
- Whisper VAD yields 0 segments on music-like content (440Hz sine/testsrc) — fine, not a regression
- Console: use `python -X utf8` for emoji-laden output (segments contain 🎵)
- Duplicate short-circuit: hash/shortcode paths need distinct bytes/urls; semantic path fires on identical EMBEDDINGS (placeholder summaries) — guarded now

## 6. Commands cheat sheet

```powershell
# suite (fast)
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_ai_eval.py
# live eval (needs llama-server up)
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_ai_eval.py -q -s
# pipeline bench / bulk soak
.venv\Scripts\python.exe -X utf8 scripts\bench_pipeline.py
.venv\Scripts\python.exe -X utf8 scripts\soak_bulk_ingest.py 20
# live proof (needs llama-server)
$env:RV_PHASE3=1; pytest tests/test_phase3_pipeline_proof.py -v
# services
curl http://127.0.0.1:8756/healthz
curl http://127.0.0.1:8091/v1/models
```

## 7. Do-not list (frozen until post-v0.1)

Android · Docker · Web Push · Playwright/IG login fallback expansion · Business-SLM anything (`RV_SLM_ENABLED=false`) · new auth features · vector-search expansion · bulk scraping · multi-user. Do not replace: SQLite WAL, durable queue, auth/sessions, llama.cpp/Qwen runtime, logging/redaction, backups, schema/migrations.
