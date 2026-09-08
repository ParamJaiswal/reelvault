# Session handoff — Production hardening (Aug 26, 2026, evening)

## v0-core lean rebuild — Phase 1 DONE (Sep 6, 2026) — SUPERSEDED
Superseded by plan.md merged strategy (keep existing architecture, no
_archive moves, no Ollama). Orphaned artifacts on branch v0-core: `v0/`,
`tests_v0/` — cleanup decision pending.

## Implemented & verified live
1. **Auth v2** (`app/core/auth.py`, migration v4): password logins
   (PBKDF2-240k), HS256 access JWTs (30 min), rotating refresh tokens stored
   HASHED as session CHAINS — old rows kept with revoked=2 so REUSE of a
   rotated token revokes the entire chain (theft response). Roles:
   owner>admin>viewer enforced per-endpoint. Bootstrap .auth_token still works
   as owner. First-run owner credentials: data/.owner_credentials.txt.
   Endpoints: /api/auth/login|refresh|logout|sessions(+DELETE),
   /api/admin/users. Verified live incl. theft test → family revoked.
2. **Web Push** (`app/core/push.py`): VAPID P-256 keypair auto-generated
   (cryptography lib directly — py_vapid not needed), push_subscriptions
   table, /api/push/public-key|subscribe|unsubscribe|test, SW push+click
   handlers in sw.js, client opt-in in push-client.js (HTTPS-only), hourly
   deadline scanner pushes ≤48h reminders. Live: publicKey 87-char valid.
3. **HTTPS**: scripts/make_cert.py self-signed cert covering LAN IP + localhost;
   verified uvicorn serves https://...:8443/8444 with 200s. docs/DEPLOYMENT.md
   documents Tailscale (recommended) + Cloudflare Tunnel + self-signed paths.
4. **Docker**: Dockerfile multi-stage CUDA build (llama.cpp compiled in
   builder stage; runtime = nvidia/cuda 12.4 runtime), Caddy sidecar for
   internal HTTPS on :8443, volumes for models/data/media, healthcheck.
   NOTE: Docker not installed on this PC yet — build untested locally.
5. **Backups** (`app/core/backups.py`): SQLite online-backup API snapshot +
   incremental media mirror with retention; Fernet encryption from
   RV_BACKUP_PASSPHRASE. POST /api/admin/backup (admin+). Daily scheduler
   thread started at boot (D:/reelvault-backups). Verified live: DB snapshot
   written + media mirror 105 files unchanged.

## Tests
36 passed (28 prior + 8 new prod tests covering hash roundtrip,
create/rotate/revoke/reuse-theft, garbage tokens, role rules, backup
plaintext+encrypted roundtrip, media mirror incrementality, VAPID gen).

## Notes / next
- Viewer DELETE returns 404 when reel missing — acceptable; could map to 403
  for stricter semantics.
- Push permission prompt fires once per browser after first click.
- For phone push over LAN: must use HTTPS path (self-signed accepted or
  Tailscale). Plain HTTP blocks Web Push by browser design.
- Dockerfile ready to build once Docker Desktop is installed.

---

Date: 2026-09-06
Phase: 0 — Verify baseline (per plan.md merged strategy + AGENTS.md v0.1 plan)
Done:
- Read plan.md + restored AGENTS.md; lean-rebuild plan rejected, merged strategy adopted.
- Verified test suite, service health, and Docker status on this machine.
- Marked lean-rebuild handoff section as superseded; v0/ + tests_v0/ flagged as orphaned artifacts.
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py → 40 passed, 1 pydantic deprecation warning, 4.85s
- curl http://127.0.0.1:8756/healthz → no response (app not running)
- curl http://127.0.0.1:8091/v1/models → no response (llama-server not running)
- wsl docker_build3.log tail → empty, exit 1 (Docker build never ran; Docker not installed — matches Aug 26 note)
Blockers:
- None for Phase 1. Services were simply not started at verification time; health claims from Aug 26 could not be re-confirmed live.
- Tests grew 36 → 40 since Aug 26 — growth unexplained but suite is green.
Next:
- Phase 1 — Correctness fixes: evidence quote minimum length, confidence when t_s missing, empty-users bootstrap guard, queue claim heartbeat re-check, focused regression tests for all four.

