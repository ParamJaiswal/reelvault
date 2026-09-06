# ReelVault — Agent Guidance

> **Read this first.** It orients any agent (or future you) to the project's purpose, architecture, conventions, and current state in ~5 minutes.

---

## 1. What is ReelVault?

A **fully-offline, privacy-first platform** that turns Instagram Reels into a searchable, evidence-backed personal knowledge base.

**Core loop:** `Reel URL → download → transcribe (Whisper) → OCR → classify → extract structured facts (with proof quotes) → embed → searchable KB`

**Key differentiators:**
- **Evidence Ledger** — every fact carries its verbatim source quote + timestamp; hallucinations auto-dropped
- **Opportunity Radar** — jobs/deadlines ranked by urgency
- **Self-healing AI layer** — providers detect broken runtimes and fall back automatically
- **100% local** — no cloud AI calls, no telemetry, no accounts required

---

## 2. Project layout

```
D:\reelvault\
├── app/
│   ├── api/main.py          FastAPI server + pipeline worker + all endpoints
│   ├── core/
│   │   ├── auth.py          Sessions, refresh rotation, roles, JWT
│   │   ├── push.py          Web Push (VAPID) + deadline scanner
│   │   ├── backups.py       Encrypted DB snapshots + media mirror
│   │   ├── llama_manager.py Auto-start/stop llama-server
│   │   ├── config.py        pydantic-settings (env-driven)
│   │   └── logging_setup.py JSON logs, secret redaction
│   ├── db/
│   │   ├── schema.py        SQLite WAL schema + migrations (v4)
│   │   └── queue.py         Durable job queue (retries/backoff/DLQ/heartbeats)
│   ├── ingest/adapters/
│   │   └── base.py          IngestionAdapter interface + URL/file/watch-folder/share-target
│   ├── pipeline/
│   │   ├── fetch.py         yt-dlp + Playwright fallback (disposable IG account)
│   │   ├── ig_browser.py    Playwright reel downloader
│   │   ├── media.py         ffprobe/audio/frame sampling
│   │   └── stages.py        7-stage pipeline orchestrator
│   ├── ai/
│   │   ├── providers.py     LLM/STT/OCR/Embedder abstraction + AutoTranscriber
│   │   └── slm_provider.py  Business-SLM wrapper (5.3M vendored transformer)
│   ├── knowledge/
│   │   ├── schemas.py       Pydantic extraction schemas
│   │   ├── evidence.py      Evidence Ledger matcher
│   │   ├── search.py        FTS5 + vector hybrid search
│   │   ├── assistant.py     Local RAG assistant with [R#id] citations
│   │   └── deadlines.py     Deterministic date parsing + reminders
│   └── static/              PWA frontend (HTML/CSS/JS, vanilla)
├── android/                 Kotlin mobile app (share-intake, login, summaries, WebView detail)
├── models/                  Qwen2.5-3B GGUF + business SLM weights
├── llamacpp/                CUDA llama-server binary
├── media/                   Processed reels (video/audio/frames)
├── data/                    SQLite DB, auth, push keys, certs, logs
├── vendor/slm/              Vendored business SLM source
├── tests/                   40 pytest cases (unit + integration + prod)
├── scripts/                 make_cert.py, make_test_reels.py
├── docs/                    DEPLOYMENT.md, SESSION_HANDOFF.md, etc.
├── Dockerfile               Multi-stage CUDA build (llama.cpp compiled in-builder)
└── requirements-docker.txt
```

---

## 3. How to run

```powershell
# 1. SLM server (GPU) — auto-starts with the app if auto_start_llama=true
D:\reelvault\llamacpp\llama-server.exe -m D:\reelvault\models\qwen2.5-3b-instruct-q4_k_m.gguf --port 8091 -ngl 33 --jinja

# 2. App + worker (double-click OR command)
D:\reelvault\Start-ReelVault.ps1
# OR:
cd D:\reelvault && .venv\Scripts\python.exe -m uvicorn app.api.main:app --host 0.0.0.0 --port 8756

# 3. Open http://127.0.0.1:8756
```

**First run:** owner credentials are auto-written to `data\.owner_credentials.txt`.

**Tests:**
```powershell
cd D:\reelvault && .venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_ai_eval.py
```

---

## 4. Architecture decisions (why it is the way it is)

