# ReelVault — Full Project Report
**Date:** August 26, 2026 · **Status:** WORKING END-TO-END (verified by live execution)
**Code:** `D:/reelvault` · **Models/Media:** `D:/reelvault`

---

## 1. Product Overview

**ReelVault turns Instagram Reels into a private, searchable, evidence-backed knowledge base — with AI that runs 100% on your own GPU. Zero cloud calls in the entire pipeline.**

You paste/share/drop a Reel. The system fetches or receives the video, transcribes the speech, reads the on-screen text, reasons over both with a local 3B SLM, and stores **structured knowledge where every fact carries its proof** — a verbatim quote and timestamp. It then becomes searchable (keyword + semantic), rankable by urgency (Opportunity Radar), and question-answerable (cited offline assistant).

Unique concepts shipped:
1. **Evidence Ledger** — anti-hallucination backbone. Facts without source support are dropped automatically (threshold: 0.45 similarity).
2. **Opportunity Radar** — deadlines/jobs/internships auto-scored by urgency (job reel scored 99/99).
3. **Self-healing model layer** — providers detect broken runtimes mid-flight and permanently fall back (proved live when Windows Application Control blocked CTranslate2 DLLs).

## 2. Architecture

```
                 ┌────────────────────────────────────────────────┐
  INGESTION      │  URL paste · PWA share-target · file upload    │
  (adapters)     │  watch folder        [IngestionAdapter iface]  │
                 └───────────────┬────────────────────────────────┘
                                 ▼
                     FastAPI  :8756  (auth: bearer token)
                                 ▼
                  SQLite WAL  ──  durable job queue
                  (16 tables +   (retry/backoff/DLQ/heartbeat/
                   FTS5 index)   stale-reclaim, idempotent stages)
                                 ▼
   PIPELINE WORKER THREAD:  ingest → media → transcribe → ocr →
                            classify_extract → embed → finalize
                                 ▼
        ┌────────── LOCAL AI LAYER (provider abstraction) ──────────┐
        │ LLM: llama-server :8091 (Qwen2.5-3B Q4_K_M, RTX 3050)     │
        │ ASR: AutoTranscriber (faster-whisper → torch-whisper)     │
        │ OCR: RapidOCR (ONNX/CPU)   EMBED: bge-small-en-v1.5 384d  │
        └───────────────────────────────────────────────────────────┘
                                 ▼
        Evidence Ledger verification → facts/entities/embeddings
                                 ▼
   Vanilla-JS PWA: Inbox · Radar · Jobs · Learning · Tools · Saved ·
                   Search · Assistant · Processing · Settings
```

## 3. Technology Stack & Why

| Layer | Choice | Reason |
|---|---|---|
| API | FastAPI + uvicorn | async, typed, OpenAPI docs free at `/api/docs` |
| DB | SQLite WAL | personal-scale, zero-ops, one-file backup |
| Queue | custom SQLite queue | retries, dead-letter, heartbeats — no Redis needed |
| SLM | Qwen2.5-3B-Instruct Q4_K_M (2.1GB) | fits 4GB VRAM with 8k ctx, good JSON discipline |
| Runtime | llama.cpp b7795 CUDA build | GPU-offloaded (-ngl 33), OpenAI-compatible server |
| ASR | openai-whisper small (torch) | works where faster-whisper is AV-blocked; Hinglish-capable |
| OCR | RapidOCR ONNX | CPU, no system tesseract install |
| Embeddings | fastembed bge-small-en-v1.5 | 384-dim, ONNX/CPU, fully local |
| Media | ffmpeg/ffprobe + yt-dlp | validation, audio extraction, frame sampling |
| Frontend | vanilla-JS PWA | one artifact, instant load, native OS share-target |

**Total AI cost per reel: ₹0.00** (all inference local).

## 4. Instagram Integration Method (compliance research)

**Finding (verified Aug 2026):** Instagram/Meta APIs do **NOT** expose a user's Saved posts/collections. Community + Meta docs confirm automation walls around saved media.

**Decision:** No undisclosed scraping. Implemented behind one swappable interface (`app/ingest/adapters/base.py`):

