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

---

Date: 2026-09-08
Phase: wrap-up — context cache refresh commit + service health check
Done:
- Committed pending docs/AGENT_CONTEXT.md refresh (a2dd0ff).
Verification:
- git status clean after commit; HEAD v0-core a2dd0ff.
- GET :8756/healthz -> {"ok":true,"version":"1.1.0"}; GET :8091/v1/models -> qwen2.5-3b-instruct-q4_k_m.gguf listed. Both services up.
Blockers:
- None.
Next:
- Owner decision: purge test/soak reels (ids 1-50) before real use — asked, no answer yet.
- Phase 6 is the owner's task (>=20 real reels over ~2 weeks, then docs/V0_RETROSPECTIVE.md).
- Phase 7 leftover (optional, only on demand): caption-less URL reels.

---

Date: 2026-09-08
Phase: 7 (continued) — backup-restore verification + media-path bug fixes
Done:
- Backup restore verified END-TO-END for the first time (AGENTS.md §12): snapshot
  db_20260908_125723.sqlite + media mirror restored into a clean dir, app booted on
  :8799 against it -> integrity ok (50 reels / 25 facts / 148 embeddings), owner
  login OK (auth rows survived), FTS search returned 40 hits with titles/summaries.
- BUG FOUND + FIXED: reel rows store ABSOLUTE media paths at ingest; after a restore
  to a different location /media/video + /media/thumb 404 (3 legacy rows already
  pointed at dead D:\reel-knowledge\...). Fix: resolve_media_path() in
  app/pipeline/media.py (settings.media_dir + basename fallback), wired into
  /media/video, /media/thumb, delete purge, stage_ingest, stage_media. No schema change.
- BUG FOUND + FIXED: backup_db with encrypt=True + empty RV_BACKUP_PASSPHRASE silently
  wrote PLAINTEXT DB snapshots (5 found in backups/, auth data inside). Now raises;
  scheduler loop already tolerates the raise. Owner should set RV_BACKUP_PASSPHRASE.
- ADDED: restore_db() in app/core/backups.py (decrypt + PRAGMA integrity_check into a
  clean dir, wrong passphrase rejected) — the missing restore path.
- Test hygiene: test_prod.py leaked settings.media_dir / backup_passphrase globally
  (same class as the known test_pipeline retry_backoff_s leak).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 84 passed, 1 skipped (6 new
  restore-path tests + 2 new backup tests).
- Live restore re-check with new code: GET /media/video/3 -> 404 before fix,
  200 (402KB) after; /media/thumb/3 -> 200; search OK; test instance killed, scratch removed.
Blockers:
- None. Note: LIVE app on :8756 still runs pre-fix code until next restart.
Next:
- Owner: set RV_BACKUP_PASSPHRASE (encrypted backups) and consider purging test/soak
  reels (ids 1-50) before real use — both still unanswered.
- Old plaintext snapshots in backups/ contain auth data; owner may delete the ones
  before 2026-09-08 once a verified encrypted backup exists.
- Phase 6 remains the owner's task (>=20 real reels, then docs/V0_RETROSPECTIVE.md).

---

Date: 2026-09-08
Phase: audit execution — P0 batch (A5/A6/A8) + P1 A4 + hygiene A7; eval gate re-run
Done:
- Executed owner-supplied audit Part A P0 items + reminder clamp. All four claims
  verified in code before fixing.
- A5: _normalize_categories (app/ai/router.py) now falls back to case-insensitive
  exact match against VALID_CATEGORIES after alias miss — verbatim valid categories
  ("Personal Advice", future additions) are no longer silently dropped.
- A6: router.py imports VALID_CATEGORIES from schemas.py (single source of truth).
- A8: stage_transcribe missing-audio path writes a NO_AUDIO processing event before
  raising (dead ev_local=None removed) — failures now have an audit trail.
- A4: reminder_for() clamps past-computed reminders to now+1h while deadline is
  still ahead; already-past deadlines unchanged. Owner authorized via audit.
- A7: tests/test_pipeline.py retry_backoff_s leak -> monkeypatch.
- Added tests/test_audit_p0.py (4 regression tests).
- Eval gate re-run (AGENTS.md section 9): 1 passed; field recall 0.824 (unchanged),
  malformed 0.0, parse_recall 1.0, category 0.769 (gate >=0.60; within small-sample
  noise of baseline 0.846), unsupported 0.205 (gate <=0.55). Recorded in EVAL.md.
- llama-server was down; restarted via PowerShell wrapper (verified 200 on :8091).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py -> 88 passed, 1 skipped.
- pytest tests/test_ai_eval.py -q -s -> 1 passed (161s and 231s runs).
Blockers:
- None.
Next:
- Audit P1 remaining: B1 (extraction few-shot prompt, needs eval before/after),
  B5 (Whisper no_speech_prob filter — NOTE: verify faster-whisper segment fields
  before implementing; handoff pitfall says provider may not expose them),
  B4 (verbatim date_text prompt + best_deadline fallback).
- Then P2: A1 evidence perf, B2 number normalization, B6 OCR pHash pre-dedup,
  C1 stage timing, D1-D4 test gaps.

---

Date: 2026-09-08
Phase: multi-agent parallel batch (guide protocol executed end-to-end)
Done:
- Coordinated 4 agents per mutiagentguide.md with git worktrees under .worktrees/
  (tag pre-parallel-work @ 5fa7a2c). Merge order per guide: tests -> router+evidence
  -> eval -> pipeline -> final eval.
- Agent 3 (agent3-tests, f5fcc11): 26 new tests in 4 new files (search ranking,
  category normalization, dup check, deadline parsing). EXPOSED 1 REAL BUG via
  xfail: parse_deadline("2026-06-01") -> 2027-01-06 (dateutil dayfirst flips ISO
  dates when both trailing numbers are valid months). Fix candidate: ISO fast-path
  (date.fromisoformat) inside parse_deadline — NOT yet applied.
- Agent 1 (agent1-router, 305084f+08a10c9): B3 recipe/fitness->generic aliases
  (finance already existed), B1 compact 4-fact few-shot example in extraction
  prompt (1808 chars, schema-validated, quotes verbatim-by-construction),
  B4 verbatim-date rule in BOTH schema and generic branches. Flagged:
  llm_max_tokens=1400 truncation risk on dense reels; extract() has no retry
  (classify does) — coordinator decisions pending.
- Agent 2 (agent2-evidence, 0819b2c+7130b4c): A1 Jaccard pre-filter (0.20 gate,
  containment exempt), B2 number word->digit normalization with phone/date
  safety guards, A3 no-timestamp confidence redistribution (0.45/0.35/0.20,
  ts-branch bit-identical), B4 best_deadline value-parse fallback at 0.5.
  Deviation (justified): pinned 0.42->0.51 in tests/test_phase1_fixes.py (A3
  mandates the change). Known limit: '50crore' vs '50 crore' no longer matches.
- Agent 4 (agent4-pipeline, 10c9d78+acc28fe+82c572f): B5 whisper hallucination
  filter (no_speech_prob>0.7 AND avg_logprob<-1.0; fields VERIFIED present in
  providers + transcript_segments; drops counted in event data_json),
  B6 phash frame dedup before OCR (hamming<5, frames.phash column reused),
  C1 stage timing events from queue dispatch, C2 wal_checkpoint(TRUNCATE)
  every 100 jobs, C3 worker stop event + bounded graceful shutdown.
Verification:
- Suite: 88+1 baseline -> 151 passed, 1 skipped, 1 xfailed after all merges.
- Golden eval after agents 1-3 merge: 1 passed (recall 0.765-0.824 across 2 runs,
  unsupported 0.12). Final eval after agent4 merge: 1 passed; recall 0.824,
  malformed 0.0, unsupported 0.098 (halved vs 0.178 baseline), parse_recall 1.0,
  category 0.769. Recorded in docs/EVAL.md.
Blockers:
- None.
Next:
- DONE 2026-09-09 (4b5f0dc): fuzzy-path ISO flip fixed — ISO fast-path generalized to embedded dates ("apply by 2026-10-01" now October 1, was dayfirst-flipped Jan 10 + year roll). Slash dates keep dayfirst. Suite 155+1; eval gates pass (recall 0.824, malformed 0.0, unsupported 0.085, parse 1.0). All known parse_deadline day/month flip bugs closed.
- DONE 2026-09-09 (runbook executed): worktrees+agent branches removed (tag pre-parallel-work kept); RV_BACKUP_PASSPHRASE generated (random, stored in data/.backup_passphrase.txt + .env, both gitignored, never printed); app restarted on :8756 with full new code (pid 32452); suite 154+1; eval gates pass (recall 0.824, unsupported 0.128, parse 1.0); ALL 50 test/soak reels purged via API (DB empty); FIRST ENCRYPTED BACKUP taken (db_20260909_113849.sqlite.enc, 123 media files) and RESTORE-VERIFIED (integrity ok, 0 reels post-purge, 2 users); all old plaintext *.sqlite snapshots deleted.
- Phase 6 is NOW LIVE: owner imports >=20 real reels over ~2 weeks, then docs/V0_RETROSPECTIVE.md. Watch during window: unsupported-kept rate on real content, A3 confidence lift on caption-only reels, llm_max_tokens truncation (dense reels), extract() malformed-JSON rate (decide on retry).
- Coordinator decisions: extract() one-retry (AGENTS.md permits), llm_max_tokens bump for dense reels (measure truncation rate first), Pillow getdata() deprecation in phash().
- Worktree cleanup available: git worktree remove .worktrees/{agent1-router,
  agent2-evidence,agent3-tests,agent4-pipeline} + branch deletion after owner
  confirms retention preference.

---

Date: 2026-09-09
Phase: 6 kickoff — live smoke test on production code (PASS), DB returned to clean
Done:
- Live end-to-end smoke test with real content (scripts/make_test_reels.py job_reel.mp4:
  TTS speech + text overlays) via upload API on :8756, all new code active:
  - Pipeline: queued -> media -> transcribe -> ocr -> classify_extract -> embed ->
    finalize -> completed in ~40s wall.
  - Extraction: 5 facts kept, ALL evidence-backed (verbatim quotes from transcript/OCR),
    conf 0.9; deadline 'September 15' -> 2026-09-15 via the new ISO-safe path; summary
    correct. Schema job fields (company/location/role/skills/deadline) all present.
  - B5 verified live: 8 transcript segments persisted, 0 hallucinated dropped (clean
    audio - expected); filter active with dropped_hallucinated counter available.
  - B6 verified live: 12 frames kept after pHash dedup (from ~26 sampled at 1.5s on a
    14s clip - overlays visible ~2s each, dedup working as designed).
  - C1 verified live: per-stage durations in processing_events: media 2.97s,
    transcribe 15.81s, ocr 21.72s, classify_extract 7.31s, embed 2.83s, finalize 0.03s.
    NOTE: OCR (21.7s) + transcribe (15.8s) = 76% of pipeline time - optimization
    targets if speed matters later.
  - C2/C3: WAL checkpoint counter active (job 1 of 100); worker stop event wired.
  - Search: 'data analyst internship Bangalore' -> 1 hit, score 0.916, semantic match.
  - Media serving: video 200, thumb 200 (resolve_media_path active).
- Smoke reel deleted + media purged; DB returned to EMPTY, Phase 6 clean state.
Verification:
- Live API traces above; suite 155 passed 1 skipped (pre-smoke); DB reels count 0 after cleanup.
Blockers:
- None.
Next:
- PHASE 6 IS LIVE - owner imports >=20 REAL reels over ~2 weeks (upload = reliable
  path; URLs best-effort). Fill docs/V0_RETROSPECTIVE.md at the end.
- Watch in window: unsupported-kept on real content, A3 confidence lift on
  caption-only reels, llm_max_tokens truncation on dense reels (C1 timing +
  processing_events now make this measurable), malformed-JSON rate (extract retry
  decision), multi-date '01/05 and 15/06' style deadlines (ambiguous slash path).

---

Date: 2026-09-09
Phase: 6 (personal-use validation) — batch 1: 12 real reels

Done:
- All 12 owner reels (ids 1-12) completed, 0 failed, 0 stuck. Serial worker ~65s/reel, healthy across the whole batch.
- 50 facts kept total; 50/50 (100%) carry verbatim evidence quotes.
- Search verified on real data: "Zylker hiring", "September 15", "data analysts Bangalore", "philosophy" all return the correct reels (hybrid FTS+semantic, summary-level).
- A5 fix confirmed live: multi-word category "Personal Advice" retained on reel 2 (would have been silently dropped pre-fix).
- Categories mostly sensible (Tutorial/Educational dominate); reel 12 miscategorized (Tutorial/Product; its summary is a job/hiring reel).
- B5/B6/C1 active on real content (hallucination-drop counter available; per-stage timings in processing_events).

Quality signals (logged, NOT hot-fixed — per AGENTS.md section 9 any fix goes through golden eval):
- 0 facts carry timestamps (t_s absent across all 50) -> click-to-seek unavailable. Top improvement candidate.
- 0 deadlines detected on 12 reels. Reel 12's summary contains an explicit deadline ("apply by September 15") but produced 0 facts / 0 deadlines -> B1/B4 deadline-recall gap confirmed on real content.
- Fact-less reels (1: meme, 12) get conf 0.35.

Verification:
- API polls + POST /api/search traces (outputs above); 12/12 completed; suite state unchanged (no code touched this session).

Blockers:
- None.

Next:
- Owner supplies remaining URLs/uploads toward >=20 reels; then fill docs/V0_RETROSPECTIVE.md.
- Candidate scoped fix session (needs owner approval + golden eval gate): (a) t_s propagation to facts for click-to-seek, (b) B4 verbatim date_text deadline prompt fix.

---

Date: 2026-09-09
Phase: 6 (personal-use validation) — batch 2: 9 more reels (13-21)

