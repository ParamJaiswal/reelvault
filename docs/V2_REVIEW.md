# V2 Review Report — 2026-09-21

Branch `v2` vs frozen `main` (tag `v0.1.0`). Scope: multi-source ingestion,
LLM backend swap seam, widened search. Two review passes + live rehearsal.

## Verdict

Ship-ready for personal use on v0.1 paths. v2 text sources functionally
complete, gated on real-LLM run + owner approval for new deps.

## Evidence

| Check | Result |
|---|---|
| Full suite | 290 passed, 2 skipped (AI eval gated separately) |
| Live DB upgrade rehearsal (copy) | v5→v9: 21 reels, 88 facts intact, integrity ok, FTS 21 rows, search 8 hits |
| Real live DB | migrated v5→v9 with pre-snapshot `db_pre_v9_20260921_004030.sqlite` |
| Old running server vs new schema | old search query returns rows against contentless FTS — no breakage in restart window |
| Backup restore | snapshot → clean temp dir, integrity ok, FTS works |
| Preservation | bundle `reelvault-v0.1.0.bundle` verifies complete; GitHub remote has main + v0-core + tags |
| v0.1 compat by construction | content_kind default `video`, llm_backend default `llamacpp`, IG routing regression-tested incl. instagr.am |

## Review pass 1 (cavecrew) — all fixed, verified by tests

1. Contentless FTS SELECT returns NULLs → delete silently failed → index
   bloat. Fixed: `reels_fts_shadow` table (v8), backfilled.
2. SSRF: article adapter fetched any user URL. Fixed: private/loopback/
   reserved-IP rejection.
3. Bearer/API-key values could land in `ai_runs.error`. Fixed: redaction.
4. Document insert outside reel transaction. Fixed: same transaction.
5. Prompt/evidence truncation mismatch (10K). Fixed: aligned.
6. v6 backfill missed shadow. Fixed: v8 backfill pass.

## Review pass 2 (cavecrew) — all fixed

1. 🔴 SSRF still bypassable: separate DNS check + `follow_redirects=True`
   (302→127.0.0.1) and rebinding window. Fixed: manual redirect loop, each
   hop re-validated, max 5.
2. 🟡 Migration 9 `executescript` autocommits per statement: mid-script crash
   could brick startup or lose facts. Fixed: `BEGIN IMMEDIATE`/`COMMIT` +
   `DROP TABLE IF EXISTS facts_new` rerun-safety.
3. 🟡 `delete_reel` left FTS + shadow rows (no delete trigger since v6);
   recycled rowids surfaced deleted content. Fixed: explicit delete in
   `delete_reel`.
4. 🟡 User corrections (PATCH reel, add/edit/delete fact) never re-indexed —
   v0.1 "correct, then find" loop broken. Fixed: `_fts_refresh` on all four
   endpoints; PATCH summary now grounds against document body too.
5. 🟡 Exact-host blocklist missed `mobile.twitter.com`, `youtu.be` → shared
   tweets/shorts ingested as garbage articles. Fixed: suffix match.
6. 🟡 JSON-shaped errors (`"api_key": "..."`) evaded redaction. Fixed:
   regex covers quoted form.

## Known gaps (honest, not blockers)

- Text-source extraction quality unmeasured — live run shows aggressive
  dropping (1/8 facts kept on GPT-3 abstract). Golden set needed (§9).
- PDF text extraction not added (pypdf/pdfplumber need owner approval).
  `evidence_page` plumbing ready. Page locators stay NULL until then.
- X video/photo downloads: media URLs stored in meta but not fetched;
  X tweets ingest text only. Tweet video = future work.
- trafilatura pending approval → basic HTML extraction until installed.
- Jev gate waived this session (key never reaches process); caveman reviewer
  substituted, two passes.
- Dead-reel FTS hygiene applies to API delete only; queue/DLQ paths don't
  delete reels.

## Change surface

22 files, +~1900/−40 vs main. New: 4 adapters, documents table, STAGE_PLANS,
OpenAICompatProvider, shadow table, 63 new tests. Schema v6→v9, forward-only.

## Live test (2026-09-21, real services + real Qwen)

Restarted server on v2 code; ingested real arXiv papers (1706.03762,
2005.14165) and a real X post (x.com/jack/status/20) through the API.

Working live: routing, documents storage, kind-aware plans (ocr/classify/
embed/finalize only), clean titles after fix, summaries, categories,
document-evidenced facts, FTS search via POST /api/search, delete purges
FTS+shadow.

Bugs found by live test, fixed + regression-tested (commit 24d3b8d):
1. arXiv http→https 301 broke lookup (follow_redirects added).
2. Feed-level `<title>` junk polluted title/body (parse inside `<entry>`,
   unescape entities).
3. Adapter title never persisted (create_reel read req.meta, not resolved).
4. Media-bearing tweets enqueued ingest → failure path clobbers documents
   (ingest stage now video-only).

Open quality finding (not a crash): on a clean GPT-3 abstract the ledger
kept 1/8 facts — the 3B model paraphrases quotes below the 0.45 similarity
floor. Dropping wrong facts is correct behavior; recall is the known
evidence-matcher tension (§2 of the challenges doc). Text sources need
golden-set rows + calibration before extraction quality can be claimed.

## Next (single ordered list)

1. One text-source golden-set row per kind; calibrate matcher on text spans
   (paragraph granularity may need sentence-level chunks).
2. Owner: approve trafilatura + pypdf → swap in real extraction.
3. Tweet media / PDF OCR pass with page locators.
4. Try cloud LLM seam (Groq/Gemini) for text extraction quality A/B.
5. Android: unfreeze only when intake pain appears; PDF/image share intents.
