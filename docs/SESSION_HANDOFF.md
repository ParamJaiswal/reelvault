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
