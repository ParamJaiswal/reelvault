# AI Extraction Evaluation

**Last run:** September 6, 2026 · **Branch:** `phase4-agentic` · **Status: baseline v1**

Golden-set benchmark for classification, structured extraction, Evidence
Ledger validation, and deadline parsing against the real Qwen/llama.cpp path
(`TaskRouter.classify` + `TaskRouter.extract` + `find_evidence`), mirroring
`stage_classify_extract`'s unified text and span building exactly.

## How to run

```powershell
# llama-server must be running (default http://127.0.0.1:8091/v1)
D:\reelvault\.venv\Scripts\python.exe -m pytest tests\test_ai_eval.py -q -s
```

Results are written to `<data_dir>/eval_results.json`. Excluded from the
default suite (`AGENTS.md` test command ignores it) because it needs the
live model server and takes ~2 minutes.

## Golden set

`tests/golden/*.json` — 13 hand-labeled reel-like items:

| Domain | Items | Notes |
|---|---|---|
| Job | job-01, job-02 | deadlines in speech + caption |
| Scholarship | scholarship-01 | deadline in speech + OCR |
| Education | edu-01, edu-02, edu-03 | concept / tutorial / deadline-dense |
| Tool | tool-01, tool-02 | pricing hallucination bait |
| Recipe (OCR-heavy) | recipe-01 | quantities on screen, taxonomy gap |
| Fitness (music-heavy) | fitness-01 | fragmented speech, taxonomy gap |
| Hinglish | hinglish-01 | mixed-language hard case |
| Finance | finance-01 | numbers-heavy |
| Event | event-01 | location + registration |

### Label policy

Each item has two category label sets:

- **`primary`** — the set a *precise* classifier must include. Used for the
  gated accuracy/macro-F1 metrics. The model reliably returns 1–2 focused
  categories; greedy 4-category wish-lists punish correct precision.
- **`categories`** — full acceptable wish-list. Reported as
  `macro_f1_acceptable_labels`, not gated.

Content is fictional (no real companies); `min_facts_kept` is the floor of
evidence-kept facts expected per item.

## Baseline v1 — September 6, 2026

Qwen2.5-3B-Instruct Q4_K_M via llama.cpp, temperature 0.15/0.1, one run.
Mean per-reel latency: 10.4s (classify 0.7s + extract 9.7s).

| Metric | Value | Gate | Result |
|---|---|---|---|
| Category multilabel accuracy (`primary`) | **0.846** | ≥ 0.60 | ✅ |
| Strict-or-near category hit rate | 0.923 | — | — |
| Category macro-F1 (`primary`) | **0.680** | ≥ 0.45 | ✅ |
| Schema agreement | 0.615 | — | — |
| Golden field recall | **0.824** | ≥ 0.50 | ✅ |
| Content presence (taxonomy-gap items) | 0.667 | — | — |
| Malformed-JSON rate | **0.000** | ≤ 0.15 | ✅ |
| Facts kept | 73 | — | — |
| Hallucinations dropped | 7 | — | — |
| Unsupported-kept rate | **0.178** | ≤ 0.55 | ✅ |
| Min-facts-kept rate | **1.000** (13/13) | ≥ 0.75 | ✅ |
| Deadline parse recall | **0.750** (3/4) | ≥ 0.50 | ✅ |

### Per-case highlights

- job-01: 5/5 named fields hit, 9 facts kept, 3 unsupported quotes dropped.
- job-02: 3/4 fields, deadline (caption-only "October 20th") parsed.
- edu-03: deadline "5th of October" parsed correctly.
- scholarship-01: 11 facts kept; deadline fact was paraphrased, not verbatim
  ("Apply before November thirtieth" → value "November 30") so the deterministic
  parser found no matching date — see Known issues #4.
- tool-01/02: 2/2 and 1/2 fields; tool-02's "twenty dollars a month" price not
  extracted verbatim.

## What changed for baseline v1

| Change | Why (measured) | File |
|---|---|---|
| Extraction prompt now requires one fact for **every** supported field | Pre-fix run: field recall 0.176, job-01 extracted 1 fact total for a 5-field reel | `app/ai/router.py` (prompt only, no schema change) |
| Golden label policy: `primary` vs wish-list `categories` | Pre-fix category accuracy 0.5 was label calibration, not model failure (near-hit rate was 0.846) | `tests/golden/*.json`, harness |
| Harness rewritten for golden-dir corpus + pipeline-mirrored spans | Old 12 hardcoded synthetic strings didn't match pipeline unified format | `tests/test_ai_eval.py` |

## Known issues (candidates for next eval cycle)

1. **Schema drift on non-job content**: finance/fitness/recipe/hinglish are
   classified `education` (schema `generic` expected). Categories are usually
   still reasonable; only the schema pick drifts. Impact: content lands in
   education-shaped extraction, which still produced evidence-kept facts.
2. **Content presence 0.667**: recipe/fitness/hinglish wildcard checks show
   extraction loses some on-screen/fragmented domain terms (e.g. paneer
   quantity lives only in OCR).
3. **Unsupported-kept 0.178**: 13 of 73 kept facts share <40% token overlap
   with source. Mostly formatting/normalization ("twenty five thousand" →
   "25,000") rather than invention, but the Evidence Ledger accepts them.
   Worth a closer look before relaxing any threshold.
4. **Deadline recall is substring-dependent**: paraphrased date values that
   no longer contain the raw date text fail deterministic parsing. The
   AGENTS.md direction (model returns `date_text` verbatim; deterministic
   code normalizes) is the correct long-term shape.

## Regression rule

Any prompt, model, OCR, STT, evidence-matcher, or schema change must re-run
this eval and compare against the table above. A regression requires an
explanation or a reversion.