Done:
- 9 owner URLs submitted and processed: 9/9 completed, 0 failed. Worker healthy.
- 34 facts kept; 34/34 (100%) evidence-backed quotes. conf range 0.35-0.91.
- Phase 6 reel-count target REACHED: 21 real reels processed (>= 20).
- Search verified on new content: "Microsoft Azure Principal", "Claude certifications", "free experiences Bangalore" all hit correct reels.
- Extraction quality: strong on tutorial/product/job content (reels 14, 16, 18, 19: 3-8 facts each, correct categories incl. Job, AI/ML, Startup, News).
- Reel 21: 0 facts (conf 0.35, hiring-tips talking-head reel) - general-advice reels produce few evidence-backed facts; acceptable v0.1 behavior.

Persistent gaps (unchanged, logged for retrospective / fix session):
- 0/50+34 facts carry timestamps (t_s) -> click-to-seek unavailable everywhere.
- 0 deadlines detected across 21 reels (batch 2 had no explicit deadline reels; reel 12 in batch 1 remains the confirmed B1/B4 miss).

Verification:
- Poll loop: 9/9 completed; POST /api/search hits above; no code changes this session.

Blockers:
- None.

Next:
- Continue 2-week usage window (owner keeps importing as reels surface) and/or draft docs/V0_RETROSPECTIVE.md - 20-reel threshold met; answer the 5 Phase 6 questions from observed data.
- Scoped fix candidates remain: t_s propagation for click-to-seek; B4 verbatim date_text deadline prompt (golden-eval gated).

---

Date: 2026-09-09
Phase: 6 support — fix session: short-value evidence rescue + prompt example-leak fix

Done:
- Root-caused reel 12 "0 facts despite deadline in summary": NOT an evidence-matcher bug alone. Two defects found.
  (a) evidence.py find_evidence: when both quote and value are < MIN_QUOTE_CHARS (15), it returned 0.0 before the designed value-in-span weak-support path (0.75) could fire — short factual values (Zylker, Bangalore, September 15) were auto-dropped even when verbatim in source. FIXED: short-value rescue via whole-phrase token containment, similarity capped at 0.75, MIN_VALUE_CHARS=6 floor ("AI" still dropped).
  (b) router.py extract prompt: the B1 few-shot example (a Zylker hiring reel) was parroted verbatim by Qwen on low-signal content — model emitted the example's summary + 4 facts regardless of actual reel content; evidence ledger correctly dropped the facts but the summary leak persisted. FIXED: explicit FORMAT-ONLY anti-copy instruction naming the example values; generic path gained verbatim-value rule.
- Correction to batch 1/2 reports: "0/84 facts carry timestamps" was a measurement error (script used nonexistent field 't_s'; real field is evidence_t_s). DB truth: 63/84 facts have timestamps (OCR 22/22, transcript 41/46, caption 0/16). Click-to-seek data was always present; V0_RETROSPECTIVE failure log row corrected accordingly.
- Live proof: reel 22 (DcgcqXfSVrI) delete + reprocess with fixes: summary now correct (LinkedIn networking playbook, no Zylker leak), 0 facts -> 4 kept, all source-anchored with t_s + quotes, conf 0.7.

Verification:
- New tests tests/test_evidence_rescue.py (8): all pass; existing phase1/evidence/units tests unchanged (43 focused green).
- Full suite: 163 passed, 1 skipped (one transient ordering flake in 2 pre-existing tests re-ran green; unrelated to changes).
- Golden eval (2 pre-change runs for noise band, 1 post-change): field recall 0.824 -> 0.882 (improved), malformed 0.0, unsupported-kept 0.085 (in band), multilabel 0.769 (unchanged), parse_recall 0.75 (n=4 noise, pre-change swing documented). Gates: 1 passed.
- EVAL.md updated with the September 9 run entry.

Blockers:
- None.

Next:
- Correct the retrospective failure-log row for timestamps (0/84 -> 63/84, caption facts legitimately untimed) - done in same commit.
- Owner continues Phase 6 imports; B4-style prompt refinement for deadline recall can be evaluated against the new 0.882 recall baseline.

---

Date: 2026-09-09
Phase: 6 COMPLETE (window closed early at owner instruction, day 3 of 14)

Done:
- docs/V0_RETROSPECTIVE.md completed: metrics filled, failure log final, all
  5 Phase 6 questions answered from measured data, Phase 7 feature selected.
- Final window numbers: 21 unique real reels, 21/21 completed; 88 facts, all
  evidence-backed, 67 with evidence_t_s; 0 user corrections needed; search
  10/10 agent-verified hits; URL ingestion 22/23 submissions succeeded; 2
  zero-fact reels (meme, talking-head advice); 0 parseable deadlines captured
  from real content.
- Answers summary: (1) search recovers content — mechanism proven, organic
  owner reliance unproven at day 3; (2) evidence makes facts trustworthy —
  ledger demonstrably dropped 8 fabricated facts (Zylker leak case); known
  gaps: summaries not evidence-checked, weak-but-anchored facts pass floor;
  (3) URL was the practical path (22/23), upload untested in-window;
  (4) top failures: extraction fidelity on low-signal reels (parroting bug
  fixed), deadline recall ~0 on real content, observability of dropped facts;
  (5) next feature: extraction/evidence quality (§11 row "Facts are
  inaccurate") — grow golden set with real failures, harden extraction.
- Phase 7 recommendation recorded in retrospective Decision section.

Blockers:
- None for Phase 6 closure.

Next:
- OWNER DECISION NEEDED: AGENTS.md §11 pre-marks "Best-effort ingest
  reliability" as Phase 7 (pre-window pain note). Measured data contradicts
  it (URL ingest 22/23). Retrospective recommends extraction/evidence row.
  Confirm which to lock, then update AGENTS.md §11 before starting Phase 7.
- Phase 7 start (per AGENTS.md: "Update this plan before starting it").

---

Date: 2026-09-09
Phase: 7 SELECTION LOCKED (owner confirmed)

Done:
- AGENTS.md §11 Phase 7 table updated per owner confirmation: SELECTED =
  "Facts are inaccurate → Expand golden set and improve extraction/evidence".
  URL-import row re-marked "(considered, NOT selected — Phase 6 measured
  22/23 URL success)".

Blockers:
- None.

Next:
- Phase 7 kickoff scope (proposed): (1) expand tests/golden/ with real
  Phase 6 failure cases (deadline-bearing reel, low-signal talking-head,
  OCR-heavy networking reel) labeled from stored transcript/OCR artifacts;
  (2) baseline eval on expanded set — measure real-content deadline recall;
  (3) fix drop-observability: log dropped fact quotes (not just values) to
  processing_events; (4) eval-gated extraction/evidence hardening from
  measured failures only.
- Per AGENTS.md §11: one item only, golden eval gates every change.

---

Date: 2026-09-09
Phase: 7 IN PROGRESS — steps 1-3 done (golden expansion, baseline, observability)

Done:
- Golden set expanded 13 -> 16 with real Phase 6 failure cases, labeled from
  stored artifacts: job-03 (Microsoft/Azure OCR-only interview reel, no
  deadline - fabrication guard), edu-04 (low-signal talking-head, 1-word
  transcript, min_facts_kept=0), edu-05 (OCR-heavy networking how-to).
- Expanded baseline captured (EVAL.md): multilabel 0.781, macro_f1 0.539,
  field recall 0.762, malformed 0.0, unsupported 0.161, min-kept 0.938,
  parse_recall 0.75 (n=4). Gates 1 passed. NOT comparable to 13-item runs.
- Measured hardening targets recorded: (1) fact inflation on tiny content
  (job-03: 12 kept / 9 unsupported from one OCR sentence), (2) edu-05 field
  misses 0/2, (3) schema drift on real content (job-03 education vs job).
- Observability fix: dropped facts now log quote (truncated 120) + similarity
  in processing_events AND eval per-case output (was value-only). Found the
  ev() call was discarding enrichment (value-only strings) - fixed there too.
- New test test_classify_extract_logs_drop_reason passes.

Verification:
- Full suite: 164 passed, 1 skipped. Eval harness: 1 passed on 16-item set.
- No extraction/prompt/evidence behavior changed this commit (logging +
  test data only), so no eval re-gate required.

Blockers:
- None.

Next:
- Phase 7 step 4 (eval-gated hardening), smallest first: candidate fixes for
  fact inflation on tiny content (per-field fact cap or span-diversity rule
  in extraction prompt; must re-run 16-item eval before/after), then edu-05
  topic/technologies probe follow-up.

---

Date: 2026-09-09
Phase: 7 step 4 (partial) — anti-padding prompt fix landed, eval-gated

Done:
- Fact-inflation fix: schema-branch extraction prompt gains "NEVER PAD"
  instruction (values must be stated in source; no quote reuse; invented
  field worse than missing).
- Two post-change eval runs, both improve target metrics vs 16-item baseline:
  field recall 0.762 -> 0.857 / 0.810; unsupported-kept 0.161 -> 0.141 /
  0.129; parse_recall 0.75 -> 1.0 / 1.0; no gated regression (min-kept
  0.938, multilabel 0.781, malformed 0.0 unchanged).
- job-03 per-case: 12 kept / 9 unsup -> 5/1 -> 3/0 (fields 2/2 throughout).
  Inflation eliminated.
- EVAL.md updated with the step-4 run table.

Verification:
- Eval gates 1 passed on both runs (16-item set, llama-server up).
- Note: both services were found down this session (machine restart?);
  llama-server + app restarted via established Start-Process pattern.

Blockers:
- None.

Next:
- Remaining Phase 7 measured targets: (a) edu-05 field misses (topic/
  technologies 0/2 stable across runs - how-to content doesn't fill
  education fields), (b) job-03 schema drift (education vs job). Both need
  eval-gated changes; do (a) or (b) next session, one at a time.

---

Date: 2026-09-09
Phase: 7 step 4b — schema-drift fix landed, eval-gated

Done:
- Classify prompt rule added: job interview questions / role-play scenarios /
  role-title-at-company content -> Job (router.py _CLS_SYSTEM).
- Two eval runs: job-03 schema job/job in BOTH (was education); schema
  agreement 0.562 -> 0.688 both runs (stable). Cost: multilabel 0.781 ->
  0.750 (stable, gate >=0.60 fine); edu-04 ("Nobody's hiring AI enthusiasts")
  now picks job schema — categories were already Job-primary there, expected
  generic is unstable for that item. Not narrowing the rule further (would
  be single-item overfitting / tuning by intuition).
- Run B showed extraction-side noise (job-03 inflation returned 12/9 unsup)
  - documented as LLM variance; extract prompt unchanged in this step.

Verification:
- Eval gates 1 passed both runs (16-item set).
- Note: services were found down at session start; both restarted via
  Start-Process pattern and healthy.

Blockers:
- None.

Next:
- Remaining Phase 7 measured target: edu-05 topic/technologies field misses
  (1/2 at best across runs). Weakest-signal item; consider whether education
  schema field names fit how-to reels at all before touching the prompt
  (schema design question, not just a prompt fix) - decide with owner.
- Alternatively: close Phase 7 step 4 here (two fixes landed, both
  eval-gated, no regressions) and update AGENTS.md plan status.

---

Date: 2026-09-17
Phase: 7 — evaluation integrity; NOT complete
Done:
- Removed this session's uncommitted field-name prompt trial; no production
  changes or golden-label changes retained. Do not treat edu-05's misses as
  proof that education schema cannot represent how-to content.
- Corrected named-field recall to use evidence-kept facts and preserve multiple
  values per field. Added metric_version=2, per-field results, invalid field
  reports, and opt-in RV_EVAL_TRACE=1 provider-boundary traces.
- Eval result path now printed; write errors fail instead of disappearing.
  conftest redirects data_dir to temporary storage, explaining earlier missing
  files in app data. No live user records deleted/reprocessed this continuation.
- Confirmed a substantive unresolved trust defect: raw edu-05 trace copied
  company=Zylker with a genuine networking quote; matcher kept it at similarity
  1.0. Quote match is not claim validation. Earlier 'all evidence-backed' and
  'prompt leak fixed' conclusions were too strong.
Verification:
- Focused existing evidence/pipeline tests: 37 passed.
- New evaluator regression tests: 9 passed, including duplicate field order,
  rejected-fact recall, no synonym relaxation, opt-in trace and error cleanup.
- Full non-AI suite: 173 passed, 1 skipped, 10 warnings.
- Corrected 16-case live golden eval with RV_EVAL_TRACE=1: 1 passed in 150.01s.
  Recall 16/21=0.762; multilabel 0.750; macro F1 0.509; schema agreement 0.688;
  unsupported-kept heuristic 5/61=0.082; min-kept 0.938; malformed 0;
  deadline recall 4/4=1.0 (fictional positives only). edu-05 still 0/2.
- Saved D:/Temp/user/rv_test_zwzpsagr/eval_results.json verified to exist:
  all 16 exact extraction traces present, aggregate kept-fact recall recomputed.
  Temporary private output, not committed; do not assume it persists forever.
- App /healthz and llama /health returned healthy during this continuation.
Blockers:
- Metric v2 not directly comparable to earlier raw-fact recall. Harness still
  forces expected schema; empty deadlines do not test false positives. Real
  fixture provenance/labels and quote-to-claim support require review.
Next:
- Add a deterministic quote/claim mismatch regression, then evaluate scoped
  claim-support hardening. Preserve quote floor; do not tune to gold needles.
- Changes left uncommitted for owner review; no branch or commit created.

---

Date: 2026-09-17
Phase: 7 — claim-term guard checkpoint; phase NOT complete
Done:
- Added same-span meaningful claim-term prerequisite in evidence.py. Exact
  fabricated Zylker + genuine networking quote regression reproduced failing
  at similarity 1.0, then passed after the guard.
