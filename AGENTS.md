## ReelVault — Agent Guidance

**Last updated:** September 6, 2026  
**Active milestone:** `v0.1` — useful local personal prototype

Read this file before changing code. Then read `docs/SESSION_HANDOFF.md` for the latest verified session state.

---

## 1. Product direction

ReelVault is a local-first tool that turns short videos—primarily Instagram Reels—into a searchable, evidence-backed personal knowledge base.

### v0.1 promise

> Import a short video, extract evidence-backed notes, and find them later.

The core loop is:

```text
Video upload or best-effort Reel URL
→ media extraction
→ transcription + OCR
→ structured extraction
→ Evidence Ledger validation
→ FTS search
→ inspect quote and jump to source timestamp
```

### Primary user

One person who saves useful reels/videos and cannot reliably find useful information later.

### v0.1 success criteria

The prototype is successful only when the owner has processed at least 20 real reels/videos and can:

1. Find previously imported content using search.
2. Inspect a fact's supporting quote.
3. Jump from the quote to the relevant video timestamp when available.
4. Correct or remove bad AI output manually.
5. Honestly say ReelVault saves time compared with scrolling saved content.

---

## 2. Current architecture

```text
D:\reelvault\
├── app/
│   ├── api/main.py          FastAPI server, worker, and endpoints
│   ├── core/
│   │   ├── auth.py          Sessions, JWT, refresh rotation, roles
│   │   ├── push.py          Web Push and deadline scanner
│   │   ├── backups.py       Encrypted DB snapshots and media mirror
│   │   ├── llama_manager.py llama-server lifecycle management
│   │   ├── config.py        pydantic-settings configuration
│   │   └── logging_setup.py JSON logging and secret redaction
│   ├── db/
│   │   ├── schema.py        SQLite WAL schema and migrations
│   │   └── queue.py         Durable queue: retries, DLQ, heartbeats
│   ├── ingest/adapters/     Ingestion adapter interfaces
│   ├── pipeline/            Fetch, browser fallback, media, orchestration
│   ├── ai/                  LLM, STT, OCR, embedder abstractions
│   ├── knowledge/           Schemas, evidence, search, RAG, deadlines
│   └── static/              Vanilla HTML/CSS/JS PWA frontend
├── android/                 Kotlin mobile client
├── models/                  Qwen GGUF and Business-SLM artifacts
├── llamacpp/                Local llama-server binary
├── media/                   Processed media artifacts
├── data/                    SQLite DB, auth data, logs, keys, certs
├── vendor/slm/              Vendored Business-SLM source
├── tests/                   Pytest suite
├── scripts/                 Local utility scripts
├── docs/                    Handoffs, deployment, plans, evaluation docs
├── Dockerfile               CUDA container build
└── Start-ReelVault.ps1      Windows startup script
```

### Keep, do not rewrite for v0.1

- SQLite WAL database
- Existing durable queue
- Existing auth/session system
- Existing llama.cpp / Qwen runtime integration
- Existing logging and secret redaction
- Existing backup implementation
- Existing schema/migration structure

Do not replace these with a simpler alternative merely to reduce code. Fix scoped defects; preserve working investment.

---

## 3. Active v0.1 scope

### In scope

- Local video upload as the reliable ingestion path
- Best-effort Reel URL ingestion through the existing compliant adapter path
- Audio extraction, transcription, OCR, and artifact persistence
- Evidence-backed fact extraction
- Deterministic deadline parsing
- FTS5 search over summaries, facts, transcript, and OCR text
- Reel detail UI with visible evidence
- Manual correction of summary, category, facts, and deadlines
- Golden-set extraction evaluation
- Backup restore and retention verification

### Explicitly frozen until v0.1 validation

Do not delete these components. Do not add features to them unless the active session explicitly authorizes it.

- Android build, APK release, and Android feature work
- Docker build/debug/deployment work
- Web Push expansion or phone push verification
- Playwright/Instagram login-fallback expansion
- Custom Business-SLM training, routing, or fine-tuning
- New auth features, roles, invitations, or OAuth
- Vector-search/embedding expansion beyond existing stable behavior
- Bulk Instagram ingestion or saved-post scraping
- Multi-user product work