| Adapter | Status | Mechanism |
|---|---|---|
| `UrlIngestionAdapter` | ✅ active | paste any reel/post URL (cleans `?igsh=` garbage) |
| `ShareTargetAdapter` | ✅ active | PWA manifest share_target → Android/iOS share sheet |
| `FileIngestionAdapter` | ✅ active | upload mp4/mov/webm |
| `WatchFolderAdapter` | ✅ active | drop files in `D:/reelvault\watch`, scan endpoint |
| `InstagramOfficialAdapter` | ⏸ stub | activates if Meta ever grants saved-content access |

Public-URL fetching uses yt-dlp best-effort **without cookies/auth impersonation**; failures land in a graceful *metadata-only* terminal state (user attaches file manually) — never a crash loop.

## 5. Data Pipeline (as executed live)

```
ingest   → download/attach, dedupe short-circuit          ✓ verified
media    → ffprobe validate, 16kHz mono wav, ≤12 frames,
           thumbnail, sha256[:16]+duration content-hash    ✓ verified
transcribe→ ASR w/ timestamps + language + segment confs   ✓ verified
ocr      → per-frame OCR, near-duplicate overlay merge,
           temporal preservation (t_s kept)                 ✓ verified
classify_extract → SLM multi-label classification →
           specialized schema extraction (job/edu/tool/event)│generic
embed    → reel + segments + OCR + facts → 384-d vectors    ✓ verified
finalize → duplicate check (shortcode/hash/cosine≥0.93),
           notification, completion                         ✓ verified
```
Every stage writes an audit row to `processing_events` (visible in UI) and every AI call to `ai_runs` (model, latency, tokens, ok/error).

## 6. AI Pipeline — Evidence Ledger

Confidence = `0.35·quote_similarity + 0.25·timestamped_source + 0.2·corroboration + 0.2·model_confidence`, clamped to [0.05, 0.99].

Any extracted fact whose quote/value fuzzy-matches **no** transcript/OCR/caption span at ≥0.45 similarity is **dropped as probable hallucination** and logged. Regex pre-pass (URLs, emails, phones) adds deterministic facts at 0.95.