- Added tested number/date canonicalization for measured false negatives:
  zero, ordinal forms, month abbreviations, integer k amounts, LPA.
- Kept existing rescue and thresholds unchanged. No prompt/schema/golden-label
  changes. Quote-floor bypass in the old rescue is still unresolved.
- Retained prior evaluator-integrity work; 15 claim-support tests plus nine
  evaluator tests are included in the final full-suite result.
Verification:
- Full non-AI suite: 188 passed, 1 skipped, 10 warnings in 7.20s.
- Final live 16-item golden eval with traces: 1 passed in 163.55s. Metric v2
  recall 0.810; unsupported-kept heuristic 0.055 (3/55); min-kept 0.875;
  deadline recall 1.0 (4 positives); multilabel 0.750; malformed 0.
- Saved D:/Temp/user/rv_test_yp_z23xu/eval_results.json verified, 16 cases.
- Initial guard caused deadline false negatives; final normalization restored
  all four positive dates this run. No causal improvement claim across sampled
  outputs. git diff --check passed.
Blockers:
- edu-05 final run: 0 kept / 9 dropped, fields 0/2. Meaningful paraphrases and
  multi-segment facts can be rejected. Lexical support is not entailment.
- Existing rescue still accepts short/missing quotes. No live data changed,
  no service restart; do not claim the running app has this guard loaded.
Next:
- Separate follow-up documented in EVAL.md: replay identical captured outputs
  to assess false negatives and remaining recall drift, then decide quote-floor
  policy. No further tuning within this checkpoint; commit only if owner asks.

---

Date: 2026-09-17
Phase: 7 — frozen-output replay complete; Phase 7 still open
Done:
- Added opt-in offline test_evidence_replay.py. Compares pinned committed
  matcher f42fbef and current matcher using exactly the same recorded raw facts
  and source spans; refuses source/prompt drift. No live model/API calls.
- Replayed both private saved runs. Trace A: kept 66->51, field hits 16/21
  unchanged, deadlines 4/4 unchanged, min-kept cases 14/16 unchanged. Trace B:
  kept 76->55, field hits 18/21->17/21, deadlines 4/4 unchanged, min-kept cases
  15/16->14/16. edu-05 kept 4->2 and 7->0 respectively.
- Confirmed recall cost: Networking Simplification vs simplify networking;
  yrs vs years and multi-segment claims also cause losses. Benefit: invented
  Remote and Not specified placeholder claims lose false support.
- Explicit conservative edu-05 acceptance regression: LinkedIn networking
  passes with source timestamp 0.0; Zylker and Networking Simplification drop.
  This deliberately acknowledges lexical false negatives; no claim of full
  entailment or end-to-end edu-05 extraction success.
Verification:
- Each opt-in replay invocation: 3 passed in 0.09s. Private output files named
  eval_results_replay.json beside each saved input in rv_test_7h5g488o and
  rv_test_yp_z23xu; verified and reviewed changed decisions.
- Determinism check: 101 unique inputs, 66 repeated identical inputs across
  captures, no changed current-matcher result for identical inputs.
- Full non-AI suite before final acceptance test: 190 passed, 2 skipped,
  10 warnings in 13.53s. After acceptance test: claim suite 16 passed in 0.05s.
- No production changes, no golden-label changes, no service restarts,
  no user data modifications, no commit in this step.
Blockers:
- Guard remains conservative and can reject legitimate paraphrases/multi-span
  facts. Old rescue quote-floor exception remains. No automatic deployment.
Next:
- Review conservative checkpoint before deployment. Any recall expansion or
  strict quote-floor policy must be a separate scoped evaluation, not more
  aggregate-score tuning. EVAL.md records the accepted behavior and limits.

Final verification for this iteration (2026-09-17):
- Added pipeline persistence regression: a genuine networking quote cannot
  persist company=Zylker; supported LinkedIn networking persists with quote
  and timestamp 0.0. No production edits after the guard checkpoint.
- Full non-AI suite: 192 passed, 2 skipped, 10 warnings, 6.42s.
- One final live golden run: 1 passed, 126.33s. Metric v2 recall 0.762;
  unsupported-kept heuristic 1/53=0.019; min-kept 0.875; deadline recall 4/4;
  malformed 0; multilabel 0.750. edu-05: 0 kept / 3 dropped, fields 0/2.
  Trace written to D:/Temp/user/rv_test_wf4tigqv/eval_results.json.
- Verdict: isolated fabrication regression fixed and tested through persistence;
  frozen replay confirms a real recall trade-off. Not a complete extraction
  solution or production-readiness claim. Stop iteration; changes uncommitted.

---

Date: 2026-09-17
Phase: 7 — checkpoint c00be1d revalidated; open limitations retained
Done:
- Confirmed prior guard/evaluator/replay work is committed as c00be1d.
- Explored a bounded transcript-window fallback with test-only work; it was
  not implemented. Removed only that new uncommitted test file. Production
  code remains c00be1d; no prompt/threshold/label changes or deployment.
Verification:
- Non-AI suite: 192 passed, 2 skipped, 10 warnings in 9.14s.
- Golden eval with traces: 1 passed in 161.09s; metric v2 recall 0.762,
  unsupported-kept heuristic 2/52=0.038, min-kept 0.938, deadline recall 4/4,
  malformed 0, multilabel 0.750. edu-05: 2 kept, 2 dropped, fields 0/2.
- Output reported at D:/Temp/user/rv_test_x5n237fn/eval_results.json.
Blockers:
- Passing aggregate gates is not proof of semantic claim support. Existing
  rescue still bypasses strict quote-floor policy; multi-segment and paraphrase
  recall remain limited. Phase 7 not declared fully complete.
Next:
- Owner review of conservative checkpoint and its deployment/quality trade-off.
  Do not repeat tuning or revalidation without a new scoped acceptance target.

Date: 2026-09-17
Phase: 7 — education candidate reverted; checkpoint c00be1d restored
Done:
- Diagnosed edu-05 via live runs and saved traces: model never emits LinkedIn
  as a technology (emits LLM only); claim-term guard correctly drops an
  invented difficulty value. Evidence matcher proven capable when a LinkedIn
  value IS emitted (test_edu05_conservative_claim_acceptance).
- Tried and removed two mechanisms: extra prompt wording (1 passed, 2 failed
  live) and a bounded second-pass recall call (same failures). Both are
  measured dead ends, not silent reverts.
- Reverted field-keyed candidate to checkpoint behavior (router.py,
  schemas.py, related tests). Checkpoint measured better: recall 0.762,
  deadline recall 1.000, min-kept 0.938 vs 0.714/0.500/0.875.
- Kept additive verified work: deadline surface-form parse tests, isolated
  edu-03 stage replay (evidence score 0, persisted 2026-10-05 via existing
  deterministic source fallback), benchmark-metric limitation documented.
Verification:
- Full non-AI suite: 196 passed, 2 skipped, 11 warnings in 15.95s.
- Single-test confirmation of the restored Zylker-rejection pipeline test.
- Live edu-05 extraction run: technologies=[LLM] only, no LinkedIn emitted;
  all kept facts sim=1.0 except correctly-dropped invented difficulty.
Blockers:
- Education platform/tool recall (edu-05 LinkedIn) unsolved. Phase 7 open.
- No commit, deployment, label edits, threshold relaxation or reprocessing.
Next:
- Owner decision on a genuinely different extraction strategy for missing
  platform facts; do not repeat prompt wording or second-pass trials.

Date: 2026-09-17
Phase: 7 — recall mechanisms exhausted; entity evidence guard delivered
Done:
- Tried and removed a third recall mechanism (education worked example):
  measured worse live (paraphrased values dropped by claim-term guard,
  edu-01/03/05 all false). Reverted; job prompt untouched.
- Verified model input contains the missing values (LinkedIn, RAG present
  in transcript+caption spans) — omission is model value selection.
- Entity bridge measured dead: model emits FAISS/Qdrant entities for
  edu-01 but no LinkedIn entity for edu-05.
- Fixed a real trust-policy violation found during entity tracing: entities
  were persisted with NO evidence validation. stage_classify_extract now
  runs every entity through find_evidence; unsupported names are dropped
  and logged; supported entities keep matched-span quote and timestamp.
  Live runs had shown Zylker (job-example copy) entering education entities.
Verification:
- New regression: test_entity_hallucination_is_not_persisted passes
  (fabricated Zylker dropped, supported LinkedIn kept, t_s=0.0).
- Full non-AI suite: 197 passed, 2 skipped, 11 warnings in 8.42s.
- Live education runs: edu-01/03/05 kept-field matches all false with the
  example prompt; checkpoint prompt restores prior behavior (topic true).
- Benchmark metrics unaffected by the entity guard by construction (harness
  replicates the fact loop only); no new benchmark run needed for it.
Blockers:
- edu-05 technologies (LinkedIn) is the ONLY stable extraction failure:
  0/2 across 4 runs of unchanged checkpoint behavior and all 5 prompt
  variants tried (checkpoint, field-keyed, extra wording, second pass,
  worked example, grammar-constrained). Model capability ceiling at
  Qwen2.5-3B; requires an owner decision (larger model / different
  paradigm / accept limitation).
- CORRECTION of the earlier entry: edu-03 deadline is NOT a stable miss -
  it flips 0/1 <-> 1/1 between runs of the unchanged checkpoint (variance
  study: recall 0.714/0.714/0.810, deadline 0.75/0.75/1.00). The prior
  "field-keyed regressed recall" rationale was single-run misattribution;
  education hits were 2/4 in both candidates. Phase 7 open. No commit.
Next:
- Owner decision required: accept the entity guard as a scoped Phase 7
  increment, and choose whether education recall work continues via a
  different strategy (e.g. larger model or two-stage classify-then-verify)
  or pauses with the limitation documented.

Date: 2026-09-18
Phase: 7 — CLOSED with accepted limitation
Done:
- Owner decision recorded: education recall limitation ACCEPTED. No larger
  model, no paradigm change, no further prompt iterations. Entity evidence
  guard is the accepted scoped Phase 7 increment.
- One further hypothesis (strict one-shot technologies-only prompt or
  dedicated pass) was evaluated against session evidence and disregarded:
  the dedicated pass was already measured dead (1/3 live), a worked example
  measured worse (0/3 live), and a LinkedIn-specific example would be
  fixture contamination, violating the no-fixture-specific-extraction rule.
Verification:
- Full non-AI suite: 197 passed, 2 skipped (final tree, this session).
- Variance study (3 runs) and all probe results recorded in docs/EVAL.md.
Blockers:
- None for the accepted scope. edu-05 technologies stays 0/2, documented.
Next:
- Owner authorization to commit the working tree (stages.py entity guard,
  deadline parse tests, edu-03 replay test, EVAL/handoff docs) as the
  Phase 7 closing checkpoint.

Date: 2026-09-20
Phase: Post-Phase-7 verification + regression fix
Done:
- Full read-through of the codebase at HEAD 738603c (main agent, direct reads).
- Fixed test regression introduced by 738603c (deterministic platform
  recovery): tests/test_pipeline.py assertions in
  test_fabricated_claim_with_real_quote_is_not_persisted and
  test_education_deadline_replay_uses_source_fallback now accept the
  source-anchored `technologies` regex facts ("linkedin", "sql") while
  preserving the fabrication guards (company=Zylker never persists;
  zero-supported deadline entry never persists). Tests were stale vs.
  intended behavior; no production code changed.
- Registered the jev MCP server in Qoder user scope
  (C:\Users\dell\.qoder\settings.json), mirroring the Zed context_servers
  entry; AI_GATEWAY_API_KEY kept as an env reference, not plaintext.
- Fixed Pillow deprecation in app/pipeline/media.py phash():
  img.getdata() -> img.get_flattened_data() (getdata removed in
  Pillow 14; installed version 12.3.0 warns).
- Verified the untracked "ReelVault Project Codebase Review.md" audit
  claims match repo reality: commit d7c32ed (P0 audit batch) is an
  ancestor of HEAD and tests/test_audit_p0.py +
  tests/test_category_normalization.py exist.
Verification:
- pytest tests/test_pipeline.py -q: 10 passed.
- phash hash output byte-identical between getdata and
  get_flattened_data on a deterministic random test image.
- Full non-AI suite: 197 passed, 2 skipped (post-Pillow-fix).
Blockers:
- App server (:8756) and llama-server (:8091) are down (healthz check);
  not restarted this session.
- jev tools become visible only after /mcp reload in a Qoder session.
Next:
- Owner decisions: commit the test fix + this handoff entry; disposition of
  "ReelVault Project Codebase Review.md" (103k-line untracked transcript);
  uncommitted AGENTS.md Jev-workflow edit.

Date: 2026-09-20
Phase: 8A complete (trust fixes) + two 8C hygiene items
Done:
- State assessment: full read of HEAD 738603c, verified the untracked
  codebase-review transcript's P0 claims against git (d7c32ed is an ancestor;
  tests/test_audit_p0.py and tests/test_category_normalization.py exist).
- Produced the Phase 8 plan (8A correctness, 8B evaluation honesty, 8C finish
  the promise, 8D prove v0.1 exit) with explicit non-goals honoring the
  2026-09-18 accepted-limitation decision. Owner approved starting.
- 8A.1 Skill matcher (defect introduced by committed 738603c): substring
  containment replaced by whole-token regex (SKILL_RES), and each matched
  skill now carries the SourceSpan that contained it, so evidence_source,
  evidence_quote and evidence_t_s are real rather than a hardcoded
  "transcript"/None. Measured before state: "pythonic"->python,
  "digital"->git. Also fixed a latent ordering bug: build_spans ran AFTER the
  pre-pass, so the matcher referenced spans before assignment.