### Business SLM decision

`RV_SLM_ENABLED` remains `false` for v0.1.

The vendored 5.3M Business-SLM checkpoint is undertrained and is not a production extraction model. Do not fine-tune it, improve its router, or make it the default. Continue using the existing Qwen + llama.cpp path for local extraction.

---

## 4. Source-of-truth rules

1. `docs/SESSION_HANDOFF.md` is the source of truth for the most recent verified state.
2. This file describes intended architecture and v0.1 policy, not proof that a component currently works.
3. Any status older than September 6, 2026 must be verified before relying on it.
4. Never claim Docker, Android, push, backup restore, or a pipeline stage works without running its relevant verification command.
5. Never trust model output without schema validation and Evidence Ledger validation.

---

## 5. Start, health, and tests

### Start llama-server

The current local model runtime is llama.cpp. Do not add Ollama for v0.1 unless the project owner explicitly approves a runtime migration.

```powershell
D:\reelvault\llamacpp\llama-server.exe -m D:\reelvault\models\qwen2.5-3b-instruct-q4_k_m.gguf --port 8091 -ngl 33 --jinja
```

The application may auto-start it when the relevant config setting is enabled.

### Start application

```powershell
D:\reelvault\Start-ReelVault.ps1
```

Or:

```powershell
cd D:\reelvault
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 0.0.0.0 --port 8756
```

### Verify services

```powershell
curl http://127.0.0.1:8756/healthz
curl http://127.0.0.1:8091/v1/models
```

### Run tests

```powershell
cd D:\reelvault
.venv\Scripts\python.exe -m pytest tests\ -q --ignore=tests/test_ai_eval.py
```

### Check Docker build only when container work is active

```powershell
wsl.exe -d Ubuntu -u root -- bash -lc 'tail -20 /mnt/d/reelvault/data/docker_build3.log 2>/dev/null'
```

---

## 6. Configuration and path rules

Configuration is defined in `app/core/config.py` using pydantic-settings. Read config through `settings`; do not scatter environment reads throughout the codebase.

### Path policy

- Windows defaults may use the existing `D:\reelvault\...` locations.
- Production code must obtain paths from configuration, not hardcode them in pipeline, API, or model code.
- Alternate locations must work through environment variables.
- Never assume the current working directory.
- New path behavior requires a startup validation error that clearly says what is missing or unwritable.

Key environment variables:

```text
RV_DB_PATH
data/reelvault.db

RV_MODELS_DIR
D:/reelvault/models

RV_MEDIA_DIR
D:/reelvault/media

RV_LLM_SERVER_URL
http://127.0.0.1:8091/v1

RV_SLM_ENABLED
false

RV_BACKUP_DIR
D:/reelvault-backups
```

Do not log secrets, tokens, passwords, cookies, VAPID private keys, or backup passphrases.

---

## 7. Implementation conventions

### Code placement

- API endpoints and worker orchestration: `app/api/main.py`
- Configuration: `app/core/config.py`
- Auth: `app/core/auth.py`
- Database schema and migrations: `app/db/schema.py`
- Queue behavior: `app/db/queue.py`
- Pipeline stages: `app/pipeline/`
- Provider abstractions: `app/ai/providers.py`
- Extraction Pydantic schemas: `app/knowledge/schemas.py`
- Evidence validation: `app/knowledge/evidence.py`
- Search: `app/knowledge/search.py`
- Date/deadline logic: `app/knowledge/deadlines.py`
- Frontend: `app/static/`
- Tests: `tests/`

### Schema changes

Any database schema change requires all of the following:

1. Edit `app/db/schema.py`.
2. Add a forward migration.
3. Bump `SCHEMA_VERSION`.
4. Add migration and behavior tests.
5. Confirm existing databases upgrade successfully.

### Tests