| Decision | Rationale |
|---|---|
| **SQLite WAL** | Zero-ops at personal scale; durable queue + KB in one file |
| **Custom durable queue** | Retries/backoff/DLQ/heartbeats without Redis dependency |
| **IngestionAdapter interface** | Instagram's API doesn't expose Saved; compliant adapters behind one swap point |
| **yt-dlp → Playwright fallback** | Anonymous fetch first; disposable-account browser only if login-walled (env-gated) |
| **Evidence Ledger** | Every fact must fuzzy-match back to transcript/OCR/caption or be dropped |
| **Session chains + reuse-detection** | Refresh rotation creates child rows; old rows retained with revoked=2 so replay = theft → family revoked |
| **Bootstrap token survives session ops** | Owner can't lock themselves out; session revocation touches only auth_sessions |
| **Business SLM OFF by default** | 5.3M checkpoint is a 6-step smoke train (ppl~218); flip `RV_SLM_ENABLED=true` after fine-tuning |

---

## 5. API surface (key endpoints)

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | public | Password → access + refresh tokens |
| `POST /api/auth/refresh` | public | Rotate refresh token |
| `POST /api/auth/logout` | access | Revoke current session |
| `GET /api/auth/sessions` | viewer | List sessions |
| `DELETE /api/auth/sessions/{id}` | viewer | Revoke a session |
| `POST /api/admin/users` | owner | Create viewer/admin |
| `POST /ingest/url` | viewer | Submit reel URL (mobile app) |
| `POST /ingest/upload` | viewer | Upload video file (mobile app) |
| `GET /summaries` | auth | Compact list (mobile app) |
| `GET /summaries/{id}` | auth | Compact detail (mobile app) |
| `GET /api/push/public-key` | public | VAPID public key |
| `POST /api/push/subscribe` | viewer | Register push subscription |
| `POST /api/admin/backup` | admin | Trigger backup now |
| `POST /api/reels`, `GET /api/reels/{id}`, etc. | auth | Standard CRUD |

---

## 6. Environment variables (`.env` or system)

| Var | Default | Purpose |
|---|---|---|
| `RV_DB_PATH` | `data/reelvault.db` | DB location |
| `RV_MODELS_DIR` | `D:/reelvault/models` | Model weights |
| `RV_MEDIA_DIR` | `D:/reelvault/media` | Processed media |
| `RV_LLM_SERVER_URL` | `http://127.0.0.1:8091/v1` | SLM endpoint |
| `RV_SLM_ENABLED` | `false` | Enable business SLM route |
| `RV_BACKUP_PASSPHRASE` | *(empty)* | Encrypt backups |
| `RV_BACKUP_DIR` | `D:/reelvault-backups` | Backup destination |
| `IG_USERNAME` / `IG_PASSWORD` | *(empty)* | Disposable IG account for Playwright fallback |

---

## 7. Current state (Aug 26, 2026)

- **40/40 tests pass** (unit + integration + prod hardening)
- **Auth v2** live: sessions, rotation, theft-detection, roles — all verified
- **Web Push** live: VAPID keys generated, deadline scanner running hourly
- **HTTPS** live: self-signed cert generated; Tailscale recommended for phone
- **Backups** live: daily encrypted snapshots to `D:\reelvault-backups`
- **Mobile API** live: `/ingest/url`, `/ingest/upload`, `/summaries`
- **Android app** code complete (Kotlin): share-intake, Keystore token storage, summaries list, WebView detail — needs Android SDK to build
- **Docker** build in progress in WSL2 (CUDA multi-stage, first run ~30 min)

---

## 8. Conventions for agents

- **All paths are absolute** (`D:\reelvault\...`) — never assume cwd
- **Tests live in `tests/`** — run with pytest, not inline scripts
- **Schema changes = new migration version** — edit `app/db/schema.py`, bump `SCHEMA_VERSION`
- **New endpoints go in `app/api/main.py`** — no separate router files yet
- **Frontend is vanilla JS** — no build step, edit `app/static/` directly
- **Config is pydantic-settings** — add fields to `app/core/config.py`, read via `settings`
- **Secrets are never logged** — `logging_setup.py` redacts them
- **Compliance-first ingestion** — no scrapers, no saved-posts API, no auth bypass

---

## 9. Known limitations

- Business SLM is undertrained (6-step smoke train) — router keeps it OFF
- Transcription is CPU-bound (venv torch lacks CUDA) — consider `RV_WHISPER_MODEL_SIZE=base` on 8GB RAM
- yt-dlp fetch fails on login-walled reels → graceful metadata-only mode
- Share-target needs HTTPS for PWA install on mobile (use Tailscale)
- Docker build untested locally (Docker Desktop not installed; WSL2 build running)