- 8A.2 Summary evidence gate: grounding_ratio() + SUMMARY_GROUNDING_MIN in
  evidence.py (reuses _claim_terms, no second support notion), schema v5
  adds reels.summary_grounding, extraction persists it (NULL = never
  measured, distinct from 0.0 = measured ungrounded), reel_card derives
  summary_verified, PATCH recomputes on manual edit scoped to owner, UI shows
  a "not source-checked" chip. Threshold chosen from measured data, not
  intuition: 21 real reels pass 8/11/15/15/16/18 at coverage
  1.0/.9/.8/.7/.6/.5, median 0.909, so strict 1.0 would flag 13/21 and make
  the marker ignorable; 0.80 flags the 6-item loose tail including one real
  summary with zero source overlap.
- 8A.3 Dependency floor: requirements-docker.txt pillow>=12.1.0, verified
  against Pillow's own release notes that get_flattened_data() was introduced
  in 12.1.0 (a pillow>=12 floor would still install a 12.0.x without it).
- 8C.3 Download size cap: media.MAX_VIDEO_MB is now the single limit shared by
  validate_video and the downloader; fetch.verify_size() deletes an oversized
  file and raises a user-safe FetchError on both the yt-dlp and Playwright
  result paths, instead of discovering the limit one stage too late.
- 8C.4 auth.py Path import moved to the module header.
- Pillow phash deprecation fixed earlier this session (getdata ->
  get_flattened_data, byte-identical hashes verified).
- jev MCP registered in Qoder user scope; tools now load (see blocker).
Verification:
- pytest tests -q --ignore=tests/test_ai_eval.py: 226 passed, 2 skipped,
  8 warnings in 10.06s. Tree baseline at session start was 197 passed,
  2 skipped; +29 net new tests (tests/test_summary_grounding.py 19,
  tests/test_fetch_size_cap.py 7, 3 skill-matcher cases in test_pipeline.py).
- v5 migration rehearsed on a COPY of the owner's real DB taken via sqlite3
  backup API (includes WAL): all ten table counts identical before/after
  (21 reels, 88 facts, 320 segments, 131 OCR, 87 entities, 59 reel_entities,
  560 embeddings, 294 events, 29 sessions, 147 jobs); PRAGMA integrity_check
  ok; reels_fts still returns 3 hits for 'hiring' after the ALTER TABLE;
  schema_migrations 1-4 -> 1-5; original file confirmed untouched at v4.
- One cap-test failure was root-caused as a test defect (glob 'CAP01.*' does
  not match 'cap01_actual.mp4' on Windows, so it hit the wrong branch) and
  the fixture was corrected; production code was not altered to satisfy it.