- Run the full relevant test suite before and after each scoped change.
- Add a focused regression test for every bug fix.
- Use fixtures and pytest; do not use one-off inline scripts as a substitute for tests.
- A change is incomplete if expected failure behavior is untested.

### Scope discipline

- One focused phase per session.
- Touch only files required by the scoped change, plus required tests, configuration, migrations, and documentation.
- Keep functions cohesive and testable. Split functions when they mix responsibilities or become difficult to understand; do not apply arbitrary line-count rules.
- Do not add a dependency, inference runtime, cloud service, or model without explicit approval.
- Do not refactor unrelated components while fixing a scoped bug.

---

## 8. AI and evidence rules

### Non-negotiable trust policy

A model-generated claim is not a fact until it is supported by source evidence.

For every extracted fact:

1. Store the claim.
2. Store a purported verbatim quote.
3. Match the quote against transcript, OCR, caption, or other approved source text.
4. Enforce a minimum quote length.
5. Drop facts that do not meet evidence criteria.
6. Use a timestamp only when it is actually supported by a source segment/window.

Do not fabricate a timestamp confidence bonus when `t_s` is absent.

### Extraction rules

- Use the existing Qwen/llama.cpp route for v0.1.
- Use low temperature, normally `0`.
- Prefer server-supported structured JSON/schema constraints where available.
- Keep extraction schemas small.
- Separate classification/summary from detailed fact extraction if one large prompt proves unreliable.
- Allow at most one malformed-output retry; then save the source artifacts and fail extraction gracefully.
- Never let model output directly determine a normalized deadline date.

Recommended v0.1 extraction shape:

```json
{
  "category": "job|recipe|fitness|finance|education|other",
  "summary": "Short one-line summary",
  "facts": [
    {
      "claim": "Concise factual claim",
      "quote": "Verbatim supporting source text",
      "t_s": 12.4
    }
  ],
  "deadlines": [
    {
      "what": "What the deadline applies to",
      "date_text": "Original spoken/displayed date text",
      "quote": "Verbatim supporting source text"
    }
  ]
}
```

### Deadlines

- The model may extract `date_text` only.
- Deterministic application logic parses and normalizes dates.
- Unparseable or ambiguous dates are retained as text or flagged for review; they are not silently invented.
- Reminder/push behavior remains frozen until deadline extraction is evaluated on real data.

### STT and OCR

- Preserve raw or normalized source artifacts: transcript segments, OCR observations, source timestamps, and caption text.
- If changing STT providers, first verify the provider API and add provider-level tests.
- Do not implement thresholds based on fields that the chosen provider does not expose.
- Optimize speed only after measuring real pipeline time on representative reels.

---

## 9. Evaluation policy

No prompt, model, OCR, STT, evidence-matcher, or extraction-schema change is complete without evaluation.

### Golden set

Maintain manually labeled real reels in `tests/golden/`.

Start with 10 reels, then grow to 20+.

The set should include:

- recipe content
- fitness/workout content
- job, scholarship, or deadline content
- educational content
- OCR-heavy content
- music/noise-heavy content
- Hinglish or other mixed-language content if relevant to actual use

Each item should record expected category, key facts, deadlines, and known hard cases.

### Required metrics

Track results in `docs/EVAL.md`:

- category accuracy
- fact precision
- fact recall
- evidence validation failure rate
- deadline extraction/parsing accuracy
- malformed JSON/schema failure rate
- processing duration

A regression requires explanation or reversion. Do not tune by intuition alone.

---

## 10. Ingestion policy

### Reliable path

Local file upload is the supported and reliable v0.1 ingestion method.

### Best-effort path

Instagram Reel URL ingestion uses the existing adapter and yt-dlp path. It can fail because of login walls, rate limits, or upstream Instagram changes.

Requirements for URL ingestion:

- Failure must be graceful.
- Record a readable failure reason.
- Do not crash the worker.
- Offer manual video upload as the fallback.
- Do not build bulk scraping, saved-post scraping, authentication bypasses, or aggressive retry behavior.