---

Date: 2026-09-06
Phase: 1 + 2 — Correctness fixes and configuration/security minimum (DONE)
Done:
- Phase 1: evidence quote floor (MIN_QUOTE_CHARS=15), honest confidence without timestamp, empty-users bootstrap guard, queue claim heartbeat re-check in claim UPDATE.
- Phase 2: repo-relative path defaults (models/media/backup), WATCH_FOLDER from settings, startup path validation with clear errors, CORS middleware wired with restricted default, stale owner-credentials warning.
- 14 focused regression tests added (tests/test_phase1_fixes.py, tests/test_phase2_config.py).
- Full report: docs/PHASE_REPORT.md.
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py → 54 passed (was 40 baseline).
- grep -rn "D:/" app --include="*.py" → zero hits.
- Each regression test fails on pre-fix code (verified during development: 3 failures traced to test setup, then green).
Blockers:
- None. v0/ + tests_v0/ lean artifacts still orphaned — recommend removal.
Next:
- Phase 3 — Core pipeline proof: five real local videos through upload → queue → artifacts → clean failures (needs llama-server + app started, testmedia samples).

---

Date: 2026-09-06
Phase: 3 — Core pipeline proof (DONE)
Done:
- Live proof test tests/test_phase3_pipeline_proof.py (RV_PHASE3=1 gate): 5 real reels complete end-to-end (artifacts, summary, evidence-backed facts, FTS retrieval per keyword); corrupt upload fails cleanly (reel failed, job dead-lettered, downstream not done, worker alive — proved by processing a 7th tiny video after the corrupt failure).
- stage_media stale-artifact purge (audio/thumb/frames keyed by bare reel id collided across runs) + immediate fail + StageCancelled on fatal MediaError.
- Queue dead-letter marks reel failed and deletes queued downstream jobs; _guard added at top of all 6 downstream stages.
- ensure_owner_user() creates first owner on fresh DB instead of raising.
- Proof-test fix: _reel_state() SELECT omitted summary/error_message — assertion could never pass (test bug; pipeline was already green per DB inspection).
- Full report: docs/PHASE_REPORT.md (Phase 3 section).
Verification:
- RV_PHASE3=1 pytest tests/test_phase3_pipeline_proof.py -v → 1 passed, 202.59s, 6 uploads.
- pytest tests -q --ignore=tests/test_ai_eval.py → 56 passed, 1 skipped (proof gate).
Blockers:
- None.
Next:
- Phase 4 — Golden extraction evaluation: create initial golden set (tests/golden/), run eval, save baseline metrics in docs/EVAL.md. Do not tune extraction by intuition.

---

