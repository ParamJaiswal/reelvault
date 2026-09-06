# Testing Report — D:\reelvault (Aug 26, 2026)

## Summary
| Suite | Result |
|---|---|
| Unit + integration (pytest) | **28 / 28 pass** |
| AI golden benchmark (12 cases, live Qwen+rules) | **PASS** all gates (113s) |
| Security negatives | **all blocked correctly** |
| API surface smoke | 100% 200s |
| Live E2E (upload, share-target, watch-folder, dedup, delete) | **PASS** |

## 1. Unit + Integration — 28/28
Queue: claim/complete, retry→backoff→dead-letter→admin-resurrect,
stale-heartbeat reclaim. Adapters: URL parsing incl. share-garbage, blob
splitting, bad-input rejection. Evidence Ledger: exact-match, hallucination
drop (<0.45 sim), confidence bounds. Pipeline: metadata-only terminal state,
FakeLLM extraction w/ evidence verification + entity linking, shortcode
duplicate merge. Deadlines: month parsing, day-first, year roll-forward,
reminder-never-past, unparseable-safe, fact-preference. Router guardrails:
category normalization, schema aliases, heuristic fallback.

## 2. AI Benchmark (tests/test_ai_eval.py)
Classification: multilabel acc 0.68 · near-hit 0.92 · macro-F1 0.57
Extraction: golden field recall 0.69–0.81
Evidence: unsupported-kept rate 0.14 (hallucination guard active)
Latency: ~1.5s classify, ~9s extract per reel (Qwen on RTX 3050)

## 3. Security negatives (live)
no token → 401 · wrong token → 401 · path traversal → 404 ·
non-Instagram URL → 422 · missing reel → 404

## 4. Bugs found & fixed during this test pass
1. WatchFolderAdapter never matched `kind=watch_folder` → 500 on scan
   (also wasn't in ADAPTERS list; also `req and a.resolve(req)` oddity).
   First fix made it abstract-instantiable-wrong → caught by server crash
   on restart → final: extends FileIngestionAdapter + own matches().
2. AutoTranscriber only fell back on DLL keywords — now falls back on ANY
   primary failure (missing module included). Proven live.
3. dateutil unknown-timezone warnings from OCR text — suppressed;
   digit-less strings short-circuit before parsing.

## 5. Live flows verified
- upload → full pipeline → facts/OCR/deadline ✓
- watch folder drop → scan → ingest ✓ (then purged)
- privacy delete: media+thumb+frames gone, read-after-delete 404 ✓
- human fact correction persisted ✓
- duplicate merge by embeddings (reel #6 → #1) ✓
- assistant grounded answer w/ citations, conf 0.95 ✓
- hybrid search ranking correct ✓

## Commands
```powershell
cd D:\reelvault
.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_ai_eval.py   # fast suite
.venv\Scripts\python.exe -m pytest tests\test_ai_eval.py -q -s                # AI bench (llama-server must be up)
```