Playwright browser fallback remains gated and frozen for v0.1. Do not expand it without explicit approval and compliance review.

---

## 11. v0.1 phased plan

### Phase 0 — Verify baseline

1. Read `docs/SESSION_HANDOFF.md`.
2. Run tests.
3. Verify app health.
4. Record actual status and blockers in the handoff.

**Done when:** current claims are verified, not assumed.

### Phase 1 — Correctness fixes

Implement and test:

1. Evidence matcher minimum quote-length floor.
2. Accurate confidence behavior when no timestamp exists.
3. Empty-user/bootstrap-token guard in auth.
4. Queue claim update heartbeat re-check.

**Done when:** focused regression tests and full relevant suite pass.

### Phase 2 — Configuration and security minimum

1. Remove hardcoded operational path assumptions outside configuration.
2. Add clear startup checks for required paths/dependencies.
3. Restrict CORS to intended origins.
4. Warn if bootstrap/owner credentials remain unchanged after initial setup.

**Done when:** alternate configured paths work and failures are explicit.

### Phase 3 — Core pipeline proof

Process at least five local video files through the existing upload endpoint and durable queue.

Verify:

- media files are stored
- audio/frame extraction completes
- transcript and OCR artifacts persist
- extraction completes or fails cleanly
- worker stays alive after corrupt input
- FTS search can retrieve processed content

**Done when:** five videos complete end-to-end or fail cleanly without worker failure.

### Phase 4 — Golden evaluation and extraction hardening

1. Create initial golden set.
2. Run evaluation and save baseline metrics.
3. Improve structured extraction and evidence validation only based on measured failures.
4. Re-run evaluation after each change.

**Done when:** baseline is documented and fake/unsupported facts are demonstrably dropped.

### Phase 5 — Evidence-visible UI and manual correction

The reel detail UI must show:

- video
- transcript/OCR context
- claim
- quote
- evidence source type
- timestamp and click-to-seek when available
- edit/delete controls for bad facts
- category/summary correction
- deadline correction or removal

**Done when:** a user can correct poor AI output without editing code or rerunning the pipeline.

### Phase 6 — Personal-use validation

Use the application for two weeks and process at least 20 real reels/videos.

Write `docs/V0_RETROSPECTIVE.md` answering:

1. Did search recover content that would otherwise be lost?
2. Did evidence make facts trustworthy?
3. Was upload or URL ingestion the practical path?
4. What failed most often?
5. What single next feature would save the most time?

**Done when:** the next feature is selected from observed usage, not architecture preference.

### Phase 7 — One post-v0.1 expansion

Select exactly one based on observed pain:

| Observed pain | Candidate next work |
|---|---|
| Phone intake is difficult | Tailscale HTTPS and PWA share target |
| Another computer needs to run it | Docker validation |
| Native share-sheet UX is necessary | Android build/test |
| FTS misses useful semantic queries | Embeddings/vector search |
| Deadlines are accurate and reminders are valuable | Web Push validation |
| Queue jobs fail repeatedly | Queue monitoring/hardening |
| URL import is essential | Best-effort ingest reliability work | <-- SELECTED for Phase 7 (owner pain: importing real reels by URL)
| Facts are inaccurate | Expand golden set and improve extraction/evidence |

Do one item only. Update this plan before starting it.

---

## 12. Operations and data safety

- SQLite WAL is appropriate at personal scale; treat `database is locked` incidents as production bugs and log enough context to diagnose them.
- Retention must be verified, not merely implemented.
- A backup is not considered valid until it has been restored into a clean location and the restored app can read data and search it.
- Monitor disk use as media, audio, and frames accumulate.
- Queue failures must be visible through reel/job status and logs.

---

## 13. Session handoff format

At the end of every session, update the final section of `docs/SESSION_HANDOFF.md` with:

```text
Date: YYYY-MM-DD
Phase: <active phase>
Done:
- <completed item>
Verification:
- <test/command and result>
Blockers:
- <blocker or none>
Next:
- <single next scoped action>
```

Do not state that a feature works unless the verification command or test result is recorded.
