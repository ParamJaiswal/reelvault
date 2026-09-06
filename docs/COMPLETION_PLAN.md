# ReelVault — Finish Plan

How to take project from "works" to "done". No code yet. Order matters.

---

## Phase 0 — Fix review bugs (small, do first)

1. Evidence matcher hole — short quote substring = full score. Add min length floor. `app/knowledge/evidence.py`
2. Confidence score — fake 0.25 timestamp weight when t_s is None. Score real. `app/knowledge/evidence.py`
3. Bootstrap token crash — empty users table → 500. Add guard. `app/core/auth.py`
4. Queue claim race — UPDATE no heartbeat re-check. One line. `app/db/queue.py`
5. Tests for all four in `tests/`.

Effort: 1 session. Risk: low.

## Phase 1 — Security tighten

1. CORS `*` → PWA origin only. `config.py`
2. Refresh-as-bearer: limit or document theft-detection gap. `auth.py`
3. Hardcoded `D:/reelvault` paths → env fallback, startup check. `config.py`
4. Owner credentials file: warn if unchanged after first login.

Effort: 1 session. Risk: low.

## Phase 2 — Make features real (currently half-done)

1. **HTTPS share-target**: verify PWA install + push on phone via Tailscale. Test on real device.
2. **Docker build**: build image, run, hit healthz, test pipeline inside container. Fix path bugs found.
3. **Android app**: install SDK, build APK, test share-intake → `/ingest/url` → summaries flow.
4. **yt-dlp login-wall**: verify graceful metadata-only mode works, no crash.

Effort: 2-3 sessions. Risk: medium — first real device/container tests, expect surprises.

## Phase 3 — SLM decide (big fork)

5.3M checkpoint = toy. Two roads:

- **Road A (recommended)**: drop custom SLM. Router stays OFF. Keep llama.cpp Qwen path only. Delete route later if never used. Saves weeks.
- **Road B**: fine-tune properly — real dataset (thousands examples), eval harness, benchmark vs Qwen baseline. Only if you need tiny fast model for extraction.

Pick ONE. Do not half-do both.

## Phase 4 — Polish + done

1. Retention job actually runs (media 30 days)?
2. Docs: update DEPLOYMENT.md after Docker verified.
3. Test suite green, README honest.
4. Tag v1.0.

---

## Guidance rules (for future sessions)

- Run tests before + after changes.
- Schema change = new migration. Always.
- Never trust model output: evidence check, schema whitelist, deterministic dates.
- Secrets never in logs.
- One thing per session. Verify live, then update SESSION_HANDOFF.md.
- If phase blocked, note blocker, move to next phase. No idle waiting.

## Done means

- [ ] Phase 0-1 merged, tests green
- [ ] Docker image runs pipeline end-to-end
- [ ] Android APK installs, share-intake works
- [ ] Push + HTTPS verified on phone
- [ ] SLM road picked and executed
- [ ] v1.0 tagged
