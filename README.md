# ReelVault

**Save an Instagram Reel → get its knowledge back as searchable, evidence-backed facts. 100% private, runs on your own PC.**

## What it does (plain words)

You give it a link to a **public** Instagram Reel (paste it, share it from your
phone, or drop the video file in). ReelVault then:

1. Downloads the video
2. **Listens** — transcribes every spoken word with timestamps
3. **Looks** — reads all text shown on screen (OCR)
4. **Understands** — classifies it (job? tutorial? tool?) and extracts the
   useful stuff: company, role, location, salary/stipend, skills required,
   deadlines, apply links…
5. **Proves it** — every extracted fact carries the exact quote + timestamp
   it came from. Made-up facts are automatically dropped.
6. Makes everything **searchable** and gives you an AI assistant that answers
   questions using only your saved reels — with citations.

> ⚠️ Private / login-walled reels can't be fetched (Instagram rules) — use
> the “＋ File” button or watch-folder for those instead. Your Saved folder
> isn't readable by any app; sharing the link is the compliant way.

## 🚀 Start (double-click)

```text
D:\reelvault\Start ReelVault.bat
```

That's it — it sets up everything on first run, starts the AI engine,
and opens your browser at `http://127.0.0.1:8756`.

First time? Click **“▶ Try the demo reel”** on the home screen to watch a
real job-post reel become structured knowledge in ~60 seconds.

## 📱 Use from your phone

1. Keep the PC app running, phone on the same Wi-Fi
2. Phone browser → `http://<PC-IP>:8756`
3. Chrome menu → **Add to Home screen**
4. In Instagram: tap **Share → ReelVault** on any public reel ✨
   You'll see “Saved ✓” instantly; processing finishes in the background.

(If the phone can't connect, allow port 8756 through Windows Firewall once.)

```powershell
cd D:\reelvault
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 0.0.0.0 --port 8756
# open http://127.0.0.1:8756   (phone on same Wi-Fi: http://<PC-IP>:8756)
```

First run auto-creates the DB, starts the pipeline worker thread, and prints
your API token (also stored in `data\.auth_token`).

The UI bootstraps itself — open the app, paste an Instagram Reel URL,
press **Add Reel**, watch it flow through:

```text
ingest → media → transcribe → ocr → classify_extract → embed → finalize
```

### Start the SLM server (required for classification/extraction/assistant)

```powershell
D:\reelvault\llamacpp\llama-server.exe ^
  -m D:\reelvault\models\qwen2.5-3b-instruct-q4_k_m.gguf ^
  --host 127.0.0.1 --port 8091 -c 8192 --gpu-layers 33 ^
  --jinja --alias qwen2.5-3b-instruct
```

Everything else (Whisper small on CUDA, RapidOCR, bge-small embeddings)
loads lazily inside the app process.

## Ingestion methods (all compliant)

| Method | How | Notes |
|---|---|---|
| URL paste | Top bar of the web app | Public reels fetched via yt-dlp |
| Share target | Install PWA on Android/iOS → share any Reel → ReelVault | The mobile-first path |
| File upload | ＋ File button | Any mp4/mov/webm |
| Watch folder | Drop files in `D:\reel-knowledge\watch`, press Scan | For screen-recorded saves |

**Why no "read my saved Reels" feature?** Instagram's Graph API does not expose
a user's saved collections (verified Aug 2026). Rather than ship a fragile
scraper, ingestion is built around share/paste flows behind one swappable
`IngestionAdapter` interface — see `docs/INGESTION.md`.

## Feature map

- **Evidence Ledger** — every fact traceable to quote + timestamp; low-evidence
  claims are dropped as probable hallucinations automatically
- **Opportunity Radar** — deadlines/jobs/internships ranked by urgency
- **Hybrid search** — SQLite FTS5 keyword + bge-small semantic, rank-blended
- **Local RAG assistant** — answers only from your vault, cites [R#id]
- **Duplicate detection** — shortcode/hash/embedding similarity merge
- **Human feedback loop** — edit or mark facts wrong; AI value preserved for eval
- **Admin view** — queue depth, dead letters, per-call model telemetry, retries
- **Privacy controls** — delete reel = purge media+transcript+facts permanently

## Docs

Architecture, database, AI pipeline, security, testing, deployment:
see [`docs/`](docs/) — start with `ARCHITECTURE.md` and `docs/FINAL_REPORT.md`.

## Stack

FastAPI · SQLite (WAL) · llama.cpp (Qwen2.5-3B-Instruct Q4_K_M, RTX 3050) ·
faster-whisper · RapidOCR (ONNX) · fastembed (bge-small-en-v1.5) ·
vanilla-JS PWA · ffmpeg/yt-dlp.