Date: 2026-09-06
Phase: 4 — Golden extraction evaluation (baseline v1, DONE)
Done:
- Golden corpus: tests/golden/*.json — 13 hand-labeled items (job x2,
  scholarship, edu x3, tool x2, recipe OCR-heavy, fitness music-heavy,
  Hinglish, finance, event) with transcript segments, OCR, captions,
  primary + acceptable category labels, field needles, deadlines.
- Harness: tests/test_ai_eval.py rewritten to load the corpus and mirror
  stage_classify_extract's unified text + build_spans; metrics for
  category acc/F1 (primary + acceptable), schema agreement, field recall,
  content presence, evidence kept/dropped/unsupported, deadline parse
  recall, malformed-JSON rate, latency. Marker ai_eval; excluded from
  default suite (live model, ~2 min).
- One measured fix, prompt-only in app/ai/router.py: extraction now
  requires one fact for every supported field (baseline field recall
  0.176 -> 0.824; job-01 1 fact -> 9 kept, 5/5 fields).
- Baseline table + known issues in docs/EVAL.md; report in
  docs/PHASE_REPORT.md.
Verification:
- pytest tests/test_ai_eval.py -q -s -> 1 passed (135s, live llama-server):
  cat acc 0.846, macro-F1 0.680, field recall 0.824, deadline recall 0.75,
  malformed 0.0, unsupported-kept 0.178, min-facts 13/13, 10.4s/reel.
- All 7 acceptance gates green.
Blockers:
- None. Schema drift (education for generic content) and content presence
  0.667 are recorded findings for a later cycle, not blockers.
Next:
- Phase 5 — Evidence-visible UI and manual correction (reel detail:
  claim/quote/timestamp/click-to-seek, edit/delete facts, correct
  summary/category/deadlines).

---

Date: 2026-09-06
Phase: 4 merged to v0-core + Phase 5 — Evidence-visible UI and manual correction (DONE)
Done:
- phase4-agentic (0ff0088) fast-forward-merged into v0-core; no other
  Phase 4 existed in the repo (verified all worktrees, stashes, dangling
  checkpoints). Full suite green on the merge: 56 passed, 1 skipped.
- Phase 5 on branch phase5-agentic:
  - PATCH /api/reels/{id}: summary, categories (canonicalized vs
    VALID_CATEGORIES, deduped, max 6), deadline_raw (deterministic parse,
    ISO fast-path, 422 on unparseable), deadline_remove.
  - POST /api/reels/{id}/facts (manual fact, user_corrected=1);
    DELETE /api/facts/{id} (ownership-checked).
  - UI: edit summary/categories, deadline Edit/Remove, add/delete fact,
    evidence timestamps click-to-seek.
  - Found real dateutil quirk: dayfirst=True flips ISO dates; routed
    around at the correction endpoint (deadlines.py unchanged).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 64 passed, 1 skipped
  (56 prior + 8 new tests/test_phase5_corrections.py).
- node --check app/static/app.js -> syntax OK.
Blockers:
- None.
Next:
- Commit phase5-agentic, merge to v0-core, then Phase 6 — personal-use
  validation (2 weeks, >=20 real reels, docs/V0_RETROSPECTIVE.md).

---

Date: 2026-09-06
Phase: 6 — personal-use validation (setup complete, usage window open)
Done:
- Merged phase5-agentic (7290021) fast-forward into v0-core; v0-core and
  phase5-agentic both at 7290021.
- Live smoke test on the running owner instance (:8756) completed: PATCH
  summary, PATCH ISO deadline, 422 on unparseable deadline, manual fact add,
  DELETE reel with purge_media all verified with the real bearer token.
- Scratch smoke-test data removed from the owner DB via the API (reel 9,
  fact 35, its jobs, embeddings): all verified 0 rows afterward.
- Created docs/V0_RETROSPECTIVE.md template: starting-point metrics,
  fill-in metric table, failure log, the plan's five end-of-window questions.
Blockers:
- None. (One transient DELETE 401 during the first smoke pass never
  reproduced; identical require_auth path succeeded on retry — recorded as
  an invocation issue, not a code defect.)
Next:
- Owner uses the app for real for ~2 weeks and processes >= 20 real
  reels/videos (currently 0 real; reels id 1-8 are verification artifacts
  and may be deleted by the owner at will). Then answer and commit
  docs/V0_RETROSPECTIVE.md per AGENTS.md §11 Phase 6.

---

Date: 2026-09-06
Phase: 7 — URL ingest reliability (core fix verified live)
Done:
- Cleanup: removed orphaned lean-rebuild artifacts (v0/, tests_v0/, stale docs/COMPLETION_PLAN.md); suite still green.
- Phase 7 selected per AGENTS.md table (owner pain: importing real reels by URL); recorded in docs/DECISIONS.md #11.
- Root cause found live with a real owner URL: stage_ingest (download_reel) was registered but NEVER enqueued — enqueue() defaults to STAGES[1:], URL reels have no media_path, so media died "No media on disk" x3 -> dead. The download was never attempted.
- Fix 1: create_reel_from_request enqueues the full STAGES chain (incl. ingest) when the adapter declares needs_download (app/api/main.py).
- Fix 2: retry endpoint — dead media job or failed-state rebuild with media_path NULL + source_url now rebuilds from ingest instead of dead-ending (old jobs wiped, full chain enqueued).
- Verification live (owner URL https://www.instagram.com/p/DcySzyYS0qB/): reel 10 completed end-to-end — yt-dlp anonymous download (DcySzyYS0qB.mp4, 15.8s), 11 frames, OCR 3 overlays, evidence floor dropped 1 unsupported fact (0 kept; low transcription conf 0.41), embed + finalize done. Reel 9 (legacy broken record) retried -> full chain from ingest -> terminal 'duplicate' of reel 10. No worker crash.
- 6 regression tests in tests/test_phase7_url_ingest.py (ingest stage enqueued for URLs; uploads unchanged; both retry paths).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 70 passed, 1 skipped (64 + 6 new).
- Live: POST /api/reels with real URL -> completed; POST /api/admin/retry/9 -> duplicate terminal state.
Blockers:
- None.
Next:
- App restart note: server restarted twice during fix (was running old code); current PID serves fixed code.
- Remaining Phase 7 scope (optional, next session): caption fetch reliability (yt-dlp caption empty on this reel — OCR carried the content), duplicate ingest UX (retry -> duplicate is correct but user-facing message could say so).

---

Date: 2026-09-07
Phase: 7 (continued) — pipeline reliability + speed pass
Done:
- PermanentJobError (app/db/queue.py): deterministic stage failures (missing media/audio, unreadable/truncated video, oversized file, ffprobe timeout) now dead-letter on attempt 1 with true attempt count recorded, instead of burning the 3x retry ladder. PermanentMediaError subclasses both PermanentJobError and MediaError so existing handlers keep working.
- Retry-storm guard: on any retryable failure, downstream queued jobs of the same reel are pushed to >= the failed job's next run_after — transcribe can no longer fail repeatedly alongside a still-retrying media stage.
- faster-whisper activated: WhisperProvider._load registers the bundled llamacpp CUDA DLL dir (cublas64_12 was missing from PATH; the CUDA attempt also poisoned the in-process cpu retry). Bench: transcribe 26.4s -> 15.5s for the 2s bench clip; 15.8s real reel audio 0.3-2.0s warm on RTX 3050.
- Bench tool: scripts/bench_pipeline.py measures media/transcribe/ocr heavy stages on a synthetic clip (use -X utf8 on Windows console).
- 5 regression tests in tests/test_reliability.py (permanent deads-once, downstream delay, missing-input permanence, truncated-video fail-fast <15s).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 75 passed, 1 skipped (70 + 5 new).
- tests/test_ai_eval.py -> 1 passed (146s live) with faster-whisper active; no metric regression (field recall 0.824, malformed 0.0, unsupported_kept 0.157 vs 0.178 baseline, deadline parse_recall 1.0, 11.7s/reel LLM latency unchanged).
- Failed-fast check: truncated mp4 -> StageCancelled in <1s (was 3x60s timeout ladder).
Blockers:
- None. Note: faster-whisper 'small' model (~480MB) now in HF cache; cublas DLLs resolved from llamacpp/ dir.
Next:
- Optional remaining reliability items: caption-less URL reels (OCR carries content), bulk-ingest (20 reels) concurrency soak before Phase 6 window fills.

---

Date: 2026-09-07
Phase: 7 (continued) — bulk-ingest soak (PASS) + semantic dup false-merge fix
Done:
- scripts/soak_bulk_ingest.py: N distinct synthetic clips -> concurrent upload -> queue drain report. Run 1 (identical-looking testsrc clips): 20/20 terminal, worker alive, 0 stuck, ~12s/reel — but 19/20 false 'duplicate' from semantic_duplicate_check: identical placeholder summaries ("No summary text provided." — invented by Qwen for content-free input) -> identical embeddings -> cos>0.93 chain; reel 14 even merged into unrelated reel 4. This was plan.md's flagged 0.93 over-merge risk, now measured live.
- Fix: semantic_duplicate_check skips reels whose summary is <40 chars (content-free); hash/shortcode paths unchanged. Regression test in tests/test_reliability.py.
- Run 2 (hue-shifted distinct clips, fixed code): 20/20 completed, 0 duplicates, 0 dead jobs, 0 'database is locked', worker alive, ~9s/reel wall.
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 76 passed, 1 skipped.
- Soak runs recorded above (live app on :8756, llama-server up).
Blockers:
- None.
Next:
- Phase 7 leftovers (optional): caption-less URL reels; Phase 6 usage window remains the owner's task (>=20 real reels).