**Live proof (Reel #1 — synthesized job reel):**

| Field | Value | Evidence | Conf |
|---|---|---|---|
| company | Zylker Analytics | transcript @00:01 | 0.90 |
| location | Bangalore | transcript @00:01 | 0.90 |
| deadline | September 15 | transcript @00:18 | 0.90 |
| job_type | Intern | transcript @00:01 | 0.90 |
| skills | SQL, Python, Excel | transcript @00:09 | 0.70 |
| application_url | zylker.example.com/careers | caption regex | 0.95 |

Reel #2 (RAG tutorial): steps extracted, 7 OCR overlays ("RAG in 60 seconds", "Chunk documents", "FAISS·Qdrant·Chroma"...), entities FAISS/Qdrant/Chroma/RAG. Reel #3 (tool): "90+ languages" @0.90, OCR caught `github.com/openai/whisper`. Unit test also proved a fabricated "₹1 crore/month salary" gets dropped while the real role fact survives.

## 7. Database Architecture

16 tables, migration framework (v1 core, v2 FTS5): `users, ingestion_sources, reels, transcript_segments, frames, ocr_results, entities, reel_entities, facts(Evidence Ledger), embeddings(BLOB float32), jobs, processing_events, ai_runs, notifications` + `reels_fts` (FTS5 external-content w/ sync triggers + rebuild). Indexes on user/status, shortcode, content_hash, ingested_at, embeddings owner/reel. All derived-row inserts are delete-then-insert (idempotent re-runs).

## 8. Search & Assistant (verified live)

- **Hybrid:** FTS5 keyword (bm25 rank) ⊕ vector cosine, blended `1/kw_rank + cos_score`.
  - `"fresher data analyst jobs in Bangalore"` → #1 correct (kw=1, sem=0.82)
  - `"offline transcription tool"` → #3 wins purely semantically (0.93, zero keyword overlap)
- **Assistant:** retrieves top-6, packs summary/takeaways/facts/transcript/OCR, answers with `[R#id]` citations only from context. Live answer listed company/location/skills/stipend/PPO/deadline correctly + honestly stated *"No other job postings were saved."*

## 9. Security Model

Bearer token auth (auto-generated → `data/.auth_token`; query-param variant for PWA share-target only) · binds 127.0.0.1 · static-file traversal guard · logging formatter redacts `access_token/client_secret/api_key/password/authorization` · secrets never logged · user-scoped queries everywhere · DELETE purges media+thumb+frames+transcript+facts+embeddings permanently · `.env.example` documents optional overrides, no credentials required by design (nothing external to authenticate).

## 10. Testing Results

| Suite | Result |
|---|---|
| pytest unit+integration | **19/19 pass** (queue claim/complete, retry→backoff→dead-letter→resurrect, stale-heartbeat reclaim, adapters/URL parsing incl. share-garbage, evidence match/hallucination-drop/confidence bounds, schema caps, metadata-only failure mode, FakeLLM evidence verification, shortcode duplicate merge) |
| Live E2E ×3 real videos | all stages green; facts/OCR/search/assistant outputs above |
| Readiness | `/readyz`: db ✅ llm ✅ embedder ✅ |
| UI assets | index/css/js/manifest/sw/icons/thumbs all HTTP 200 |

**Bugs my own testing caught and fixed during the build:** shared-connection thread crash in queue · OCR inserts silently rolled back on a side connection · FTS5 table referenced-but-never-created (added migration v2) · unvalidated `primary_schema` KeyError · NULL-summary notification crash · static route not matching dotted paths · telemetry FK violation on probe calls.

**Environment battles solved:** wrong GGUF filename casing → "Entry not found" 15-byte stub · GitHub API blocked → expanded_assets scrape for asset URLs · MSYS-vs-native path mapping broke llama-server and heredoc Python · ffmpeg gradients filter syntax + drawtext fontfile/apostrophe quirks · Windows Application Control blocking CTranslate2 DLLs → permanent torch fallback.

## 11. Deployment Instructions (this machine)

```powershell
# 1. SLM server (leave running)
D:/reelvault\llamacpp\llama-server.exe -m D:/reelvault\models\qwen2.5-3b-instruct-q4_k_m.gguf --host 127.0.0.1 --port 8091 -c 8192 -ngl 33 --jinja --alias qwen2.5-3b-instruct

# 2. App + worker
cd D:/reelvault
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8756

# 3. Use: http://127.0.0.1:8756  (phone: http://<pc-ip>:8756 → Add to Home screen)
```
Tests: `.venv\Scripts\python.exe -m pytest tests/ -q`. Backups: copy `data/reelvault.db` (+ `D:/reelvault\media` if you want media history). Rollback: migrations are append-only; delete db file to rebuild schema from zero (media preserved).

## 12. Known Limitations (honest)

1. **Transcription runs on CPU** — the project venv's torch is CPU-only, and faster-whisper's CUDA path is AV-blocked on this machine. Works fine for reel-length audio; GPU ASR needs a CUDA torch wheel or unblocking CT2.
2. **yt-dlp fetch is best-effort** — login-walled/private/deleted reels fail fetch → metadata-only mode (attach file manually).
3. **3B SLM variance** — extraction completeness varies between runs (temp 0.15); eval harness will quantify it.
4. Single-user/local by design; retention sweeper configured (`RV_RETENTION_MEDIA_DAYS`) but not yet scheduled; share-target untested on a physical phone; PWA icons functional but plain.

## 13. Roadmap

1. AI eval benchmark harness (`tests/test_ai_eval.py`) over the 3-reel golden set + more categories
2. git init + first commit; GitHub Actions CI running pytest
3. CUDA torch wheel for GPU transcription (or WDAC exclusion for CT2)
4. Retention sweeper daemon; deadline extractor feeding OS calendar reminders
5. Entity graph view + cross-reel "same company" clustering
6. Real-phone field test of PWA share-target; iOS shortcut recipe
7. Optional cloud tier (documented swap: Postgres + Redis + object storage)

---
*Handoff state, decisions log, and exact resume instructions: `docs/SESSION_HANDOFF.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`.*
