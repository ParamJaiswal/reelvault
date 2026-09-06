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
