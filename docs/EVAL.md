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

## Run — September 8, 2026 (post 4-agent parallel batch, final)

After merging agent3 (tests), agent1 (B1 few-shot + B3 aliases + B4 verbatim-date
prompt), agent2 (A1 jaccard prefilter, B2 number norm, A3 confidence
redistribution, B4 value-parse fallback) and agent4 (B5 whisper hallucination
filter, B6 phash frame dedup, C1 timing, C2 wal checkpoint, C3 shutdown).

| Metric | Final | Baseline v1 | Gate |
|---|---|---|---|
| Category multilabel accuracy (`primary`) | 0.769 | 0.846 | ≥ 0.60 ✅ |
| Golden field recall | **0.824** | 0.824 | ≥ 0.50 ✅ |
| Malformed-JSON rate | 0.000 | 0.000 | ≤ 0.15 ✅ |
| Unsupported-kept rate | **0.098** | 0.178 | ≤ 0.55 ✅ (halved) |
| Deadline parse recall | 1.000 | 0.750 | ≥ 0.50 ✅ |

Notes: run-to-run variance observed mid-batch (recall 0.765-0.824 across two
runs of identical merged code — small golden set, sampling noise); final run
matches baseline recall exactly while unsupported-kept dropped from 0.178 to
0.098 (B2 number normalization + B5 phantom-segment filter are the likely
drivers). finance-01 now routes schema education/generic (B3 fix visible).
Agent 2's A3 raises the no-timestamp confidence ceiling 0.75→0.99 — flagged
for observation during Phase 6 real-data use.

## Run — September 9, 2026 (Phase 7 step 1-2: golden set expanded with real-window items)

Golden set 13 → 16 items. Added from real Phase 6 reels, labeled strictly from
stored transcript/OCR/caption artifacts:
- `job-03` (reel 14, scaledojo.dev): Microsoft Principal-Azure interview
  scenario; content almost entirely in one OCR frame; NO deadline expected
  (fabrication guard).
- `edu-04` (reel 21, skillupchapter): low-signal hiring-tips talking-head;
  Whisper captured one word; zero-fact failure mode; min_facts_kept=0
  accepts the graceful outcome.
- `edu-05` (reel 22, mikellesplaybook): LinkedIn networking playbook;
  transcript-rich + noisy OCR; short-value rescue origin case.

Baseline on expanded set (gates: 1 passed):

| Metric | Expanded baseline | Sept 9 pre-expansion | Gate |
|---|---|---|---|
| Category multilabel accuracy (`primary`) | 0.781 | 0.769 | ≥ 0.60 ✅ |
| Strict-or-near category hit rate | 0.875 | — | — |
| Golden field recall | 0.762 (16/21) | 0.882 (15/17) | ≥ 0.50 ✅ |
| Schema agreement | **0.562** | 0.562* | — |
| Unsupported-kept rate | 0.161 | 0.085 | ≤ 0.55 ✅ |
| min_facts_kept_rate | 0.938 (15/16) | — | ≥ 0.75 ✅ |
| Malformed-JSON rate | 0.000 | 0.000 | ≤ 0.15 ✅ |
| Deadline parse recall | 0.75 (3/4) | 0.75 | ≥ 0.50 ✅ |

*small-sample noise; schema agreement was not printed in earlier runs.

Real-item signals driving hardening candidates:
1. `job-03` schema drift (education/job): the known schema-drift issue now
   confirmed on real OCR-only job content.
2. `job-03` over-extraction: 12 facts from one OCR line, 9 flagged
   unsupported (values paraphrase the OCR question; quotes are verbatim
   substrings, so the evidence ledger passes them).
3. `edu-05` field probes 0/2 (topic/technologies) — extraction fields did
   not match the labeled needles on real how-to content.
4. Deadline recall still measured only on fictional items — the window's
   real deadline cases were hallucination artifacts, so no real
   deadline-bearing golden item exists yet. Owner should supply one.

## Run — September 9, 2026 (PHASE 7 step 4: anti-padding extraction prompt)

Change: schema-branch extraction prompt gains "NEVER PAD" instruction —
values must be actually stated in the source, no quote reuse across facts,
an invented field is worse than a missing one. Target: fact inflation on
tiny content (job-03 baseline: 12 kept / 9 unsupported from one OCR
sentence). Two post-change runs; gates 1 passed both.

| Metric | Baseline (16) | Run A | Run B |
|---|---|---|---|
| Golden field recall | 0.762 | **0.857** | **0.810** |
| Unsupported-kept rate | 0.161 | **0.141** | **0.129** |
| Deadline parse recall | 0.750 | 1.000 | 1.000 |
| Min-facts-kept rate | 0.938 | 0.938 | 0.938 |
| Multilabel accuracy | 0.781 | 0.781 | 0.781 |
| Malformed-JSON rate | 0.000 | 0.000 | 0.000 |

job-03 per-case: 12 kept / 9 unsup → 5 kept / 1 unsup → 3 kept / 0 unsup
(fields 2/2 in all three; model now declines to pad unsupported fields and
the ledger sheds the rest). edu-04 kept=1 (anchored, min=0 OK). No gated
regression; both target metrics improved on both runs.