---

## 10. When continuing this project

1. **Read this file first.**
2. **Check `docs/SESSION_HANDOFF.md`** for the latest session state.
3. **Run the test suite** before making changes.
4. **Verify the server is up** (`curl http://127.0.0.1:8756/healthz`) before testing endpoints.
5. **Check the background Docker build** if container work is needed:
   ```
   wsl.exe -d Ubuntu -u root -- bash -lc 'tail -3 /mnt/d/reelvault/data/docker_build3.log 2>/dev/null'
   ```


## read this new agent md inside Build. But not this project. This project = 10 features, 3 half-done, zero users. Cut fat. Ship small core. Grow later.

Truth first:

Core idea good. Reels → searchable facts with proof. Real gap. Nobody does offline.
Current repo = over-built. Auth v2, push, Docker CUDA, Android, custom SLM, Playwright IG login. All before one real user used it. Classic trap.
Custom 5.3M SLM = toy. Kill now. Road A. No debate.
Biggest AI risk not code. No eval set. Blind tuning.
Practical use (why bother)
Who actually pays/uses:

You. Saved reels graveyard → searchable. "That gym reel with 3 exercises" → found in 2s.
Job seekers / students. Reels with deadlines, scholarships, hiring posts. Radar = real value.
Recipe / fitness hoarders. Structured steps + ingredients extracted.
Creators. Archive own content. Search old scripts.
Small marketing teams. Competitor reel research. Offline = no data leak.
Portfolio. Shows STT + OCR + LLM extraction + grounding + RAG. Strong interview piece. Better than 10th chatbot.
Honest: niche. Not startup. Good personal tool + resume weapon. Build for that.

Cut list (do first, one session)
Delete or freeze. Not "later". Freeze = move to _archive/, remove from tests.

Kill	Why
Custom SLM + vendor/slm	Toy. Weeks wasted. Qwen enough
Android app	No SDK, no users. PWA share-target enough
Docker CUDA	Path bugs, 30-min builds, no one runs it
Web Push	Needs HTTPS+phone. Replace: dashboard badge
Playwright IG login	Ban risk, ToS risk. Drop
Auth v2 sessions/roles/theft detect	Single user. One password + token enough
Encrypted backups	copy db script. Done
Left = core loop + search + simple UI. That is prototype.

Stack for small agent (simple > clever)
Small agent fails on: GPU setup, CUDA, Playwright, multi-service. Remove all.

Piece	Use	Why
STT	faster-whisper small, int8, CPU	pip install. No torch CUDA pain. 30s reel = ~20s
OCR	rapidocr-onnxruntime	pip, CPU, no tesseract install
LLM	Ollama + qwen2.5:3b	one installer, one command. Kill llama_manager.py
Embed	skip v0. FTS5 only	vector later if FTS weak
DB	SQLite + FTS5	already there
API	FastAPI	already there
UI	vanilla HTML	already there
Input	file upload first, yt-dlp second	upload never breaks. yt-dlp optional
Queue	simple status column + one worker thread	drop heartbeat/DLQ complexity for v0
Rule for agent: every stage = one function, one file, one test, one CLI command to verify. No stage touches two files.

Plan (agent-executable)
Each phase = one session. Each has done check. Agent runs check. Fails → fix. Passes → next. No skipping.

Phase 0 — Reset (1 session)
New branch v0-core.
Move kill-list dirs to _archive/.
pip install faster-whisper rapidocr-onnxruntime fastapi uvicorn yt-dlp. Install Ollama, ollama pull qwen2.5:3b.
Config: all paths from env, default = relative to repo root. No D:/ anywhere. grep -r "D:/" app/ must return zero.
Done check: python -c "import faster_whisper, rapidocr_onnxruntime" OK. curl localhost:11434/api/tags shows qwen.