- Calibration and gate limits recorded in docs/EVAL.md (Sept 20 section).
Blockers:
- Jev review gate did NOT run: the jev MCP server loads but every call fails
  with "AI Gateway authentication failed". AI_GATEWAY_API_KEY is not reaching
  the spawned process. Owner must export it in the environment that launches
  Qoder (Zed's copy is a shell expansion, not a literal). Per AGENTS.md the
  gate is required before any completion claim, so the items above are
  implemented and locally verified but UNREVIEWED.
- llama-server (:8091) and the app server (:8756) are both down, so
  tests/test_ai_eval.py was never run: no live extraction, no end-to-end UI
  verification, and no reel was reprocessed. Real DB summaries are still
  NULL for grounding; the published distribution measured stored summaries
  against stored sources, it is not a re-extraction result.
- Nothing committed. Working tree holds all Phase 8A/8C changes plus the
  owner's uncommitted AGENTS.md section and the untracked 103k-line
  "ReelVault Project Codebase Review.md".
Next:
- Owner: fix the jev key and run the review gate over this diff, then
  authorize the commit. After that, 8C.2 (FTS over facts/transcript/OCR)
  deliberately deferred: it changes search ranking, which currently measures
  10/10, and cannot be re-verified while both services are down.

Live verification follow-up (same day, 2026-09-20, owner authorized restart):
Done:
- llama-server restarted on :8091 with the Qwen2.5-3B checkpoint and the app
  server restarted on 127.0.0.1:8756 (localhost only, not 0.0.0.0). Startup
  applied the v5 migration to the real database.
- Ran the live golden benchmark, which had never been executed this session.
- Added test_edu05_platform_recovered_without_model_help: drives the real
  stage on the real edu-05 fixture with an extraction returning zero facts,
  proving technologies=linkedin persists from the t=0.0 transcript span.
  This is the end-to-end proof the golden harness structurally cannot give.
Verification:
- /healthz {"ok":true,"version":"1.1.0"}; /readyz all_ready=true with
  db/llm/embedder all true.
- RV_EVAL_TRACE=1 pytest tests/test_ai_eval.py -q -s: 1 passed in 118.03s,
  16 cases, all served_by=qwen. field recall 0.810 (17/21), deadline parse
  recall 1.000 (4/4), unsupported-kept 0.036 (2/55), min-kept 0.875,
  multilabel 0.750, macro-F1 0.509, schema agreement 0.688, malformed 0.0,
  mean 7.32s/reel. All gates pass; both varying metrics sit at the TOP of the
  previously recorded spread, so this is no-regression evidence, not an
  improvement claim. Recorded in docs/EVAL.md.
- Real DB after startup: schema versions [1,2,3,4,5], summary_grounding
  present, 21 reels and 88 facts intact.
- Browser check of the shipped UI (read-only, nothing edited): detail dialog
  renders the "not source-checked" chip with correct amber styling.
- pytest tests -q --ignore=tests/test_ai_eval.py: 227 passed, 2 skipped.
New finding and its resolution (owner authorized the backfill same day):
- All 21 live reels displayed the not-source-checked chip right after startup
  migrated to v5, because every pre-v5 row had NULL grounding. Honest but
  useless: it recreated the cry-wolf problem the 0.80 threshold was calibrated
  to avoid, from the unmeasured side instead of the ungrounded side.
- Resolved with scripts/backfill_summary_grounding.py (no model call needed;
  transcript/OCR/caption are already stored). Dry run reproduced the
  calibration exactly (21 rows, 6 below the gate: reels 1 @0.0, 4 @0.333,
  15 @0.429, 21 @0.5, 8 @0.571, 22 @0.688); --apply persisted all 21; a second
  run reported "nothing to backfill". The script writes only NULL rows, so a
  re-extracted or user-corrected measurement can never be clobbered.
  Pre-backfill snapshot: D:/Temp/user/reelvault_pre_backfill_20260920_183021.db
  (temporary directory, not a validated restore).
- Post-backfill verification: API serves 15 verified / 6 flagged, no NULLs
  remaining. Browser check on the two extremes — reel 2 (grounding 1.0) renders
  no chip; reel 1 (0.0) renders "not source-checked" in rgb(255,180,84). The
  marker discriminates on live data.
- 5 tests added for the backfill (dry-run does not persist, apply persists,
  existing measurement never overwritten, second run is a no-op, blank summary
  stays NULL). One initial failure was a wrong test fixture, not a code bug:
  GROUNDED needs the OCR span for "before September 15", so a transcript-only
  source correctly scores 0.7; the helper was fixed, the code was not.
Blockers:
- Jev review gate still NOT run: jev MCP calls fail with "AI Gateway
  authentication failed" even with the server loaded. Owner must export
  AI_GATEWAY_API_KEY in the environment that launches Qoder.
Next:
- Owner: fix the jev key so the gate can clear this diff, then authorize the
  commit. Then Phase 8B.1 (predicted-schema end-to-end metric), justified by
  the 5/16 live schema mismatches now recorded in docs/EVAL.md.

---

Date: 2026-09-21
Phase: v2 foundation (multi-source)
Done:
- Preserved v0.1: committed Phase 8A/8C on v0-core, created main, tagged
  v0.1.0, bundle at D:/reelvault-backups/reelvault-v0.1.0.bundle, pushed all
  to https://github.com/ParamJaiswal/reelvault.git (main + v0-core + tags).
- v2 branch built (e79146a, 5e94600, 9b5a799, + this commit):
  1. Backup restore verified into clean temp dir (integrity ok, FTS live).
  2. RV_LLM_BACKEND swap seam: OpenAICompatProvider + get_llm() routing.
  3. Contentless FTS5 widened to facts/transcript/OCR/document body;
     reels_fts_shadow table (v8) fixes contentless-delete bloat.
  4. Kind-aware pipeline: content_kind column (v7), STAGE_PLANS in queue,
     media/transcribe self-skip for text reels, documents table.
  5. Adapters: X (syndication CDN), article (SSRF-guarded), paper
     (arXiv + OpenAlex), LinkedIn (yt-dlp + text fallback); lazy-registered,
     IG routing regression-tested.
  6. Evidence: SourceSpan.page, facts CHECK + 'document' + evidence_page
     (v9, table rebuild verified lossless on copied live DB v5→v9).
  7. Review pass (cavecrew): fixed FTS shadow, SSRF, Bearer/API-key
     redaction in ai_runs, transactional document insert, truncation align.
- docs/INGESTION.md written (dangling reference from base.py now real).
Verification:
- 286 passed, 2 skipped (full suite minus gated AI eval) on v2.
- Live-DB rehearsal: copy of data/reelvault.db v5→v9: 21 reels, 88 facts
  preserved, integrity ok, FTS 21 rows, search hits 8, all content_kind=video.
- E2E (tests/test_v2_e2e.py): X ingest → document → kind-aware job plan (no
  media/transcribe jobs), document-evidenced fact searchable, IG URLs still
  route to IG adapters, stages self-skip at runtime.
Blockers:
- Jev gate still unrun (key never reaches process) — owner waived this
  session; cavecrew reviewer used instead.
- trafilatura not installed (needs owner approval as new dependency);
  article adapter falls back to basic HTML extraction until then.
- PDF text extraction (pypdf/pdfplumber) not added: new dependency, needs
  owner approval. evidence_page plumbing is in place for it.
Next:
- Install llama-server, run one real X post + one real paper URL through the
  full pipeline on live data; then golden-set entries per new kind.

---

Date: 2026-09-21 (later same session)
Phase: v2 remaining-work pass
Done:
- Owner approved deps (via "continue remaining"): trafilatura 2.2.0 +
  pypdf 6.19.0 installed & pinned. PyMuPDF still excluded (AGPL).
- Paper adapter now streams OA PDFs (25MB cap, 40 pages) into page-marked
  body; build_spans -> SourceSpan.page; facts carry evidence_page.
- Article adapter: browser UA + precise DNS-failure message.
- Detail API + UI show source text; document evidence icon.
- Text-source golden set + N=3 protocol + eval baseline recorded.
Verification:
- Live arXiv 1706.03762: 15 PDF pages extracted, facts with evidence_page=1.
- Live paulgraham.com essay: 66,635-char clean body, title persisted,
  searchable via POST /api/search.
- 297 tests pass (non-LLM suite), golden eval 19 items green.
Blockers:
- Cloud LLM backend: needs RV_LLM_API_KEY + service choice from owner.
- Android: still frozen by policy.
Next:
- Use it: 20-item personal validation incl. text sources (v0 retrospective
  criteria), then decide cloud A/B from observed pain.

---

Date: 2026-09-21 (android pass)
Phase: v2 Android unfreeze
Done:
- Portable build env installed without admin: D:\tools\jdk17 (Adoptium),
  D:\tools\gradle-8.7, D:\android-sdk (platform-tools, platforms;android-34,
  build-tools;34.0.0).
- android/ was GITIGNORED and never compiled — now tracked with real
  first-build fixes: Kotlin 2.0 Compose Gradle plugin, gradle.properties
  (AndroidX), adaptive launcher icon, sdk.dir escaping, Card named-arg,
  missing JSONObject import, KDoc `video/*` nested-comment syntax error.
- app-debug.apk BUILDS: 7.6 MB at
  android/app/build/outputs/apk/debug/app-debug.apk. Gradle wrapper
  committed; README has verified build steps.
- Share intake extended: application/pdf + image/* SEND filters; activity
  accepts any stream; multipart upload sends real filename+MIME; server
  sanitizes filename; DocumentFileAdapter routes .pdf→paper, images→
  image_post; stage_ingest extracts PDF body (page markers) or registers
  image as a frame for existing OCR; UI renders shared images.
Verification:
- 303 tests pass (incl. 11 new file-ingest/PDF tests).
- Live: /ingest/upload real PDF → completed, 39590 chars, 15 pages,
  evidence_page=1 facts, email anchors correctly 'document'.
- Live: /ingest/upload PNG with text → frames→OCR→completed, exact text
  'INVOICE 4500 DUE 15 SEPTEMBER' extracted.
Blockers:
- APK never run on a physical phone (no device here) — install + share-sheet
  smoke test is the owner's step: adb install -r ...app-debug.apk.
- Scanned (image-only) PDFs get no text (no OCR on PDF pages yet).
Next:
- Owner installs APK on phone; shares one PDF + one image; check notes appear.

---

Date: 2026-09-21 (cloud seam)
Phase: v2 Groq A/B preparation
Done:
- settings.llm_api_key (RV_LLM_API_KEY via .env; secret never logged; raw
  key write via shell blocked by sandbox policy — correct).
- OpenAICompatProvider: 429 Retry-After backoff (4 attempts) for free-tier
  TPM ceiling; key sourced from settings first.
- GOLDEN_FILTER env for subset eval runs (quota-bounded A/B).
- .env.example documents the Groq trio.
Verification:
- 306 tests pass (9 backend incl. new backoff/key tests).
Blockers:
- OWNER STEP: paste the 4 .env lines (key + backend + url + model) manually
  into D:\reelvault\.env, then restart server, then I run the A/B:
  GOLDEN_FILTER=paper,xpost,article pytest tests/test_ai_eval.py
Note: key was pasted into chat — recommend rotating it in the Groq console
after testing.
Next:
- Run text-golden A/B vs the local 3B baseline; record in docs/EVAL.md.

---

Date: 2026-09-21 (hybrid routing)
Phase: v2 per-kind LLM backend (branch `v2-hybrid-routing`)
Done:
- `providers.get_llm_for_kind(content_kind)`: text kinds (`x_post`, `article`,
  `paper`, `note`) use `RV_LLM_TEXT_BACKEND`; `video` and `linkedin_post`
  (can carry video) stay on `RV_LLM_BACKEND`. Empty text backend == today's
  single-backend behavior, so v0.1 is unaffected by construction.
- `TextFallbackProvider`: cloud-first, per-call fallback to the local backend,
  so a 429 or an offline cloud tier cannot kill a job.
- `RV_LLM_CLOUD_SERVER_URL` / `RV_LLM_CLOUD_MODEL_NAME`: the cloud provider
  needed its own endpoint because hybrid runs llama-server and Groq at the
  same time; both fall back to the shared settings when unset.
- `router.classify/extract` take an optional injected provider; the classify
  event's `served_by` now reports the real backend name (`llamacpp`,
  `openai_compat`, `hybrid(...)`) instead of the hardcoded `qwen`, so
  post-hoc debugging can tell who answered.
Verification:
- 324 tests pass, 2 skipped (17 in `tests/test_hybrid_routing.py`: routing
  table, cloud-fail→local at wrapper and stage level, unset-backend
  equivalence, endpoint split + shared-endpoint no-op warning,
  keyless-config dead-letter, cache rebuild, log redaction, plus two stage
  regressions that route by the reel's own `content_kind`).
- Live probe with the real Groq key: `video -> llamacpp
  http://127.0.0.1:8091/v1`, `paper -> hybrid(openai_compat->llamacpp)` with
  primary `https://api.groq.com/openai/v1` / `openai/gpt-oss-120b`; one cloud
  chat call returned the expected JSON. Re-run after every fix; same result.
Reviewer pass 1 (Jev gate waived → cavecrew reviewer), findings fixed:
- 🔴 the fallback `log.warning` interpolated a raw httpx exception string, which
  can carry the `Authorization` header → now runs through `_sanitize_error`.
- 🟠 a keyless `RV_LLM_TEXT_BACKEND=openai_compat` raised per call and silently
  degraded to local, which reads as "hybrid is slow" rather than "hybrid is
  misconfigured" → now fails loudly when the wrapper is built.
- 🟠 the `_llm_text` cache pinned one backend name and one `get_llm()` result →
  now keyed on the setting; `conftest` also stops every test inheriting the
  owner's live `.env` routing (a real key there would turn unit tests into
  cloud calls).
- Accepted, not fixed: `served_by` records the route (`hybrid(cloud->local)`),
  not which tier answered a given call — `ai_runs.backend` is the per-call
  record, and the fallback logs when it fires.
- Accepted: `note` has no producer yet; it matches the existing
  `STAGE_PLANS["note"]` entry.
Reviewer pass 2 (on the fix diff), findings fixed:
- 🟡 the keyless-hybrid `RuntimeError` was retryable, so every text job burned
  the three-attempt backoff ladder before dead-lettering → now raises
  `PermanentJobError`, the repo's existing dead-once idiom.
- 🟡 `_BEARER_RE` stopped at the first `+`, `/` or `=`, so a standard-base64
  bearer token left its tail in the log → widened the character class, with a
  `test_error_sanitization_strips_base64_bearer` regression test.
- 🔵 the no-op warning logged `primary.base_url` while guarding with
  `getattr(..., None)` → now compares one local and skips when absent.
- Confirmed clean by the reviewer: the `RuntimeError("LLM backend …
  unreachable")` message change matches nothing downstream (worker/DLQ, UI,
  tests, docs); `served_by` is only counted, never compared to `"qwen"`; the
  conftest isolation masks no production path; no `agents.md` scope breach.
Blockers:
- OWNER STEP: hybrid needs `.env` changes (I do not write keys):
  `RV_LLM_BACKEND=llamacpp`, `RV_LLM_TEXT_BACKEND=openai_compat`,
  `RV_LLM_CLOUD_SERVER_URL=https://api.groq.com/openai/v1`,
  `RV_LLM_CLOUD_MODEL_NAME=openai/gpt-oss-120b`, and `RV_LLM_SERVER_URL`
  back to `http://127.0.0.1:8091/v1`.
- Current `.env` sets `RV_LLM_MODEL_NAME=llama-3.3-70b-versatile`, which this
  key does not serve — live calls 404. Use a `gpt-oss-*` id.
- Groq key is still in this chat history: rotate it.
Next:
- Restart with the hybrid `.env`, ingest one paper and one video, confirm
  `processing_events.data_json` shows `hybrid(...)` for the paper and
  `llamacpp` for the video.

---

Date: 2026-09-21 (evidence containment audit)
Phase: Evidence Ledger hardening on `v2-hybrid-routing`
Done:
- Audited the A/B follow-up ("short-value rescue on document spans") against the
  live library and found three doors, not one: `_short_value_match`, the
  `value in span -> 0.75` boost in `find_evidence`, and `_sim`'s unbounded
  `probe in span -> 1.0` shortcut. The last one is why
  difficulty='Research' and topic='Transformer model' looked like strong
  evidence rather than weak.
- Documents are now chunked (`stages._split_document`, <=600-char sentence
  chunks, PDF page locator preserved), which is what makes the locality test
  meaningful: an article body used to arrive as one flat 66,000-char block
  because trafilatura 2.2.0 `extract()` returns no newlines.
- Containment inside a document chunk is capped by
  `evidence._containment_is_local` (`20 * len(value) + 120` normalized chars);
  beyond that doors A and B contribute nothing and door C returns 0.75 instead
  of 1.0. Transcript/OCR/caption behavior is deliberately untouched — the same
  budget applied there would have removed 11 of 17 real caption hits.
- `_sim`'s verbatim shortcut now requires `MIN_QUOTE_CHARS`, so a short value
  cannot manufacture a perfect match anywhere.
- Two instruments kept: `scripts/audit_rescue.py` (foreign-value containment
  false-accept rates on the live library) and
  `scripts/replay_matcher_rules.py` (score one extraction set under HEAD's
  matcher and the shipped one; imports HEAD rather than retyping it).
Verification:
- 338 tests pass, 2 skipped (14 new in `tests/test_evidence_document_locality.py`).
- `scripts/audit_rescue.py`: foreign document hits 11 -> 4 admitted (7 removed);
  own document hits 4 -> 1 admitted, and the 3 removed are exactly the three
  facts listed above. ocr/caption/transcript rows unchanged.
- `scripts/replay_matcher_rules.py` on all 19 golden rows: 66 facts kept under
  the pre-change matcher, 66 under the shipped one, 0 flips.
- Two full `tests/test_ai_eval.py` runs (65 -> 72 kept) were compared first and
  rejected as evidence: raw per-item fact counts differed at temperature 0, so
  llama-server is not run-to-run reproducible. That is why the replay harness
  exists. Recorded in docs/EVAL.md.
Blockers:
- The live library still holds the three weakly-supported facts: this change
  affects extraction, not stored rows. Re-running `classify_extract` on reels
  26/27/28 would rewrite the owner's facts, so it was left for the owner.
- The golden set cannot exercise the rule: its longest document row is a
  1,200-char abstract. A multi-page-PDF golden row is the missing coverage.
Next:
- Add a long-document golden row (from the 15-page PDF already in the library),
  then re-extract the three document reels and confirm the boilerplate facts
  are gone.

---

Date: 2026-09-21 (evidence containment audit)
Phase: Evidence Ledger hardening on `v2-hybrid-routing`
Done:
- Audited the A/B follow-up ("short-value rescue on document spans") on the live
  library. Three doors admitted facts on containment alone, not one: the rescue
  path, `find_evidence`'s `value in span -> 0.75` boost, and `_sim`'s
  `probe in span -> 1.0` shortcut. The shortcut is why
  difficulty='Research' looked like *strong* evidence rather than weak, and the
  module docstring's own promise ("0.75, never 1.0") was already being broken.
- Shipped one predicate: a document span carries a verbatim match only within
  `20 * len(match) + 120` normalized chars; beyond that the short-value doors add
  nothing and the shortcut returns 0.75. `MIN_STRONG_QUOTE_CHARS = 60` keeps long
  verbatim quotes strong at any span size (the worst accidental match measured in
  vivo was 25 chars), and `_sim`'s shortcut finally has the `MIN_QUOTE_CHARS`
  floor. Scoped to document spans by measurement, not assumption.
- Reverted again the same way mistakes should be: a first attempt chunked PDF
  pages and article bodies into ~600-char spans. It was built, committed, then
  measured against the golden set and dropped two real paper-01 facts whose
  200-char quotes straddled a boundary (4 kept -> 2 under BOTH matchers). The
  budget alone achieves the same false-accept removal without that cost.
- Instruments kept: `scripts/audit_rescue.py` (foreign-value containment rates by
  span source, read-only on the live DB) and
  `scripts/replay_matcher_rules.py` (one extraction set scored under
  `git show <rev>:app/knowledge/evidence.py` and under the working tree).
Verification:
- 336 tests pass, 2 skipped (12 new in `tests/test_evidence_document_locality.py`).
- `scripts/audit_rescue.py`: foreign document hits 8 -> 0 admitted; own document
  hits 4 -> 1; ocr/caption/transcript rows unchanged.
- `scripts/replay_matcher_rules.py --against 1df9cc4^`: 66 facts kept before, 66
  after, 0 flips.
- Live replay of the three suspect facts against their stored sources:
  `Research` 0.0, `Difficult` 0.0, `Transformer model` 0.75 (was 1.0), and the
  legitimate `Attention Is All You Need` still 1.0.
- Two full eval runs were compared first and rejected as evidence: raw fact
  counts differ at temperature 0 (edu-05: 11 facts then 4). Recorded in
  docs/EVAL.md as a protocol finding.
Blockers:
- Stored rows are unchanged: the rule affects extraction, so the three weak
  document facts are still in the library until those reels are re-extracted.
  Re-running `classify_extract` rewrites the owner's facts, so it was left to them.
- The golden set cannot exercise the rule (largest document row is a 1,828-char
  abstract); a multi-page-PDF golden row is the missing coverage.
- Accepted residual gap: OCR spans are capped at 2000 chars, so an OCR line can be
  as unlocal as a page. Capping them removed real support, so it stays document-only.
Next:
- Add a golden row from reel 28's 15-page PDF, then re-extract reels 26/27/28 and
  confirm the boilerplate facts are gone.

---

Date: 2026-09-22
Phase: v2-hybrid-routing — Android APK emulator verification + contract tests

Done:
- Installed Android emulator (system-images;android-34;google_apis;x86_64) via
  sdkmanager with JAVA_HOME=D:\tools\jdk17. AVD rv_test created, boots with WHPX.
- APK installed on emulator, login flow verified end-to-end: server URL
  http://10.0.2.2:8756, owner credentials, summaries list displays 5 real reels
  matching server API output exactly.
- Upload endpoint verified: POST /ingest/upload with multipart field `file`
  returns reel_id + queued status.
- URL ingest verified: POST /ingest/url returns reel_id + queued status.
- ShareIntakeActivity launches correctly on SEND intents; finishes after upload
  (correct behavior). file:// URIs rejected as expected (real shares use content://).
- New test file `tests/test_android_contract.py` (8 tests) pins the HTTP contract
  the APK relies on: login tokens, bearer auth, upload field name, URL ingest,
  summaries, healthz. All 8 pass.
- Full suite: 344 passed, 0 failed, 2 skipped.
Verification:
- Emulator screenshots confirm login → summaries list with real data.
- curl from host confirms upload (reel 30) and URL ingest (reel 31) queued.
- pytest tests/test_android_contract.py -q → 8 passed.
- pytest tests/ -q --ignore=tests/test_ai_eval.py → 344 passed, 2 skipped.
Blockers:
- None.
Next:
- Add multi-page-PDF golden row from reel 28 for document locality coverage.
- Re-extract reels 26/27/28 to clear boilerplate facts (owner-authorized).

Follow-up, same day — paper-02 golden row added (`b9d8648`), closing the first
Next item above.

## Do not relabel golden fixtures to raise schema agreement

Schema agreement is 0.688 (11/16). Four of the five mismatches were "fixed" by
editing labels and the change was reverted (`3cc95c8` -> `82a2536`). Two traps,
both non-obvious:

- `recipe-01`, `fitness-01`, `hinglish-01` expect `generic` **on purpose**. They
  are the taxonomy-gap probes (docs/EVAL.md:39-40) and their `fields` use the
  `{"*": [...]}` wildcard that drives the "Content presence (taxonomy-gap items)"
  metric. Relabeling them to `education` makes the classifier look better only by
  deleting the cases that measure what happens when no schema fits.
- `scholarship-01` expects `job` because `JobFacts` is the only schema with a
  `deadline` field (`app/knowledge/schemas.py:29-50`) and the harness extracts
  against the **expected** schema (`tests/test_ai_eval.py:223`). Switching it to
  `education` leaves the fixture structurally unable to express its Nov-30
  deadline, silently breaking deadline parse recall.

Real fix, if pursued: a schema that fits how-to content, or an owner decision
that `generic` is the correct verdict for it. Not a label edit.

## Running the app — two live defects found and fixed (2026-09-22)

Owner reported "Failed to fetch" / offline badge when adding a new Reel URL.
Two unrelated causes, both diagnosed against the running app:

1. **Groq retired `llama-3.3-70b-versatile`.** The `.env` still named it, so
   every `classify_extract` died with HTTP 404 `model_not_found` after 3
   attempts. Confirmed live: `GET /v1/models` on the owner's key lists 13 models
   and that name is not among them; `openai/gpt-oss-120b` and `-20b` do respond,
   and gpt-oss returns usable JSON at the existing `llm_max_tokens=1400` (~1.3 s,
   reasoning ~420 chars). Owner chose to leave the cloud and return to local
   llama.cpp, so `.env` is now `RV_LLM_BACKEND=llamacpp`,
   `RV_LLM_SERVER_URL=http://127.0.0.1:8091/v1`,
   `RV_LLM_MODEL_NAME=qwen2.5-3b-instruct`. Backup: `.env.bak-20260922-085924`.
   **If cloud is wanted again, the model name must be re-checked against
   `GET /v1/models` — Groq retires models and a retired name is a hard 404.**
2. **`retry_stage` left reels wedged forever** (`03c0f88`, extended `06be8bd`).
   `fail()` deletes every still-queued stage downstream of one it dead-letters,
   but the admin retry revived only the dead row. Result: all jobs read `done`,
   no `embed`/`finalize` exist, and the reel sits in `processing` permanently.
   `retry_stage` now re-queues the missing tail of the reel's chain,
   idempotently. Two further holes in the same class were found on review:
   - `ingest` is not in `STAGE_PLANS`, so retrying a dead `ingest` restored
     nothing. The chain is now `["ingest"] + plan`.
   - `/api/admin/retry`'s rebuild fallback used hardcoded `STAGES`, so a **text
     source** (paper/article/x_post/note) that failed with `media_path` NULL —
     normal for them — was forced back through `ingest` and additionally got
     `media`+`transcribe`. Running `ingest` on a text source takes the
     metadata-only terminal path and **clobbers its stored
     `documents.body_text`** — the hazard the enqueue comment (main.py:163-177)
     warns about. Now derived from `content_kind`; video is unchanged because
     `["ingest"] + video plan` **is** `STAGES`.
   Do not infer a reel's chain from its surviving `jobs` rows — `fail()` deletes
   them. `content_kind` is the only durable signal, and it is set once at reel
   insert and never updated.

Also fixed (`add8bf6`): `auto_start_llama` defaults True, so with a cloud
`RV_LLM_SERVER_URL` (no port to parse) `llama_manager._server_base()` raised
`ValueError: invalid literal for int() with base 10: '//api.groq.com/openai'`
and killed the llama-starter thread on **every** startup. Now gated on
`llm_backend == "llamacpp"`.

Verification:
- Startup log clean; `rv.llama_mgr spawning llama-server` -> `is up (pid 24144)`;
  `/readyz` = db/llm/embedder all true.
- Reel 32 (`instagram.com/reel/DdhzEBOqxlp/`) un-wedged by enqueuing its missing
  `embed`+`finalize`: now `completed/done`, summary "Claude helps with DSA
  interview preparation…", cats Tutorial/Product, conf 0.9, 1 evidence-backed
  fact (`company='Claude'`, transcript, t_s=0.0).
- FTS confirms the core promise: searches "DSA interview", "Claude", "hints"
  all return reel 32.
- Suite: **350 passed, 3 skipped**. 5 new tests this session (3 retry-chain,
  2 text-source/ingest), each verified to FAIL against the pre-fix code and pass
  after — not vacuous.
- cavecrew review of the queue diff: 0 critical; two advisory findings both
  addressed (inaccurate comment corrected, caller invariant documented). It
  independently surfaced the text-source ingest clobber before it was fixed.
- `.env` is gitignored but a config **backup is not**: `.env.bak-*` sat in the
  repo root holding the live Groq key, one `git add .` from being committed.
  Moved to `D:/Temp/user/reelvault_env.bak-20260922-085924`, and `b6fb5e5`
  added `.env.bak*` to `.gitignore` (verified `.env.example` is still tracked —
  the pattern is narrower than `.env.*`).
- Reels 30/31 are emulator-test junk (30 = invalid stub MP4, `failed`;
  31 = fake URL, `completed`, no facts). Owner left them in place.
- **Background processes started from the agent do not persist** — the app died
  twice with no traceback when the tool call ended. For a durable session the
  owner must run `Start-ReelVault.ps1` in their own terminal.
- Unpushed: `v2-hybrid-routing` is ahead of its remote; nothing was pushed.
- Git hygiene: `06be8bd`'s subject duplicates `03c0f88`'s; it is the refinement
  of that fix, not a repeat. Left as-is rather than amended.


## Document-quote matching ceiling fixed (2026-09-23)

Date: 2026-09-23
Phase: Phase 7 — fact accuracy / Evidence Ledger

Done:
- `app/knowledge/evidence.py`: new `_local_coverage`, reached from `_sim` only for a
  **document** span and only for a quote at `MIN_STRONG_QUOTE_CHARS` or longer, as
  `max(_ratio, _local_coverage)`. It returns the fraction of the *quote* carried by
  one contiguous run of difflib matching blocks, or **0.0** — so a quote the branch
  declines gets precisely the verdict it had before the branch existed. The run must
  stay on one diagonal (`MAX_LOCAL_DRIFT = 2`, drift charged), reach both ends of the
  probe within that tolerance and without crossing a space, and leave at most
  `MAX_LOCAL_EDIT_CHARS = 8` characters unaccounted. The old failure:
  `SequenceMatcher.ratio()` divides by len(probe)+len(span), so a 2,810-char page
  capped a fully-present quote at 0.006. Jaccard's prefilter is bypassed on this
  branch (it is what made the class unrecoverable) and `autojunk=False` is required
  at this span length.
- 8 tests added to `tests/test_evidence_document_locality.py` (20 total), one per
  attack shape.
- `docs/EVAL.md`: the "Fix condition (NOT applied)" paragraph replaced by the applied
  record — the four-round history, the measured table, and the accepted limit; header
  and the defect section's status updated.

Verification:
- Deterministic matcher A/B (`d60b631^` from git vs working tree, same probe, real
  paper-02 page 1): near-verbatim 98-char document quote **0.000 → 0.9796**
  (`find_evidence` 0.98, kept, `page=1`); a verbatim run with one same-length typo
  **0.9672**. `'Research'` **0.000 → 0.000** — it would be 1.000 without the 60-char
  floor, which is why the floor is tested. `'Difficult'` 0.000 → 0.000.
  `_sim(..., False)` is byte-identical to `_ratio`, asserted.
- **Four review rounds, each finding false accepts the previous version shipped.**
  1. Summing *all* matching blocks: a quote stitched from two disjoint regions of
     one page scored **0.9919** (pre-fix 0.066, dropped).
  2. Diagonal chaining fixed the stitch and made *elisions free*: one deleted source
     word scored a perfect **1.0** — the shape of a quote that drops a negation. The
     diagonal moves by exactly `span_gap - probe_gap`, so tolerance 2 plus charging
     the drift separates a typo (0–1) from an elision (its full length).
  3. Ends were unanchored: `"not " + verbatim run` scored **0.976** — an edge edit
     costs ~11% of a long quote. Hence the both-ends anchor and the 0.9 floor.
  4. The anchor counted characters, so **`"no " + run`** — a two-character word —
     scored **0.9841** and inverted polarity; and an equal-length swap moves the
     diagonal by zero, so 20 invented characters in a 247-character quote scored
     **0.9312**. Hence the space-junction rule and the character budget.
  Every attack probe now reads **0.000**: stitch, mid elision, all three edited ends,
  the equal-length swap, and a 101-char absent quote. `MIN_LOCAL_RUN` swept 3→6: true
  probes unmoved at 0.9919, absent quote 0.059 → 0.079 → 0.049 → 0.000 — a noise
  filter with ~9× margin, not a tuned threshold.
- Accepted limit recorded in `docs/EVAL.md`: a substitution changing the length by at
  most 2 characters still certifies (`scale` → `size` = 0.9879), so a `not` ↔ `now`
  or `gain` ↔ `loss` swap does too. No string measure can catch that; it is the
  owner's review UI, which shows the quote and its page locator.
- `scripts/replay_matcher_rules.py` (20 rows, 94 raw facts, one extraction scored
  twice), against both `d60b631^` and `ee9be18`: kept **65 → 65**, 0 facts lost, 0
  count flips. The rule fires once — paper-02 `topic='Transformer model'` **0.75 →
  0.98**, i.e. weak support becomes strong support. The real document facts that
  should score still do: paper-01 1.0 ×3, paper-02 0.979, article-01 1.0.
- `scripts/audit_rescue.py` on the live library (30 reels, 73 values): document
  spans admit **0 foreign / 1 own** value — the false-accept door did not widen.
- All 15 facts stored on the 5 document-bearing reels were replayed through `ee9be18`
  and the shipped matcher against their own spans: **0 scores changed, 0 newly kept,
  0 newly dropped.** The owner's library is untouched; the branch only changes what
  a *future* near-verbatim document quote can certify.
- Cost measured, not assumed: `find_evidence` 3.7 ms/fact on the real 2-page paper,
  12.0 ms/fact against one 66k-char span.
- Suite: **358 passed, 3 skipped**.
- Fresh 20-row golden eval after the fix: `pytest tests/test_ai_eval.py -q -s` →
  **1 passed in 187.40 s**, all 20 served by llamacpp. Kept 65, dropped 38,
  unsupported-kept 0.108, deadlines 4/4, malformed JSON 0, schema agreement 0.700,
  field recall 0.760, content presence 0.333, 9.33 s/reel. Classification numbers
  are byte-identical to the Sep 22 run; what moved is at fact level. Three rows now
  miss `min_facts_kept` (`recipe-01` emitted 1 of 2, `edu-05` had all 7 raw facts
  dropped, `hinglish-01` had its 1 dropped); both paper rows clear their floor.
  Details in `docs/EVAL.md`, "September 23, 2026".
- Second, trace-enabled run (`RV_EVAL_TRACE=1`) → **1 passed in 193.50 s**, kept 69,
  used for the per-fact drop classification in `docs/EVAL.md`. Note for whoever runs
  this next: **llama-server did not survive the gap between the two runs** — port
  8091 was dead and had to be restarted (detached via `Start-Process`, ready in ~30 s)
  before the eval would go through. With no server the run **fails, it does not
  skip**: `assert llamacpp.available` at `tests/test_ai_eval.py:188`, in 2.8 s
  (verified by pointing `RV_LLM_SERVER_URL` at a dead port).

Durable lesson: **a loosened trust door needs an adversarial input pinned in the
test, not just the positive case.** All four review rounds on this branch found the
same shape — the new score behaved perfectly on the quote it was written for and
accepted a quote that was never in the source.
`test_a_quote_stitched_from_disjoint_page_regions_is_not_evidence` and
`test_a_quote_that_skips_source_text_is_not_verbatim` are those inputs; write one
before shipping any change that can only raise a score. The version that survived
returns 0.0 rather than a smaller number, so it is monotone-safe by construction.

Durable lesson: **for an evidence-matcher change, the row count is not the
evidence.** Whether a golden row flips depends on which quotes the model happened
to emit (this run's paper-01 quotes came back byte-verbatim, so containment already
scored them 1.0), and llama-server is not run-to-run reproducible here. The
deterministic HEAD-vs-tree comparison on a fixed probe is the measurement; the
replay's job is only to prove nothing was *lost*. Say so in the doc, and never
carry a number forward that the current session cannot re-derive — three figures
quoted from an earlier run in this session's own working notes (61→64, a
second 0.0→0.86 flip set) did not reproduce and were deleted rather than committed.

Next:
- Decided, not blocking: `edu-05`'s drops were classified offline from the trace —
  of 32 dropped facts across the run, **3 are matcher false rejects** (all `edu-05`,
  all quotes verbatim across consecutive transcript segments), 10 had no quote at
  all, 9 were paraphrases, 10 were refused by the claim-term gate on purpose. The
  matcher is no longer the main limiter of fact recall; model emission is.
- If the cross-segment case is ever worth fixing: composite consecutive segments into
  one span and require **exact containment** — no fuzzy measure. Measure how many
  live-library facts it recovers first; on the golden set the number is 3.

Blockers: none. Unpushed: `v2-hybrid-routing` is 22 commits ahead of its remote;
nothing was pushed this session. Owner-side items from the previous section were
all actioned in the section that follows this one — **read that section before
repeating any of it.**

## Library maintenance, the owner credential, and what re-extraction proved (2026-09-23)

Date: 2026-09-23
Phase: Phase 7 — fact accuracy / Evidence Ledger

Done:
- Deleted the two junk rows left over from the Android emulator test: **reel 30**
  (a 1,036-byte stub MP4 with no `moov` atom — the only `failed` reel and the only
  `dead` job in the queue) and **reel 31** (`instagram.com/reel/TESTEMULATOR/`, a
  fabricated URL that completed with 0 facts). Both through
  `DELETE /api/reels/{id}` so media, frames, embeddings and the FTS shadow row go
  with them. Library 30 → **28 reels**.
- Re-extracted **26, 27, 28** (the three document rows whose facts predate the
  matcher fix), owner-approved in session, after a consistent online snapshot:
  `D:/reelvault-backups/db_pre_maintenance_20260923_224707.sqlite` (30 reels /
  105 facts). Enqueued `classify_extract → embed → finalize`; extraction is
  DELETE-then-INSERT (`app/pipeline/stages.py:525`) so nothing duplicates. Nothing
  manual was at risk: **`user_corrected` is 0 for all 105 stored facts.**
- Result: 26 `8 → 8` (`resources='Eight P100 GPUs'` in, `difficulty='Research'`
  out), 27 `1 → 2` (gained `next_steps`, `difficulty` re-rolled from `Difficult`
  to `Ambitious`), 28 `5 → 8`. Library **105 → 109 facts**, document-sourced
  **15 → 16**.
- **None of that gain is attributable to the shipped matcher branch.** Every new
  document fact's quote is *verbatim contained* in its chunk, so `_sim` returns
  1.0 at the `len(a) >= MIN_STRONG_QUOTE_CHARS` disjunct
  (`app/knowledge/evidence.py:237-240`) and control never reaches
  `_local_coverage`. Sweeping those three rows for the only input the branch can
  act on — a document quote of `MIN_STRONG_QUOTE_CHARS` or longer that is **not**
  verbatim contained in any span — finds **none**. The +4 is model emission on a
  second pass, which is exactly the "the row count is not the evidence" lesson
  above, now observed against the owner's real rows instead of the golden set.
  `_local_coverage` stays proven only by the replay (paper-02 `topic`
  0.75 → 0.98).
- Added `scripts/set_owner_password.py` + `tests/test_owner_password.py` (3
  tests). This closes the last Phase-2 credential item: `ensure_owner_user`
  writes a plaintext password to `data/.owner_credentials.txt` **when it promotes
  an owner row that lacks one** (the upgrade path this database came in by) and
  `warn_if_owner_credentials_stale` logs until the file is gone, but **no route
  changes a password** (`/api/auth/*` stops at login/refresh/logout/sessions), so
  the file was simultaneously the only copy of the credential and the thing that
  had to be removed. The script reads the replacement through `getpass` (never
  argv, never echoed), refuses below 8 characters before touching the database,
  and unlinks the file only after the transaction carrying the new hash has
  committed — a refused password or a failed write therefore leaves the recorded
  credential intact.
- **Found, not fixed:** the *other* branch of `ensure_owner_user` — an empty
  `users` table — hashes a `token_urlsafe(10)` password, inserts it, and returns
  at `app/core/auth.py:130` **before** the file write. On a genuinely fresh
  install the owner's password is therefore stored nowhere and password login is
  unusable (the UI does not care: `/api/bootstrap` hands it the bearer token,
  which is why nobody noticed). Fixing it means either writing the file on that
  path too or forcing a claim-password first run — an owner decision about the
  first-run UX, out of scope for this section, and the reason the tests here
  create the file by hand rather than through `ensure_owner_user`.

Verification:
- Deletions checked in the database, not from the response body: `reels` 30 → 28
  with no row for 30 or 31, `reels_fts_shadow` holds **0** rows whose `reel_id` is
  absent from `reels`, `jobs` holds **0** non-`done` rows (the dead job left with
  reel 30), and the stub file is gone from `media/video/`.
- Re-extraction: queue drained to `pending: []` at 23:27:21 with all three rows
  `('completed','done')`. `find_evidence` re-scored offline against each reel's
  own stored spans returns **similarity 1.0** for every new document fact, and the
  value is locatable in the stored `documents.body_text` (`Eight P100 GPUs` →
  page-marked chunks 2 and 8). `POST /api/search` retrieves 26/27/28 for
  "P100 GPUs", "great work frontier" and "self-attention".
- `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_ai_eval.py` →
  **361 passed, 3 skipped in 21.89 s**, run against the final working tree.
  Targeted: `pytest tests/test_owner_password.py -q` → **3 passed in 1.38 s**.
- Review round on this diff (Jev is owner-waived; `caveman:cavecrew-reviewer` is
  the standing substitute) returned **0 blockers, 6 should-fix, 4 nits** and every
  one of its three file:line claims re-checked true. Fixed: the false
  "on first run" premise (both here and in the script docstring), a
  `verify_password` read-back guard that could not fail because it read
  pre-commit state on the same connection (deleted; the real guarantee is
  unlink-after-commit), the `RuntimeError` branch that printed "nothing changed"
  after the old hash was already gone, a third test that would have passed against
  an implementation destroying the credential (now it asserts the file survives a
  `LookupError`), and the over-scoped A/B sentence above. Rejected with reason:
  catching `sqlite3.Error`/`OSError` in `main` to print "nothing changed" — a
  commit-time `database is locked` or a failed unlink has an *unknown* outcome, so
  the traceback is the truthful report.
- Groq-key exposure measured, not assumed: the only Groq-shaped key is
  `RV_LLM_API_KEY` in `.env` (56 chars, `gsk_` prefix, `.env` confirmed gitignored
  via `git check-ignore`). The two `gsk_` matches in tracked files are a comment at
  `.env.example:15` and the literal `gsk_from_env_file` in
  `tests/test_llm_backend.py`. **Nothing to purge from the repository** — rotation
  happens at the provider and only the owner can do it.

Next:
- Owner-side, both needing a secret this agent must not handle: run
  `.venv/Scripts/python.exe scripts/set_owner_password.py` (it deletes
  `data/.owner_credentials.txt` itself on success), and rotate the Groq key, then
  paste the replacement into `.env` as `RV_LLM_API_KEY`.
- The ±4-fact movement above is **one** observation on a runtime that is not
  run-to-run reproducible. Re-extracting twice to separate signal from that noise
  is not worth a session; the useful residue is the finding that the matcher
  branch had no input to act on in the live library.

Blockers: none. Unpushed: `v2-hybrid-routing` is ahead of
`origin/v2-hybrid-routing` by the commits carrying this section (the script and
these docs); nothing was pushed, and `git status -sb` is authoritative for how
many.




## The zero-deadline number hid a parser bug (2026-09-25)

Date: 2026-09-25
Phase: Phase 7 — deadline intelligence / fact accuracy

Done:
- Went to measure "0 of 28 reels have a deadline" instead of repeating it. Result:
  **1 reel produces a deadline-hint sentence and it holds no date; 4 reels state a
  date a human would accept and none carries a hint word.** The two halves never
  meet, which is the real explanation. Provenance stated plainly: the only row with an
  explicit due date (`INVOICE 4500 DUE 15 SEPTEMBER`) is reel 29, a synthetic
  2,613-byte invoice PNG shared during the Android share-target test — so the library
  genuinely has no live deadline, and the finding is about scanner vocabulary, not a
  missed obligation.
- **Fixed a parser that invented years out of amounts.** dateutil is called with
  `fuzzy=True`, so any 4-digit token can become the year. Before/after run against
  `git show HEAD:app/knowledge/deadlines.py`, `today` = 2026-09-09:
  `"Deadline for the payment: invoice 4500 due 15 September"` → was **4500-09-15 at
  conf 0.85**, now rejected as text; `"invoice 2000 …"` → was **2027-09-15 with a
  reminder on 2027-09-10**, now rejected. One window guard
  (`today.year - 1 … today.year + 10`) placed where all three parse branches converge.
- **The first version of that guard was wrong and a review round caught it.** It
  checked `dt.year` *after* the rolling-year rewrite, so the 4500 case was blocked
  while the 2000 case walked through promoted to 2027 — the dangerous direction, since
  a year-4500 reminder can never fire (`reminder_iso <= now+48h`,
  `app/core/push.py:134-147`) but a 2027 one lands in the column and eventually does.
  The shipped guard keeps the year **as the source stated it** (`parsed_year`, captured
  before any rollover) and windows that.
- **Stopped the invention at its source**, which an earlier draft of this section had
  accepted as a limitation. dateutil's `default=today` supplies any field the text omits,
  so `"the deadline is 2029"` parsed to 2029-09-09 at confidence 0.85 with a reminder on
  2029-09-04 — and no year window can catch it, because 2029 is plausible. The numeric
  branch now requires the sentence to state **both** a day and a month
  (`_states_day_and_month`, `app/knowledge/deadlines.py:24-44`, applied at `:98`).
- **A review round on that gate found three leaks it had shipped with**, each measured
  before being fixed: the month alternation lacked word boundaries (`"mar"` in
  `"remarks"` opened the gate); the *month-name* branch had the same invention from the
  other side, reading `20` out of `2026` as the day so `"applications close September
  2026"` parsed to 2026-09-20 at 0.75 (lookarounds at `:116-124`); and `may` is both a
  month alias and the commonest modal in this corpus, which is why the rule is day
  **and** month, not day **or** month. Gating one branch alone would have left the class
  open while its own tests stayed green.
- **Residual, recorded not patched:** a duration still reads as a day-and-month pair —
  `"deadline 2029 in 1-2 days"` → 2029-02-01. Separating a pair from a range needs a
  unit-aware scan. Kept as a non-strict `xfail` so the suite states the §8 violation.
- `DEADLINE_HINTS` (`app/knowledge/deadlines.py:46-48`) has "apply by", "closes",
  "submit before" but **not "due"**, so an invoice line never reaches the rules
  fallback (`:223-224`). Left as an owner decision — and the entry point is broader
  than the fallback: `best_deadline` also parses any verified fact whose **field name**
  contains "deadline" or "due" (`:209`) with no hint gate, so only one of the two
  directions was ever screened.
- Measured OCR word-boundary collapse: **20 of 144 `ocr_results` rows on 9 of 28
  reels** carry a space-free run ≥40 chars (worst 94). Not our join
  (`app/pipeline/stages.py:344` joins boxes with a space). It does **not** break date
  parsing (`August25,2026` parsed) — it breaks **quote containment**, so those frames
  can only support facts fuzzily. 2 of 109 stored facts carry such a quote (fact 53
  reel 13, fact 67 reel 17). Two options costed in `docs/EVAL.md`; a space-insensitive
  OCR comparator is a third loosening of the trust door and was not attempted.
- Re-scored v0.1 criterion 4 (manual correction): `user_corrected` is **0 of 109**
  facts while `tests/test_phase5_corrections.py` covers the routes at HTTP level. Built
  and code-verified, **undemonstrated by a human** — and a hand-written correction is
  the only source of human labels this project will get for extraction quality.
- `docs/EVAL.md`: new 2026-09-25 section — method, both tables, before/after numbers,
  the two accepted limitations, and what cleanup the fix does and does not need.

Verification:
- `pytest tests/test_deadline_parsing.py -q` → **25 passed, 1 xfailed**: the 4500 regression
  (asserted through `best_deadline` as well as `parse_deadline`), the promoted-year 2000
  case the first guard missed, four bare-year forms that must stay text, and a
  parametrization proving the gate still parses `15 09 2026`, `15 09 26`, `15.09.2026`,
  `5.9`, `31st March`, `1 October 2026` and `20261015` — so it cannot quietly become a ban.
- `pytest tests/ -q --ignore=tests/test_ai_eval.py` → **374 passed, 3 skipped,
  1 xfailed**.
- **One flake observed and explained, not hidden**: on the first run of that suite
  `tests/test_reliability.py::test_truncated_video_fails_fast` failed, then passed in
  isolation (0.39 s) and again in a clean full run. It asserts `elapsed < 15` around
  real `ffmpeg`/`ffprobe` subprocess work (`tests/test_reliability.py:151`) while
  llama-server and the app are running on the same box, so it is a load-sensitive
  assertion, not a regression from this change. Left alone: raising the threshold to
  make a flake disappear destroys the signal it exists to catch.
- Before/after measured by loading `git show HEAD:app/knowledge/deadlines.py` beside
  the working tree and feeding both the same inputs, not by reading the diff.
- **No library cleanup needed**: `deadline_iso`, `reminder_iso`, `deadline_raw` are 0
  rows across all 28 reels, so no fabricated date was ever stored and no re-extraction
  is implied.

Durable lesson: **a feature that measures 0 on real data is a hypothesis, not a
result.** The number had been reported three times as "your content has no deadlines";
the deterministic path had never been run over the stored sources. One query loop
produced a vocabulary gap, a fabricated year-4500 parse, and — via the review round —
the promotion bug the obvious fix shipped with. A guard on a value that other code has
already rewritten is the same mistake as a guard on the wrong field.

Next:
- Owner decision only: widen `DEADLINE_HINTS` with "due" (cost: more sentences reach a
  parser that now carries a year window and a day-and-month rule), or accept that
  invoices and similar on-screen text stay outside deadline tracking.
- **The cross-segment transcript span is closed as measured, not deferred** — see the
  new table in `docs/EVAL.md`: live-library drops are recorded in `processing_events`,
  and of 20 classifiable records **zero** are the crossing-segment shape the fix would
  rescue (3 of 32 on the golden set). Not worth loosening the Evidence Ledger a third
  time for a fixture row.
- Unchanged: the merge question (read `git rev-list --count main..HEAD` for what it is
  today). Nothing else is queued.

Blockers: none. Unpushed: this work is in the commits that follow; `git status -sb` is
authoritative, and nothing was pushed.

## The v2 → main merge question, checked (2026-10-05)

Date: 2026-10-05 (this session spans Sep 23 → Oct 5; the sections above are dated by
the day their work actually ran)
Phase: release posture / merge decision

Done:
- Checked the merge rather than performing it. **`main` is a strict ancestor of
  `v2-hybrid-routing`: ahead with 0 behind as measured at `3d5db21` — a fast-forward, so
  no conflict is possible and `origin/main` would advance by FF too.** Every other local branch and
  both worktrees (`v2`, `phase4/5/6-agentic`, `v0-core`) report
  `git rev-list --count HEAD..<branch> = 0`, i.e. none of them holds unmerged work, so
  merging loses nothing that lives elsewhere.
- **Tested the rollback question instead of reasoning about it — and it is subtler than
  "one-way door".** `main` is schema v5, this branch is v9, and `SCHEMA_VERSION` appears
  nowhere outside `app/db/schema.py:20`: nothing compares the DB's recorded version to
  what the running code expects, `migrate()` only applies missing migrations. So a
  detached `main` worktree was run against a **copy** of the live database (28 reels /
  109 facts / versions 1–9) and every step succeeded: `migrate()` returned 5 and applied
  nothing, `keyword_search('transformer')` returned the same rows before and after an
  old-code `UPDATE`, the write persisted, and `facts` (109) / `reels_fts_shadow` (28)
  stayed consistent. The schema is genuinely compatible — v6 rebuilds only the search
  index, v7/v8 are additive, v9 rebuilds `facts` in one `BEGIN IMMEDIATE` while adding
  `evidence_page` and widening the `evidence_source` CHECK to include `document`.
  **The danger is the silence, not corruption:** `v0.1.0` accepts this database and then
  cannot *process* the 6 non-video rows, because its `STAGES` is the video chain and it
  has no `stages_for_kind` or document spans — enqueueing a paper row runs `media`
  against something with no media. Nothing errors, so nothing warns. A runnable v0.1
  fallback therefore needs a v0.1-era DB copy, not a `git checkout`.
  Probe artifacts were deleted (`git worktree remove`, temp DB + home removed) and the
  **live database was verified untouched** — no probe marker in reel 25, 28 reels,
  109 facts, versions 1–9.
- Confirmed both halves of AGENTS §2's "preserved by construction" rule on the actual
  runtime, not just the code: `content_kind TEXT NOT NULL DEFAULT 'video'`,
  `llm_backend = "llamacpp"`, and **`.env` does not set `RV_LLM_TEXT_BACKEND`**, so
  hybrid cloud routing is off in practice as well as by default.
- **Fixed the three AGENTS.md statements that the merge would have carried onto `main`
  as false.** The milestone header still described "`v0.1` on `main` … `v2` branch
  develops", naming a branch that is not the one carrying v2. The freeze list claimed
  Android was frozen while `4ea22cb`/`bc0c16b`/`0bcf823` had already built the APK,
  pinned its HTTP contract and verified it on an emulator. And §8's push freeze was
  gated on "deadline extraction evaluated on real data" — a condition the 2026-09-25
  measurement showed cannot be met, because there is no deadline content to evaluate.
  Each freeze line was re-checked against `git rev-list main..HEAD -- <path>`: Docker,
  `push.py`, `fetch.py`, `vendor/slm`, `auth.py`, `embedder.py` are all **0 commits** in
  the v2 range, so those claims were true and were left alone; `hybrid_search` already
  exists on `main`, and the only `search.py` change is a contentless-FTS correctness
  fix. No freeze was lifted by this edit except Android, which is lifted because the
  work is demonstrably already merged-bound.

Recommendation (unexecuted, owner's call): correct these docs → fast-forward `main` to
this branch's tip → tag the new tip (`v0.2.0`) before anyone builds Docker/Android from
`main`, since `v0.1.0` already pins the old tip and an untagged movable head is not a
release. `--no-ff` buys nothing here; squashing would destroy the per-commit rationale
this file keeps referring back to.

Verification:
- `git merge-base --is-ancestor main HEAD` → exit 0. `git rev-list --count main..HEAD`
  → 55; `HEAD..main` → 0. `git rev-parse origin/main` = `b8b0533` = `main`.
- Rollback probe executed with `RV_DB_PATH` pointed at `sqlite3.backup()` of the live
  DB: `migrate()` → 5 with nothing applied; `keyword_search` → `[28, 26]` before and
  after; `UPDATE reels SET summary=… WHERE id=25` → persisted through the v9 triggers;
  `facts` 109, `reels_fts_shadow` 28. Live DB re-checked afterwards: 28 reels, 109 facts,
  no probe marker.
- `git diff --shortstat main..HEAD -- <path>` per frozen component (numbers quoted
  above), and `git show main:app/knowledge/search.py | grep ^def` to confirm
  `hybrid_search` predates the branch.
- `pytest tests/ -q --ignore=tests/test_ai_eval.py` → **374 passed, 3 skipped,
  1 xfailed**. v0.1 gate files alone: `pytest tests/test_phase1_fixes.py
  tests/test_phase2_config.py tests/test_phase3_pipeline_proof.py
  tests/test_phase3_queue_failure.py tests/test_phase5_corrections.py
  tests/test_phase7_url_ingest.py -q` → **31 passed, 1 skipped**.

Next:
- Owner decision: execute the fast-forward and the tag (and separately, whether to push
  the commits this branch is ahead of `origin/v2-hybrid-routing` by — read it with
  `git status -sb`, because any number written here is wrong as soon as anything
  commits). Nothing in this session pushed anything.
- `"due"` in `DEADLINE_HINTS` remains an open owner decision from the previous section.

Blockers: none for the merge itself — it is mechanically a FF. The only real cost is
the rollback asymmetry above.

## The un-screened deadline direction, screened — it found four defects (2026-10-05)

Date: 2026-10-05
Phase: Phase 7 — deadline intelligence / fact accuracy

Done:
- Closed the thread the previous two sections left open: `best_deadline` parses any
  verified fact whose **field name** contains "deadline" or "due" (`:209`) with no hint
  gate, so sentences reach `parse_deadline` from two directions and only one had been
  screened. Screened both. **The field-name direction has 1 candidate of 109 facts**
  (reel 22, `field="deadline"`), and it produces **no** deadline before or after — its
  quote and value hold no date.
- **Screened the wrong corpus first, then the right one.** Hint sentences number 1 live
  and 9 golden; the quote path has no hint gate, so the real screen is every stored
  sentence: **4,140** of them (all `transcript_segments`, `ocr_results`, `documents`,
  captions, plus the golden set), old version vs new, outputs compared string-for-string.
  Result: **2 flips, both a fabrication removed, 0 added**; 54 sentences parse in both;
  the 6 golden hint dates and all 4 golden `date_text` cases are identical.
- **Defect 1: the month-name branch read months out of ordinary words.** It captured
  `[A-Za-z]{3,9}` and resolved it by 4-letter then 3-letter prefix, so `<digit> <word>`
  became a date whenever the word merely began with an alias — "6 separate steps" →
  September 6, "3 decimal places" → December 3, "5 novel ideas" → November 5. Two real
  ones in the owner's library: reel 8 OCR rows 40/41, `@kevinnaughtonjr 1.` parsed as
  **2027-08-01 @ 0.75** (`aug` cut out of `nAUGHTONJR`). Not a harmless floor: a quote
  parse is boosted to the fact's own confidence (`:215`), so the misread outranks a
  corroborated 0.5 value parse and becomes what is written to `deadline_iso` with a
  reminder. Latent on this library (reel 8's only fact is `field="projects"`, and the
  deadline columns are still 0 rows), one `deadline`-labelled fact away from live.
- **Defect 2: the day-and-month gate shipped an hour earlier silently broke the OCR
  dates `docs/EVAL.md` claims are fine.** Wrapping the month alternation in `\b…\b` also
  rejected `August25` — `\b` fails between `t` and `2` since both are word characters —
  and `_STATED_DAY`'s `\b` rejected the day glued to its month. `August25,2026` returned
  None on HEAD while the Sep 25 section asserted it "parsed correctly". Nothing caught
  it because the claim was prose, not a test.
- **Fixed 1:** branch 2 now matches `_MONTH_TOKEN` (the gate's own closed alternation,
  bounded so letters may not continue it) and its 4-/3-letter prefix lookups are gone.
  `separate`, `decimal` and `naughtonjr` supply no month; `Aug` and `Sep` alone still do.
- **Defect 3 — the one my own first version of this fix shipped with, found in review.**
  Restoring the collapsed dates looked like swapping `\b` for letter/negative-digit
  lookarounds. It is not, because `\b` was doing two different jobs. Relaxing the day
  rule let a digit glued to a **letter** supply the day, so `may` plus `11pm` produced
  **2027-05-09 @ 0.85 with a reminder**; relaxing the month rule let a year glued to a
  month name supply the month, so `"August2026 meeting, closes 5"` produced
  **2027-08-05 @ 0.85**. Both measured against HEAD, and both were *worse than the code
  I started from*. `\b` therefore stays on both independent tests and the OCR collapse
  is allowed only as a **pair** — `_GLUED_MONTH_DAY`, month abutting 1-2 digits in either
  order — which restores `August25,2026` / `Sep30,2026` / `DUE15SEPTEMBER` / `25august`
  without opening the sentence to any stray digit.
- **Defect 4 — a crash older than this work, found by the same review round.** Branch 2's
  `today.replace(month=…, day=…)` was unguarded, so an impossible stated date raised
  `ValueError`: `"deadline feb 30"` **already crashed at HEAD**, and `"deadline 30 feb"`
  joined it the moment defect 1's fix stopped the branch swallowing "deadline 30" as a
  month name. `app/pipeline/stages.py:509` calls this with no `try` around it, so one
  such OCR line failed the entire extraction stage. Now returns unparseable text.
- **The first regression list was mostly fake, and review caught that too.** Nine
  parametrized "regression tests" of the form `"deadline 3 decimal places"` passed at
  HEAD as well, because the hint word matched first and its prefix lookup missed. Only
  the bare shapes bite; the file now has eight of them plus the two live OCR ones, the
  six glued-digit shapes, and the four crash shapes.
- **Defect 5: a clock token supplied the day, and the pipeline's own `[MM:SS]` prefix was
  the clock.** `"deadline 12:30 in june"` → **2027-06-09T12:30 @ 0.85** — month from
  `june`, hour from the clock, **day borrowed from `today`**, because `\b` counted `12`
  and `30` as standalone numbers and dateutil filled what the text never stated. Since
  `stages.py:381-382` prefixes every transcript and OCR line, the normal shape
  `"[00:15] OCR: deadline in june"` had the same hole, and even a correct date picked up
  the marker's minute: edu-03's saved quote stored **2026-10-05T00:15**. Now a number
  touching a colon states minutes not a day, and the parser strips ReelVault's own line
  marker first; a time the content states survives (`"[01:20] deadline 15 September at
  12:30"` → 15 Sep 12:30).
- **Screened the next family and stopped, because it is a decision not a bug:**
  `_NUMERIC_DATE_SHAPE` accepts `d.d` / `d/d` for its own sake, so **23 stored sentences**
  with no month name anywhere parse to a confident date — `5.4 Regularization` →
  2026-10-05, `REVERSE 03/07` → 2027-07-03, `(3.5 days).` → 2026-10-03, `GLM-5.2` →
  2027-05-03 — mostly in the two paper rows, i.e. the v2 document path. **Reachable
  today: 0**, none carries a hint word and the field-name fact holds no numbers. Telling a
  section number from `last date 5.9` (a pinned positive, and project dayfirst policy) is
  not a regex question, so it is recorded in `docs/EVAL.md` as the owner's §8 ambiguity
  call rather than narrowed unilaterally. This is the fourth successive *latent* family in
  this module — each measures zero on today's library and each goes live the moment the
  library contains real deadline content.
- **Added the stage-level guard the whole family lacked.** Five shapes (bare year, clock
  token, alias inside a word, impossible date, the `[MM:SS]` prefix) on one reel plus one
  real date, asserted through `stage_classify_extract` so the columns the scanner reads are
  what's checked, with the positive control so a dead stage can't pass. Shown to fail
  against `fae7028`'s parser — it wrote `deadline_raw = "[00:05] OCR: deadline in june"`,
  the invented date beating the real `CLOSES OCT 5` — and to pass on HEAD.
- Recorded in `docs/EVAL.md` as a new dated section, with pointers on the two Sep-25
  lines this supersedes rather than silently rewriting them.

Verification:
- `pytest tests/test_deadline_parsing.py tests/test_deadlines.py
  tests/test_deadline_reminder.py tests/test_pipeline.py -q` → **92 passed, 1 xfailed**.
- `pytest tests/ -q --ignore=tests/test_ai_eval.py` → **406 passed, 3 skipped,
  1 xfailed** (374 → 406; +32 are the new cases, incl. the stage-level guard).
- `pytest tests/test_phase1_fixes.py tests/test_phase2_config.py
  tests/test_phase3_pipeline_proof.py tests/test_phase3_queue_failure.py
  tests/test_phase5_corrections.py tests/test_phase7_url_ingest.py -q` → **31 passed,
  1 skipped** — the v0.1 gate is untouched by a deadline-parser change.
- Before/after by loading `git show HEAD:app/knowledge/deadlines.py` beside the working
  tree and feeding both identical inputs — every number above came out of that, none
  from reading the diff. Re-screened the full 4,140-sentence corpus **after** the
  redesign, not only before it: still 2 flips, both removals, 0 added, 0 raises. The
  pinned Sep-25 negatives all still stay text; live DB opened read-only
  (`file:data/reelvault.db?mode=ro`) throughout.
- `caveman:cavecrew-reviewer` on the diff (the accepted substitute for the Jev gate —
  `jev_review` still returns `AI Gateway authentication failed`). It found defects 3 and
  4 and the weak test list; all three were fixed and re-measured.

Uncertain / not done:
- `"July13."` (reel 16 row 95) still returns None — the pair rule opens the gate, but a
  glued month+day **without** a year survives neither dateutil nor branch 2, which needs
  whitespace. Fixing it means an OCR-aware split, not a wider regex.
- `"3.5 august deadline"` still → `2027-08-03 @ 0.85`: the clock fix closed the `:`
  family, the `.` family is the 23-sentence policy question above. Whether `deadline_iso`
  should ever carry a time-of-day at all is still unpinned by the suite — it now can only
  come from source content, not from the marker, which is the narrower version of the
  same open question.
- The field-name direction's **value-only fallback** parses the model's own normalized
  value at 0.5 when the quote holds no date (B4, `7130b4c`). It is the one path where
  model text alone can set `deadline_iso`, against §8's "never let model output directly
  determine a normalized deadline date". Zero facts exercise it. **Deliberately not
  patched**: B4 was an intentional decision to stop dropping valid deadlines, so
  reversing it is the owner's call, not a measurement.

Next:
- Owner decisions, unchanged in kind: `"due"` in `DEADLINE_HINTS`; the B4 value-only
  trust question above; whether the bare `d.d`/`d/d` pair form should require a year or a
  same-sentence cue (costs a pinned positive; the 23-sentence family above is what it
  buys back); the fast-forward + tag + push.

Blockers: none.