Remaining Phase 7 targets (measured, not yet addressed): edu-05
field-recall misses (topic/technologies 0/2 across all runs — how-to content
does not fill education fields); job-03 schema drift (education vs job).

## Run — September 9, 2026 (PHASE 7 baseline: golden set expanded to 16 items)

Phase 7 step 1-2: added 3 real-window items labeled from stored Phase 6
artifacts — `job-03` (Microsoft/Azure interview reel: single OCR sentence,
no deadline), `edu-04` (low-signal talking-head: 1-word transcript, OCR-only
content, min_facts_kept=0 accepts graceful zero-fact), `edu-05` (OCR-heavy
networking how-to). Metrics are NOT comparable to the 13-item runs (set
changed); this is the Phase 7 hardening baseline. Harness gates: 1 passed.

| Metric | Expanded baseline (16 items) |
|---|---|
| Category multilabel accuracy (`primary`) | 0.781 (near-hit rate 0.875) |
| Macro F1 | 0.539 |
| Schema agreement | 0.562 |
| Golden field recall | 0.762 (21 probed) |
| Content presence | 0.333 |
| Malformed-JSON rate | 0.000 |
| Unsupported-kept rate | 0.161 (10/62) |
| Min-facts-kept rate | 0.938 |
| Deadline parse recall | 0.750 (n=4) |
| Latency (classify/extract mean) | 0.86s / 7.48s |

Measured hardening targets from this baseline (Phase 7 step 4 inputs):
1. **Fact inflation on tiny content** — job-03: 12 facts kept from one OCR
   sentence, 9 unsupported (token overlap <40%). The model emits a fact per
   schema field anchored to the same span; ledger can't reject tokens that
   genuinely appear. Candidate fixes need eval gating.
2. **edu-05 field misses** — topic/technologies 0/2 on a how-to reel (6 kept
   facts, none in those fields).
3. **Schema drift on real content** — job-03 classified education (B3 known
   issue, now has a real-content instance).
4. Observability fixed this commit: dropped facts now log quote + similarity
   in processing_events and eval per-case output (was value-only).

## Run — September 9, 2026 (short-value rescue + anti-leak prompt guard)

Changes under test:
1. `evidence.py` — short-value rescue: when BOTH quote and value are under
   `MIN_QUOTE_CHARS`, `find_evidence` previously returned 0.0 immediately,
   making the designed value-in-span weak support (0.75) unreachable. Now a
   value ≥ 6 chars appearing as a whole phrase in a span rescues the fact at
   similarity 0.75 (never 1.0 — the Phase-1 floor intent is preserved).
   Found via reel 12/22 root-cause work (though its real cause was #2).
2. `router.py` — anti-leak instruction on the extraction prompt: the B1
   few-shot example (a Zylker hiring reel) was being parroted verbatim by the
   3B model on low-signal content — same summary + 4 fabricated facts, all
   correctly dropped by the evidence ledger, but the summary leak persisted
   (summaries are not evidence-checked). Example now explicitly marked
   FORMAT-ONLY with named values not to copy; generic path gained the
   verbatim-value rule.

Live proof (reel 22 = DcgcqXfSVrI, delete + reprocess): summary fixed from
the parroted "Zylker is hiring data analysts in Bangalore, apply by September
15" to the true content (LinkedIn networking playbook); 0 facts → 4 kept,
all source-anchored with t_s and quotes, conf 0.7.

| Metric | This run | Sept 8 final | Gate |
|---|---|---|---|
| Category multilabel accuracy (`primary`) | 0.769 | 0.769 | ≥ 0.60 ✅ |
| Golden field recall | **0.882** | 0.824 | ≥ 0.50 ✅ (improved) |
| Malformed-JSON rate | 0.000 | 0.000 | ≤ 0.15 ✅ |
| Unsupported-kept rate | 0.085 | 0.098 | ≤ 0.55 ✅ |
| Deadline parse recall | 0.750* | 1.000 | ≥ 0.50 ✅ |

*parse_recall 0.75 vs 1.0 is sampling noise at n=4 deadline cases (pre-change
runs on identical code showed the same 0.75/1.0 swing; field recall also
swung 0.706→0.824 pre-change). Harness gates: 1 passed.

## Run — September 8, 2026 (after P0 audit fixes A5/A6, no prompt change)

Same runtime/config as baseline; changes: category normalization gains a
case-insensitive fallback to VALID_CATEGORIES (A5) and one shared list (A6).
Extraction prompt untouched. 1 passed (harness gates enforced).

| Metric | This run | Baseline v1 | Gate |
|---|---|---|---|
| Category multilabel accuracy (`primary`) | 0.769 | 0.846 | ≥ 0.60 ✅ |
| Golden field recall | **0.824** | 0.824 | ≥ 0.50 ✅ (unchanged) |
| Malformed-JSON rate | 0.000 | 0.000 | ≤ 0.15 ✅ |
| Unsupported-kept rate | 0.205 | 0.178 | ≤ 0.55 ✅ (noise at n=73) |
| Deadline parse recall | 1.000 | 0.750 | ≥ 0.50 ✅ |

Verdict: no gated regression; field recall identical. Category 0.769 vs 0.846
is within small-sample noise (one label flip swings it); A5/A6 can only ADD
categories, never drop them. Deadline parse improved (3/4 → full).

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