Phase 1 — Media in (1 session)
POST /ingest/upload → save mp4 to media/{id}/video.mp4, row in reels status=queued.
ffmpeg → audio.wav 16k mono. Frames every 1s → frames/*.jpg.
POST /ingest/url → yt-dlp → same path. Fail = status fetch_failed, no crash.
Done check: upload 1 test mp4 → wav + frames exist. Bad URL → 200 with fetch_failed.

Phase 2 — Text out (1 session)
transcribe(wav) -> [{t_start, t_end, text}] faster-whisper. Save transcript.json.
ocr(frames) -> [{t, text}]. Dedupe consecutive identical text. Save ocr.json.
Whisper hallucination guard: drop segments where no_speech_prob > 0.6 or text repeats 3×.
Done check: run on 3 reels. Eyeball transcript. Test: hallucination guard drops fake segment.

Phase 3 — Extract + evidence (2 sessions) ← the AI core
Prompt Qwen: input = transcript + OCR + caption. Output JSON strict:
json

{  "category": "job|recipe|fitness|finance|education|other",  "summary": "one line",  "facts": [{"claim": "...", "quote": "verbatim", "t_s": 12.4}],  "deadlines": [{"what": "...", "date_text": "...", "quote": "..."}],  "links_or_handles": ["..."]}
Parse with pydantic. Bad JSON → retry once with "fix JSON" → else empty.
Evidence check: quote must fuzzy-match (rapidfuzz partial_ratio >= 85) some transcript/OCR/caption span and len(quote) >= 15 chars. Fail → drop fact. Log dropped count.
Deadlines: dateparser on date_text. Model never outputs final date. Deterministic.
Confidence = match_score × (1 if t_s found in transcript window else 0.8). No fake weights.
Done check: 3 reels → facts with quotes. Inject fake fact in test → dropped. Tests pass.

Phase 4 — Golden eval set (1 session) ← do before any prompt tweak
Pick 20 reels. Hand-label: category, 3-5 key facts, deadlines. tests/golden/*.json.
Script eval.py: runs pipeline, reports category accuracy, fact recall (fuzzy), hallucination rate (facts not in gold), deadline exact match.
Baseline numbers into docs/EVAL.md. Every prompt change → rerun → compare. Regress → revert.
Done check: python eval.py prints table. Numbers saved.

Phase 5 — Search + UI (1 session)
FTS5 table over summary + facts + transcript + ocr.
GET /search?q= → ranked reels + matched snippet.
UI: upload box, paste URL, list, detail with facts + quote + click → video seeks to t_s. Radar tab: deadlines sorted by date, past = grey.
Done check: search "deadline" finds right reel. Click quote → video jumps.

Phase 6 — Ship prototype (1 session)
One password from .env. Simple bearer token.
start.ps1 / start.sh: start ollama, start uvicorn.
Retention: cron-ish thread deletes frames/ older than 30 days. Keep audio + json. Test it.
Backup: copy reelvault.db backups/YYYYMMDD.db daily. Test restore once: copy to fresh dir, open, count rows.
README honest: what works, what not. Tag v0.1.
Use it yourself 2 weeks. 30+ reels. Note pain.
Done check: fresh clone → README steps → working in 15 min. Restore test passed.

Total: ~8 sessions. Vs current path: infinite.

After v0.1 (only if you still use it)
Order by pain, not by cool:

Vector search (fastembed) if FTS misses paraphrases.
PWA share-target over Tailscale HTTPS. Phone → share → done.
Worker robustness (retries, DLQ) when jobs actually fail.
Whisper GPU if CPU too slow for you.
Multi-user auth if second human appears.
AI/ML advice (expert hat)
3B model = fragile extractor. Keep schema tiny. Few fields. More fields = more hallucination. Do one prompt per task (classify, then extract) not one mega-prompt. Cheaper, more accurate.
Grounding beats model size. Evidence check + deterministic dates gives 3B model 7B-level trust. You built this right. Keep it central.
Hinglish: whisper language=None auto-detect, or force hi and let it romanize? Test both on 5 reels. Pick by eval. Qwen handles Hinglish OK.
OCR frames: 1s sampling + dedupe. Also grab frame at each transcript segment start. Text overlays sync with speech.
Temperature 0, format: json in Ollama. Removes half the JSON errors.
Never trust eval by eyeball. 20 golden reels small but enough to catch regression. Grow to 50 over time.
Log every dropped fact. Dropped rate spikes = prompt broke or matcher too strict. Free monitoring.
Don't fine-tune anything until eval set ≥ 200 and Qwen baseline measured. Probably never needed.
Rules for small agent (paste in AGENTS.md)
One phase per session. Read phase. Do phase. Run done check. Stop.
Touch only files phase names.
Every function ≤ 40 lines. Every stage has test.
No new dependency without asking.
No GPU code. CPU works. Speed later.
Fail → log clear error, set status, continue. Never crash worker.
Update SESSION_HANDOFF.md last 5 lines: what done, what check passed, what next.
Dilemma answer, short
Do not abandon. Do not continue current path. Restart lean on same repo. 8 sessions → usable tool + strong portfolio. Then decide with real usage data, not with feelings. Half-built 10 features = nothing. One working loop = something.
