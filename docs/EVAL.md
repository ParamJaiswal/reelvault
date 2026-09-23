# AI Extraction Evaluation

**Last run:** September 23, 2026 (20-row, post-matcher-fix; see the final section) ·
**Matcher replay:** September 23, 2026 · **Branch:** `v2-hybrid-routing` ·
**Status: 20-row baseline; document-quote matching ceiling fixed; Phase 7
incomplete**

Golden-set benchmark for classification, structured extraction, Evidence
Ledger validation, and deadline parsing against the real Qwen/llama.cpp path
(`TaskRouter.classify` + `TaskRouter.extract` + `find_evidence`), mirroring
`stage_classify_extract`'s unified text and span building exactly.

## How to run

```powershell
# llama-server must be running (default http://127.0.0.1:8091/v1)
D:\reelvault\.venv\Scripts\python.exe -m pytest tests\test_ai_eval.py -q -s
```

Results are written to `<data_dir>/eval_results.json`; the full path is printed
and write failures fail the test. `tests/conftest.py` defaults `data_dir` to a
temporary directory, not the application's data directory. Set `RV_EVAL_TRACE=1`
to include exact extraction messages, request options, raw completions, and
kept/dropped facts in that local file. Traces contain source content: keep them
private and do not commit them. Excluded from the
default suite (`AGENTS.md` test command ignores it) because it needs the
live model server and takes ~2 minutes.

## Golden set

`tests/golden/*.json` — 20 items: 13 fictional reel-like fixtures, three
real-source-derived video fixtures (`job-03`, `edu-04`, `edu-05`), and four
non-video kind rows (`paper-01`, `paper-02`, `article-01`, `xpost-01`) added for
v2 ingestion. The real-source-derived ones contain selected/edited excerpts and
require provenance and label review; they are not complete raw recordings.

| Domain | Items | Notes |
|---|---|---|
| Job | job-01, job-02 | deadlines in speech + caption |
| Scholarship | scholarship-01 | deadline in speech + OCR |
| Education | edu-01, edu-02, edu-03 | concept / tutorial / deadline-dense |
| Education (real) | edu-04, edu-05 | low-signal talking head; OCR-heavy how-to |
| Job (real) | job-03 | single OCR sentence, no deadline |
| Tool | tool-01, tool-02 | pricing hallucination bait |
| Recipe (OCR-heavy) | recipe-01 | quantities on screen, taxonomy gap |
| Fitness (music-heavy) | fitness-01 | fragmented speech, taxonomy gap |
| Hinglish | hinglish-01 | mixed-language hard case |
| Finance | finance-01 | numbers-heavy |
| Event | event-01 | location + registration |
| Paper | paper-01 | 1,828-char abstract: too small to exercise span locality |
| Paper (multi-page) | paper-02 | real 2-page excerpt, page markers, 2,857-char span |
| Article | article-01 | trafilatura body text |
| X post | xpost-01 | embed-endpoint text |

### Label policy

Each item has two category label sets:

- **`primary`** — the set a *precise* classifier must include. Used for the
  gated accuracy/macro-F1 metrics. The model reliably returns 1–2 focused
  categories; greedy 4-category wish-lists punish correct precision.
- **`categories`** — full acceptable wish-list. Reported as
  `macro_f1_acceptable_labels`, not gated.

`min_facts_kept` is the floor of evidence-kept facts expected per item.

## September 20, 2026 — live golden run after the Phase 8A changes

llama-server restarted on :8091 and `RV_EVAL_TRACE=1 pytest tests/test_ai_eval.py -q -s`
executed: **1 passed in 118.03 s**, 16 cases, all served by `qwen`, 0 malformed.
Private trace: `D:/Temp/user/rv_test_zynbd73x/eval_results.json` (not committed).

| Metric v2 | This run | Recorded band for the same checkpoint |
|---|---:|---:|
| Kept-fact named-field recall | 0.810 (17/21) | 0.714 / 0.714 / 0.762 / 0.810 |
| Deadline parse recall | 1.000 (4/4) | 0.75 / 0.75 / 1.00 |
| Unsupported-kept heuristic | 0.036 (2/55) | 0.019 / 0.038 / 0.055 |
| Minimum-kept rate | 0.875 | 0.875 / 0.938 |
| Category multilabel / macro-F1 | 0.750 / 0.509 | 0.750 / 0.509 |
| Schema agreement | 0.688 | 0.688 |
| Mean classify + extract latency | 7.32 s | 9.34 s |

No regression: every gate passes and the two varying metrics (recall, deadline)
land at the **top** of the previously measured spread, not above it — the
variance study already established that this checkpoint swings this far at
temperature 0, so a single run cannot claim an improvement either.

**What the run confirmed about the harness, not the model.** `extraction_schema_mode`
is still `"expected"`: the benchmark feeds each fixture its gold schema, while the
classifier actually agreed on the schema in only 11/16 cases. The five live
mismatches are edu-04 (predicted `job`, gold `generic`), scholarship-01
(`education` vs `job`), and fitness-01 / hinglish-01 / recipe-01 (`education` vs
`generic`). In production those reels are extracted against a different field set
than the one being scored, so the recall number above is an upper bound on
routing-correct cases only. This is the concrete justification for Phase 8B.1.

Model field-name violations are still reported separately rather than absorbed:
edu-03 emitted `deadline`, edu-05 emitted `company` and `experience_required`.

**edu-05 stays 0/2 in the harness, and that number no longer tells the whole
story.** The trace shows the model again emitted `technologies = "AI, LLM, CSV"`
with no LinkedIn, and the claim-term guard dropped it (sim 0.0). But the golden
harness replicates only the fact loop, so it cannot see the Phase 7 deterministic
recovery that runs in the real stage. `test_edu05_platform_recovered_without_model_help`
now drives `stage_classify_extract` on the actual edu-05 fixture with an extraction
that returns zero facts, and asserts that `technologies=linkedin` still persists,
sourced from the transcript span at t=0.0 ("...how to mine your LinkedIn
connections in 60 seconds") with a real quote and timestamp. The platform is
searchable end-to-end even though the model never names it; the accepted
limitation is about model emission, and the mitigation is deterministic.

## September 20, 2026 — summary grounding gate (Phase 8A.2)

The Evidence Ledger covered facts and (as of Phase 7) entities. `reels.summary`
was never checked, yet migration 2 indexes it into `reels_fts`, so an
ungrounded model narrative was simultaneously the top UI field and a keyword
search surface. `reels.summary_grounding` now stores the share of the
summary's claim terms found anywhere in the source spans
(`app/knowledge/evidence.py:grounding_ratio`), recomputed on manual edit.

**Calibration, measured on the 21 real Phase-6 reels** (read-only query of
`data/reelvault.db`, terms extracted with the same `_claim_terms` the fact
guard uses):

| Grounding ratio | 1.00 | ≥0.9 | ≥0.8 | ≥0.7 | ≥0.6 | ≥0.5 |
|---|---|---|---|---|---|---|
| Summaries passing | 8/21 | 11/21 | 15/21 | 15/21 | 16/21 | 18/21 |

Median 0.909, mean 0.799. Requiring full support would flag 13 of 21 real
summaries — abstractive wording legitimately puts a few terms outside the
source vocabulary, and a marker that fires two thirds of the time teaches
the user to ignore it. `SUMMARY_GROUNDING_MIN = 0.80` flags the loose tail:
6 of 21, with the measured distribution
`[0.0, 0.33, 0.43, 0.5, 0.57, 0.69 | 0.8, 0.88, 0.89, 0.89, 0.91, 0.94, 0.95, 1.0 ×8]`.
One real summary shared **zero** terms with its source (reel 1: 15.4 s, no
transcript segments, 4 OCR lines) — exactly the case the marker exists for.

Honest limits of this gate:

- It is lexical, not semantic. A high ratio does not make a summary true; it
  only says the words came from somewhere in the source.
- The 0.80 boundary is a knife-edge on this corpus: one real summary sits at
  exactly 0.80 and passes. Re-tuning changes that row's verdict.
- `key_takeaways` / `action_items` carry the same leak and are **not** gated:
  applying the same 0.80 rule would drop 45 of 121 real items (37%), a recall
  cost with no measured wrongness behind it. They are also absent from
  `reels_fts`, so they cannot corrupt search the way the summary could. Left
  as a documented open item.
- Pre-v5 rows store NULL and are reported `summary_verified: false` — they
  were never checked, and the UI says so rather than back-filling a verdict.
  **This produced a real UX defect, now fixed:** immediately after the v5
  migration all 21 live reels displayed the `not source-checked` chip, because
  none had been re-extracted. Honest, but it reproduced the cry-wolf problem
  the 0.80 calibration exists to prevent, arriving from the unmeasured side
  rather than the ungrounded side.
- **Backfill executed 2026-09-20** (`scripts/backfill_summary_grounding.py`,
  owner-authorized). No model call is needed — transcript, OCR and caption
  are already stored. Dry run reproduced the calibration exactly (21 rows,
  6 below the gate: reels 1 @0.0, 4 @0.333, 15 @0.429, 21 @0.5, 8 @0.571,
  22 @0.688), `--apply` persisted all 21 ratios, and a second run reported
  "nothing to backfill". The API now serves 15 verified / 6 flagged with no
  NULLs, and the UI was checked in the browser on the two extremes: reel 2
  (grounding 1.0) renders no chip, reel 1 (0.0) renders `not source-checked`
  in the warn colour. The script only writes NULL rows, so a re-extracted or
  user-corrected measurement is never overwritten.
  A pre-backfill snapshot was taken at
  `D:/Temp/user/reelvault_pre_backfill_20260920_183021.db` (temporary).
- No reel was re-extracted, so the stored ratios come from measuring each
  persisted summary against the sources already in the database — they are not
  a re-extraction result, and the summaries themselves are unchanged.

## September 17, 2026 — education field-keyed candidate (not accepted)

Education-only output uses validated field arrays converted to the existing
`BaseExtraction` contract, with an explicit deadline field and temperature 0.
Other schema prompts, evidence thresholds and golden labels are unchanged.
No saved reels were reprocessed or deployment performed.

| Metric | Prior checkpoint | Field-keyed trial |
|---|---:|---:|
| Kept-field recall | 0.762 | 0.714 |
| Unsupported-kept heuristic | 0.038 | 0.019 |
| Minimum-kept rate | 0.938 | 0.875 |
| Kept-fact deadline recall | 1.000 | 0.500 |
| Malformed output rate | 0 | 0 |

Trial: `RV_EVAL_TRACE=1 ... pytest tests/test_ai_eval.py -q -s`, benchmark
1 passed in 119.46s, 16 cases. Private trace:
`D:/Temp/user/rv_test_8eyk9c97/eval_results.json`.
Loose aggregate gates passed; quality acceptance did not. edu-01 recovered
1/1 fields; edu-03 remained 0/1; edu-05 remained 1/2. Differences in unchanged
job outputs also contributed to aggregate regression; causality is not
established by this single run.

Trace findings:
- edu-05 emits networking and LLM, but omits LinkedIn as a technology.
  Conversion tests confirm field mapping preserves the model's entries.
- edu-03 emits `October 5th` with quote `[00:15] OCR: CLOSES OCT 5`;
  the evidence matcher rejects it. Parsing `October 5th`, `5th of October`
  and `CLOSES OCT 5` produces October 5 in focused tests. This is not a
  date parser failure.
- An isolated actual-stage replay of that deadline entry with edu-03 source
  artifacts drops the fact but persists October 5 using the existing
  deterministic source fallback. The benchmark deadline metric does NOT
  measure this fallback, and must not be described as end-to-end recall.
- A further instruction-only revision failed the targeted live checks
  (1 passed, 2 failed in 24.61s). Only that revision was removed.
- A bounded second-pass recall experiment (separate technologies/deadline
  call, source-line verification, dedupe) also failed the same live checks
  and was removed. Both mechanisms are documented as measured dead ends.
- A third mechanism, an education-domain worked example (watercolor/Procreate,
  no fixture values), was tried and measured WORSE: the example taught
  paraphrased values ("networking simplification") that the claim-term guard
  correctly dropped, losing even previously-passing topic fields
  (edu-01/03/05 all false live). Reverted; job prompt untouched throughout.
- Verified the input hypothesis is false: the model DOES receive "LinkedIn"
  and "RAG" in transcript+caption spans; the omission is model value
  selection, not input construction.
- Entity bridge also measured dead: live runs show the model emits tool
  entities for edu-01 (FAISS, Qdrant) but NO LinkedIn entity for edu-05, so
  an entity-to-fact bridge cannot recover the missing value.

FIX DELIVERED (uncommitted): entity evidence guard. Live runs exposed the
model copying "Zylker" from the job example into education ENTITIES, and
stage_classify_extract persisted entities with NO evidence validation -
a direct violation of the trust policy (AGENTS.md section 8). Entities now
pass the same find_evidence gate as facts; unsupported names are dropped
and logged in the classify_extract event; surviving reel_entities rows keep
the matched span's quote and timestamp. Regression test:
test_entity_hallucination_is_not_persisted (fabricated Zylker dropped,
supported LinkedIn kept with t_s=0.0). Full suite 197 passed, 2 skipped.
The golden harness replicates only the fact loop, so benchmark metrics are
unaffected by this change by construction.

Decision: the field-keyed candidate was REVERTED to checkpoint c00be1d
behavior (git checkout of router.py, schemas.py and their tests).
CORRECTION after a 4-run variance study: the original revert rationale
("checkpoint measured better: 0.762 vs 0.714") was a single-run
misattribution. The unchanged checkpoint itself measures recall 0.714,
0.714, 0.810 and deadline recall 0.75, 0.75, 1.00 across three fresh runs
(plus the earlier 0.762/1.00 run); edu-03 flips 0/1 <-> 1/1 between runs
and job-02 flips 3/4 <-> 4/4. Education field hits were 2/4 in BOTH the
checkpoint and field-keyed candidates. The aggregate differences that
drove the revert were run variance at temperature 0.15, not candidate
quality. The revert outcome stands for a different reason: field-keyed
did not fix edu-05 either (0/2 in both) and adds schema complexity.

Stable finding across all 4 runs and every prompt variant tried: edu-05
technologies is 0/2 - Qwen2.5-3B never emits LinkedIn as a technology
under the checkpoint prompt, field-keyed prompt, extra wording, second
pass, worked example, or grammar-constrained output. This is a model
capability ceiling, not a prompt/plumbing defect. Fix options require an
owner decision: larger model, a different extraction paradigm, or
accepting the limitation.

Owner decision (2026-09-18): limitation ACCEPTED. No larger model, no
extraction-paradigm change, no further prompt iterations for education
recall. The entity evidence guard is the accepted scoped Phase 7
increment. edu-03 deadline remains run-variance (flips between runs of
unchanged behavior); documented, not chased. Remaining pre-existing
follow-ups stay documented and out of scope: quote-floor rescue policy
review and golden-corpus provenance review.

Recommended future path (NOT implemented, out of accepted scope): the
deterministic pre-pass already computes pre["skills"] from SKILL_HINTS
(app/pipeline/stages.py) but never persists it - dead code today. A
curated platform/tool hint list persisted as evidence-gated facts (same
pattern as the URL/email pre-pass, matched span as quote/timestamp)
would recover edu-05 technologies deterministically without model
changes and without fixture contamination. Requires owner authorization
as a new scoped phase.

Kept from this iteration (additive, verified): deadline surface-form parse
regression tests, the isolated edu-03 stage replay proving deterministic
source-fallback persistence (evidence score 0, persisted 2026-10-05), and
the finding that the benchmark deadline metric does not measure that
fallback. Remaining limitation: education platform/tool recall (edu-05
LinkedIn) is unsolved; fixing it requires a different extraction strategy,
not more prompt wording.

## September 17, 2026 — frozen-output matcher replay

No production changes or model calls. `tests/test_evidence_replay.py` compares
committed matcher `f42fbef` against the current guard on identical saved raw
facts and fixture spans. It rejects source/prompt mismatches, writes private
per-fact decision differences beside the input trace, and reports field and
deadline scores using metric v2 for both matchers.

Run with `RV_EVAL_REPLAY=<absolute trace path> ... pytest
 tests/test_evidence_replay.py -q -s`. This runs only on explicit opt-in;
normal tests do not depend on private temporary files.

| Frozen trace | Old/current kept | Old/current field hits | Deadlines | Old/current minimum-kept cases |
|---|---|---|---|---|
| rv_test_7h5g488o | 66 / 51 | 16/21 / 16/21 | 4/4 both | 14/16 / 14/16 |
| rv_test_yp_z23xu | 76 / 55 | 18/21 / 17/21 | 4/4 both | 15/16 / 14/16 |

Both replay invocations: **3 passed**. Full non-AI suite: **190 passed,
2 skipped** (includes the opt-in replay skip), 10 warnings in 13.53s.
Saved reports verified under each input's temporary directory as
`eval_results_replay.json`; no private traces committed. Repeated evaluation
of each input and 66 repeated identical inputs across these two captures
produced identical current-matcher results (101 unique inputs checked).

Trace review, not a human-labeled precision estimate:
- Benefit: job-03 placeholder claims (`Not specified`) and invented `Remote`
  values are rejected. The name-as-company claim on edu-05 loses its unrelated
  networking quote support.
- Confirmed recall cost: the one lost named-field hit in trace B is edu-05
  `topic=Networking Simplification` versus `simplify networking`. Trace A
  also loses `2-4 yrs` versus `years` (not a scored field in that fixture).
- Multi-segment information is lost: edu-01's combined concepts and edu-05's
  combined instructions cannot fit one supporting span. Some proposed quotes
  also omit key claim terms or paraphrase source text, so simply pooling all
  spans would be an unsafe repair.
- `edu-05` kept count: 4 -> 2 in trace A, 7 -> 0 in trace B. The guard has a
  real false-negative cost, not merely run-to-run model noise. Do not deploy
  or call Phase 7 complete based only on aggregate gate passes.

Acceptance pinned by `test_edu05_conservative_claim_acceptance`: using the
actual edu-05 fixture, `LinkedIn networking` with its first transcript quote
passes and retains timestamp 0.0; `Zylker` and `Networking Simplification`
fail. The latter is an acknowledged lexical false negative, not a judgment
that the paraphrase is factually wrong. All 16 claim-support tests passed
once after adding this test. No production changes were made in this step.

Final integration verification: pipeline regression proves company=Zylker
with an unrelated networking quote is not persisted, while supported LinkedIn
networking persists with timestamp 0.0. Full suite: **192 passed, 2 skipped**,
10 warnings, 6.42s. One final live golden run: **1 passed** in 126.33s; recall
0.762, unsupported-kept heuristic 0.019 (1/53), minimum-kept 0.875, deadline
recall 4/4, malformed 0, multilabel 0.750. edu-05 still 0 kept / 3 dropped,
fields 0/2. Output: `D:/Temp/user/rv_test_wf4tigqv/eval_results.json`.
These sampled metrics do not replace the paired replay comparison above.

This closes the frozen-output investigation, not Phase 7. Do not automatically
expand matching or rewrite the labels to recover recall. If pursuing broader
recall, evaluate wording variants and bounded source windows separately on
these frozen traces while preserving unrelated-claim rejection. Quote-floor
policy and semantic support remain unresolved.

## September 17, 2026 — claim-term guard checkpoint

`find_evidence` now requires meaningful claim terms to occur in the candidate
source span before its quote-similarity score can count. The captured Zylker
claim/networking-quote regression failed at 1.0 before the change and passes
now. Canonicalization covers the observed number/date format mismatches
(zero, ordinals, month abbreviations, integer k amounts, LPA). This is a
lexical filter, not semantic entailment. Production prompts and golden labels
are unchanged. Existing short-value rescue behavior is unchanged, including
its ability to accept missing/short quotes; strict quote-floor enforcement
remains open rather than being claimed complete.

| Metric v2 | Immediate pre-fix | Initial guard | Final guard + normalization |
|---|---|---|---|
| Kept-field recall | 0.762 | 0.667 | 0.810 |
| Unsupported-kept heuristic | 0.227 | 0.040 | 0.055 |
| Minimum-kept rate | 0.875 | 0.875 | 0.875 |
| Deadline recall (4 positives) | 1.000 | 0.500 | 1.000 |
| Multilabel accuracy | 0.750 | 0.750 | 0.750 |
| Malformed JSON | 0.000 | 0.000 | 0.000 |

Commands verified on final code:
- Non-AI suite: **188 passed, 1 skipped**, 10 warnings, 7.20s.
- `RV_EVAL_TRACE=1 ... pytest tests/test_ai_eval.py -q -s`: **1 passed**,
  163.55s. Saved 16-case output verified at
  `D:/Temp/user/rv_test_yp_z23xu/eval_results.json` (private temporary file).
- `git diff --check`: passed.

### Separate follow-up: remaining recall and trust limitations

The runs have different generated outputs; improvements cannot be attributed
entirely to the matcher without replaying identical traces. No more tuning
was done after this checkpoint.

- edu-05: **0 kept / 9 dropped**, fields 0/2 in the final run. Rejected claims
  include `Networking Simplification`, `Free networking playbook`, and combined
  technology lists. Requiring every claim term in one span loses inflections
  and legitimate information spread across segments. Review these as false
  negative candidates using frozen outputs, rather than relaxing the guard
  until an aggregate gate passes.
- Dates retain only the tested normalization behavior; composite ordinals,
  negation, role relationships, and arbitrary paraphrases are not verified.
- Quote matching plus claim token presence still cannot establish a claim's
  truth. Existing rescue/long-value fallback needs a separate quote-policy
  decision. Summaries and field semantics remain outside this guard.
- Phase 7 remains incomplete. Existing reels were not reprocessed or deleted;
  no service restart/deployment of this guard was performed.

## September 17, 2026 — evaluator correction and trace verification

No production behavior or golden labels changed. The uncommitted field-name
prompt trial was removed: both earlier trial runs still scored edu-05 0/2.

Metric version 2 scores named fields from **evidence-kept facts**, not raw
model facts. Multiple values for a field are tested independently; a later
value cannot overwrite an earlier correct match. Schema field violations are
reported separately, not silently accepted or remapped. Exact provider-boundary
traces are opt-in. Output writes no longer suppress exceptions.

Verification:
- `pytest tests/test_eval_scoring.py -q`: 9 passed.
- `pytest tests -q --ignore=tests/test_ai_eval.py`: 173 passed, 1 skipped.
- `RV_EVAL_TRACE=1 ... pytest tests/test_ai_eval.py -q -s`: 1 passed,
  150.01 seconds, 16 cases. Saved output verified at
  `D:/Temp/user/rv_test_zwzpsagr/eval_results.json` (temporary, not committed).
  All 16 traces present; per-case field hits recompute to 16/21.

| Metric | Metric v2 baseline |
|---|---|
| Kept-fact named-field recall | 0.762 (16/21) |
| Category multilabel accuracy / macro F1 | 0.750 / 0.509 |
| Schema agreement | 0.688 |
| Unsupported-kept token-overlap heuristic | 0.082 (5/61) |
| Minimum-kept rate | 0.938 |
| Malformed JSON | 0.000 |
| Deadline parse recall | 1.000 (4 fictional positive cases) |
| Mean classify + extract duration | 9.34 seconds |

These metrics are not evidence of a production improvement; recall semantics
changed and model outputs vary. No thresholds or labels were relaxed.

### Open findings — supersede earlier claims of complete fixes

- edu-05 remains 0/2; the saved run contains invalid fields `company` and
  `technology`. A separate exact trace returned valid `technologies=AI, LLM,
  CSV`, which does not satisfy the explicit `linkedin` needle. Those values
  are not treated as synonyms. Missing topic, wrong field names, and a narrow
  label are different failure modes.
- The education extraction prompt includes a job-shaped Zylker example. A
  captured completion copied `company=Zylker` and attached a genuine networking
  quote; `find_evidence` scored it 1.0 and kept it. Quote presence does not
  establish claim support. The earlier anti-copy instruction did not eliminate
  prompt leakage or unsupported claims.
- Extraction still uses the **expected** schema in the harness. It measures
  extraction in isolation, not production routing. `job-03` is an interview
  scenario, not proof of a vacancy; forcing it into job extraction needs label
  review before further classifier tuning.
- Empty expected-deadline lists are not negative tests; absence of parsed
  real-world deadlines does not establish zero recall without positive labels.
- The unsupported-kept metric is a token-overlap heuristic, not human fact
  precision. Passing the permissive aggregate gates does not establish safety
  of individual facts. Earlier inflation-eliminated claims are not supported
  by the subsequent runs.

Next scoped work: reproduce quote/claim mismatch in a deterministic regression
and evaluate claim-support hardening without weakening the quote floor. Review
real-source fixture provenance and field labels separately before relabeling.

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

## Run — September 9, 2026 (PHASE 7 step 4b: classify prompt job-scenario rule)

Change: classify prompt adds a rule — job interview questions / role-play
scenarios / role-title-at-company content -> Job (fixes job-03 schema drift:
education vs job). Two post-change runs; gates 1 passed both.

| Metric | Step-4a baseline | Run A | Run B |
|---|---|---|---|
| Schema agreement | 0.562 | **0.688** | **0.688** |
| Multilabel accuracy | 0.781 | 0.750 | 0.750 |
| Macro F1 | 0.531 | 0.560 | 0.517 |
| Field recall | 0.857/0.810 band | 0.857 | 0.762 |
| Unsupported-kept | 0.141/0.129 | 0.137 | 0.301 (noise) |

Verdict: schema drift FIXED and stable (job-03 job/job both runs;
schema_agreement +0.126 stable). Cost: multilabel −0.031 stable (gate
≥0.60 fine) and edu-04 ("Nobody's hiring AI enthusiasts") now picks job
schema — categories were already Job-primary there; expected generic is
inherently unstable for that item. Run B shows extraction noise (job-03
12/9 unsup again) — LLM variance, unchanged code path for extract.
Not overfitting the rule further (would be tuning by intuition).

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

---

## Text-source baseline (v2), 2026-09-21, N=1 run, Qwen2.5-3B local

First golden rows for new kinds: `paper-01` (real arXiv GPT-3 abstract),
`xpost-01` (hiring tweet), `article-01` (how-to article). Harness gained
`document` field support mirroring `build_spans` paragraph chunking.

| id | kept/dropped | unsupported | fields | schema | gate |
|---|---|---|---|---|---|
| paper-01 | 4/2 | 1 | 1/1 | education/education | pass |
| xpost-01 | 9/2 | 2 | 2/2 (role, location) | job/job | pass |
| article-01 | 1/0 | 0 | — | generic→Educational relabelled | pass (min 1) |

Aggregate with 19 items: field recall 0.792, min_facts_kept_rate 0.895,
unsupported_kept_rate 0.063, deadline parse_recall 1.0, malformed JSON 0.

Findings:
- Matcher is NOT the text-source bottleneck: verbatim document quotes score
  1.0; live-run drops were genuine model paraphrases (§9 error analysis,
  2026-09-21).
- Model IS the bottleneck: article-01 yielded 1 fact from a numbers-dense
  body; paper quotes get paraphrased; xpost variance 1..9 facts across runs
  (single live ingest kept 1, eval run kept 9 on equivalent text). N≥3
  repeat protocol required before any quality claim.
- xpost/paper each kept 1-2 "unsupported" facts via short-value rescue —
  watch: rescue cap 0.75 is doing its job but text sources raise its usage.
- article-01 expected categories relabelled (Educational primary) after
  human review of prediction — labels, not model, were wrong.

Next before trusting text extraction: N=3 protocol on text rows, then A/B
the RV_LLM_BACKEND cloud seam on the same rows (owner approval needed).

### N=3 repeat (text rows, same session)

Runs 2 and 3 reproduced run 1 exactly for paper-01 (kept 4, fields 1/1) and
xpost-01 (kept 9, fields 2/2). article-01 jitters kept 1↔3 and surfaced
kept-unsupported facts (2) — the only unstable row.

Correction to the note above: the "1 vs 9 facts" variance was NOT
run-to-run model jitter. Eval harness pins schema (expected mode) and runs
at temp 0 — deterministic. The spread came from comparing the *live routed*
pipeline (classification picks schema; wrong schema = wrong fields) against
the harness. Live routing is the variance source, which is exactly the
predicted-schema gap Phase 8B was meant to close.

unsupported_kept on text rows: 5 of 16 kept facts ride the short-value
rescue (cap 0.75). Tolerated for now; revisit after trafilatura gives
articles real text.

## Cloud A/B: Groq gpt-oss-120b vs local Qwen3B (text rows, 2026-09-21)

`GOLDEN_FILTER=paper,xpost,article RV_LLM_MODEL_NAME=openai/gpt-oss-120b`
(single run each; 429 backoff path exercised; gate assert is full-run only)

| row | local kept/unsup | groq kept/unsup | fields | notes |
|---|---|---|---|---|
| paper-01 | 4/1 | 3/1 | both 1/1 | comparable; groq dropped 3 vs 2 |
| xpost-01 | 9/2 | 7/0 | both 2/2 | groq cleaner: zero unsupported, exact Job category |
| article-01 | 1-3/0-2 | 5/5 | — | RED FLAG: all 5 groq facts ride short-value rescue |

Schema agreement 3/3 (groq) vs 3/3 (local, expected-mode). No winner overall:
groq wins job-tweet precision; on the article it produces more facts but
none quote-verified — rescue cap 0.75 doing the work, which is exactly the
looseness §8 warns about. Decision: keep local as default for video (tuned +
quota); cloud stays opt-in per .env. Action item: audit short-value rescue on
document spans before trusting article-row facts.

---

## Evidence audit: verbatim containment inside a document span (2026-09-21)

The cloud A/B left one action item: "audit short-value rescue on document spans
before trusting article-row facts." Done, with two reusable instruments and one
narrow rule.

### Measurement

`scripts/audit_rescue.py` (read-only, runs against the live library) offers every
extracted fact value to every reel it did NOT come from. A hit on a foreign reel
is a value that containment alone would have authenticated using someone else's
source text — the false-accept rate of the door, grouped by the span source that
carried it, because that is what the rule keys on:

```
FOREIGN values - containment that would authenticate someone else's claim:
  ocr         hits   10   shipped rule admits   10   uniform budget would admit    3
  caption     hits    8   shipped rule admits    8   uniform budget would admit    1
  document    hits    8   shipped rule admits    0   uniform budget would admit    0
  transcript  hits    7   shipped rule admits    7   uniform budget would admit    7

OWN values - support the budget could cost:
  ocr         hits   22   shipped rule admits   22   uniform budget would admit   20
  transcript  hits   18   shipped rule admits   18   uniform budget would admit   18
  caption     hits   17   shipped rule admits   17   uniform budget would admit    6
  document    hits    4   shipped rule admits    1   uniform budget would admit    1
```

On document spans the rule removes all 8 foreign false accepts and 3 of the 4
own-source crutches; transcript, OCR and caption support is untouched. The
"uniform budget" column is why the scope is documents-only: applying the same test
everywhere would discard 11 of 17 genuine caption hits for no additional gain.

### The three facts that started it

```
reel 26 (paper)   difficulty='Research'          contained in spans of 2810, 3012, 3081 chars
reel 27 (article) difficulty='Difficult'         contained in a 10,000-char span (truncated body)
reel 28 (paper)   topic='Transformer model'      contained in spans of 2810, 3091 chars
```

The first two matched the arXiv license-boilerplate page. After the change,
replaying them against the stored sources gives
`Research` 0.0 and `Difficult` 0.0 (dropped), `Transformer model` 0.75 (kept, no
longer claimed as strong), and the legitimate `topic='Attention Is All You Need'`
on the same reel keeps 1.0.

### Three doors, not one

The rescue path named in the A/B note was only the first:

- **A** `evidence._short_value_match` — quote AND value under `MIN_QUOTE_CHARS`;
  returned a flat 0.75 for containment in a span of any size.
- **B** `find_evidence`'s `value in span -> max(s, 0.75)` boost — any value
  length, on whichever span the loop happened to be examining.
- **C** `_sim`'s verbatim shortcut — `a in b` returned **1.0** with no floor on
  probe length and no regard for span size. This is the door that made
  `difficulty='Research'` look like *strong* evidence, and it is what the A/B's
  article facts travelled. The module docstring had promised verbatim containment
  was "designed weak support (0.75, never 1.0)"; the shortcut contradicted it.

### Shipped rule

One predicate, `evidence._containment_is_local`: a document span may carry a
verbatim match when it is within `20 * len(match) + 120` normalized characters of
it (`CONTAINMENT_SPAN_CHARS_PER_MATCHED` / `_SLACK`). Beyond that, doors A and B
contribute nothing and door C returns 0.75 instead of 1.0 — so a genuine quote is
never discarded for size reasons, it just stops claiming certainty.
`MIN_STRONG_QUOTE_CHARS = 60` escapes the cap entirely: a verbatim run of ~10
words cannot be coincidence, and the longest accidental match in the live library
was 25 characters. `_sim`'s shortcut also gained the `MIN_QUOTE_CHARS` floor it
always should have had, so one word can no longer manufacture a perfect match in
any span kind.

A document-chunking alternative was built, measured and **reverted**: splitting
pages into ~600-char spans stranded two real paper-01 facts whose 200-character
quotes crossed a boundary (4 kept -> 2 under both matchers). The budget alone gets
the same false-accept reduction without that cost.

### Cost, measured

`scripts/replay_matcher_rules.py` extracts all 19 golden rows once, caches the
extractions, and scores the identical facts under the pre-change matcher (loaded
from `git show <rev>:app/knowledge/evidence.py`, not retyped) and the current one:
**66 facts kept before, 66 after, 0 flips.** The local model quotes properly, so
none of its golden facts travelled these doors — they were the cloud's route.

### Two protocol findings

1. **Do not compare two `tests/test_ai_eval.py` runs to judge a matcher change.**
   Before/after full runs reported kept 65 -> 72 and `unsupported_kept_rate`
   0.062 -> 0.111 while raw per-item fact counts also differed (edu-05 produced
   11 facts in one run, 4 in the next) at temperature 0. llama-server is not
   run-to-run reproducible at fact level, so single-run eval deltas are noise at
   this scale; use the replay harness.
2. **trafilatura 2.2.0 returns flat text.** `extract()` produced zero newlines for
   a multi-paragraph page, so an article body arrives as one span with no
   paragraph granularity and no position locator. The evidence rule no longer
   depends on chunking, so this stays an open ingestion-quality item rather than a
   blocker.

### Gate

`tests/test_evidence_document_locality.py` (12 tests) pins: a page is one span
and stays page-located; a short value inside a page is not evidence; a two-word
phrase inside a page is capped at 0.75 with its page locator intact; the same
phrase in a local span is still 1.0; a ten-word quote is unaffected by page size;
a mid-length phrase is capped; short document spans still earn 0.75; transcript
rescue and OCR/caption behavior unchanged; a value below the rescue floor still
needs a real quote; and both constant boundaries.
Suite: 336 passed, 2 skipped.

Not covered by the golden set: its largest document row (`paper-01`) is a
1,828-character abstract, so the replay proves no regression rather than exercising
the rule. The live library is the test, and a multi-page-PDF golden row remains
the missing coverage (reel 28's 15-page PDF is a ready candidate).

**Superseded 2026-09-22:** that missing coverage now exists as `paper-02`. See
the next section.

## September 22, 2026 — 20-row baseline, and a pre-existing matcher defect

llama-server restarted on :8091 (local `qwen2.5-3b-instruct`, `router_served_by_counts:
{"llamacpp": 20}` — no cloud involved). `RV_EVAL_TRACE=1 pytest tests/test_ai_eval.py -q -s`
→ **1 passed in 170.43 s**, 20 cases. Private trace:
`D:/Temp/user/rv_test_22ogyute/eval_results.json` (not committed).

| Metric v2 | 20-row run | Prior 19-row (Sep 20) |
|---|---:|---:|
| Schema agreement | 0.700 (14/20) | 0.688 (11/16) |
| Kept-fact named-field recall | 0.760 (19/25) | 0.810 (17/21) |
| Content presence (taxonomy-gap, n=3) | **0.000** | 0.667 |
| Evidence-kept facts | 68 | 66 |
| Unsupported-kept heuristic | **0.103** (7/68) | 0.036 |
| Minimum-kept rate | 0.900 | 0.875 |
| Deadline parse recall | 1.000 (4/4) | 1.000 |
| Category multilabel / macro-F1 | 0.750 / 0.525 | 0.750 / 0.509 |
| Malformed JSON | 0.000 | 0.000 |
| Mean classify + extract latency | 8.47 s | 7.32 s |

`paper-02` (2-page Attention excerpt, page markers, spans of 2,857 and 1,133 chars)
agreed on schema, kept 2 facts, dropped 2, `min_facts_kept: 1` met. Its kept fields
were `advantages` and `application` — **not in `EducationFacts`** — so it reports
`invalid_fields: [advantages, application, company, model]`, the same model
field-name violation already logged for edu-03/edu-05.

### The two out-of-band numbers are not regressions

**Content presence 0.0.** Traced to model emission, not matching: fitness-01 and
recipe-01 each kept exactly one fact and both emitted the literal value
`"20 minutes"` (both transcripts contain "twenty minutes") instead of the needles
`burpee` / `paneer`; hinglish-01 kept 0. The denominator is 3 items, so a single
item flipping moves the metric by 0.333. The replay below shows the matcher is
flip-free on these rows, which rules out the evidence rule as the cause. Per the
protocol finding above (llama-server is not run-to-run reproducible at fact level),
this is recorded as variance on a structurally fragile metric, **not** as a
regression — and not as evidence that the taxonomy-gap probes were wrong either.

**Unsupported-kept 0.103.** 4 of the 7 unsupported kept facts are on the two paper
rows. Both paper rows also score `topic: false` against their needles, so the
document rows are jointly weak in a way the video rows are not.

### `retry`/replay re-verified at 20 rows

`scripts/replay_matcher_rules.py --against 0a29d04^` extracts all 20 rows once and
scores identical facts under the pre-containment matcher and the current one:
**68 kept before, 68 after, 0 flips**, widest span 2,810 chars. The earlier
"66 → 66, 0 flips" claim was measured on 19 rows; it holds at 20, and now actually
crosses a multi-page document span.

### Pre-existing defect found: the Jaccard prefilter cannot see inside long documents

**Status: fixed 2026-09-23 by `_local_coverage`; see "Fix applied" at the end of
this section.** The report below is kept as found, including the `sim 0.0` verdict,
which was that extraction's — today the same fact reaches the rule at 0.75 → 0.989.

`paper-02` dropped `model="Transformer"` whose quote is
`"proposed a new simple network architecture, the Transformer, based solely on
attention mechanisms"` — the document says "We **propose** a new simple network
architecture, the Transformer, based solely on attention mechanisms". Near-verbatim,
one word inflected. It scored `sim 0.0`.

This is **not** caused by the containment rule. Replaying the identical quote
against the matcher loaded from `0a29d04^` gives `sim=0.000, kept=False` — byte-identical
verdict. The cause is in `_ratio`'s prefilter: `_jaccard(quote, span)` is measured
against a 2,857-char page span, and Jaccard is `|A∩B| / |A∪B|`. Measured on this
exact pair: the quote has **13** unique normalized terms, page 1 has **244**, and the
intersection is **13** — every single quote term is present in the span. Jaccard is
therefore 0.0533, and because the union is dominated by the span it **cannot exceed
0.0533 even for a perfect match**, against `JACCARD_MIN = 0.20`. Exact containment
would have caught the quote, but the model inflected one word, so containment failed
and the fallback similarity check was prefiltered away before it ever ran. The
length-independent overlap coefficient `|A∩B| / |A|` on the same pair is 1.0000.

Consequence, and it is the honest one: **any document quote that is near-verbatim
but not exactly verbatim is silently dropped, regardless of how well it supports the
claim.** The containment rule made large-span matches *weaker*; this makes them
*impossible* once a single word differs. The two are different defects and only
the first was addressed.

**A prefilter tweak does not fix it — measured.** Bypassing `JACCARD_MIN` and calling
`SequenceMatcher` directly on the same pair returns **0.006**, not a high score,
because `ratio()` is `2M / T` where `T` is the total length of *both* strings, so a
2,857-char span dominates the denominator exactly as it dominates the Jaccard union.
Short foreign values score 0.000 the same way. In other words, for a large document
span **neither** similarity measure can express "this quote occurs here" — only exact
containment can, and one inflected word defeats it.

**Fix applied — `app/knowledge/evidence.py:_local_coverage`.** For a document span
only (`containment_must_be_local`) and a quote only at `MIN_STRONG_QUOTE_CHARS` or
longer, `_sim` now takes `max(_ratio, _local_coverage)`, where coverage is the
fraction of the *quote* carried by one contiguous run of `get_matching_blocks()` —
divided by the quote, never by the span. The Jaccard prefilter is bypassed on that
branch because it is the prefilter that made the class unrecoverable, and
`autojunk=False` because difflib's popular-element heuristic fires on a span this
long and discards genuine matches (`ratio()` on one pair: 0.0048 default, 0.0667
without — both hopeless, but the run enumeration depends on it). `max(_ratio, …)`
because a document chunk about the size of the quote is what `ratio()` measures
well (119-char sentence, two words substituted: ratio 0.9384, coverage 0.7282).
Transcript, OCR and caption spans keep `_ratio` untouched.

**The branch certifies or says nothing, and that property is what four review
rounds bought.** Each round found the same shape — perfect on the quote it was
written for, accepting a quote the source never contained:

1. Summing *every* matching block: a probe stitched from two disjoint regions of one
   page scored **0.9919**, indistinguishable from verbatim (pre-fix 0.066, dropped).
2. Chaining blocks along one diagonal fixed the stitch but made *elisions free*:
   deleting one word from an otherwise verbatim run scored a perfect **1.0** — the
   exact shape of a quote that quietly drops a negation. Between consecutive blocks
   the probe advances `p_gap` and the span `s_gap`, and the diagonal moves by exactly
   `s_gap - p_gap`, so one quantity separates the two: a same-length typo (propose →
   proposed) moves it 0–1, an elision moves it by the skipped text. Runs continue
   within `MAX_LOCAL_DRIFT = 2` and pay their drift.
3. Ends were unanchored: `"not " + <123-char verbatim run>` scored **0.976**, because
   an edit near either end costs ~11% of a long quote. So the best run must start and
   end within `MAX_LOCAL_DRIFT` of the probe's own ends, and carry
   `MIN_LOCAL_COVERAGE = 0.9` of it.
4. Two survivors of that: the anchor counted *characters*, so **`"no " + run`** — a
   two-character word — scored **0.9841** and inverted the quote's polarity; and an
   **equal-length swap moves the diagonal by zero**, so 20 invented characters inside a
   247-character quote scored **0.9312**. Ends are now rejected when the residue meets
   the run across a space (a residue *inside* a word is the typo being forgiven), and
   `MAX_LOCAL_EDIT_CHARS = 8` bounds what no drift rule can see.

Anything failing those returns **0.0** from the branch and falls through to `_ratio`,
i.e. gets exactly the verdict it had before this change existed. The branch therefore
cannot weaken a score and cannot keep a fact that both old measures rejected unless
the quote really is the span's own text.

Measured, all against the fixture page or the real paper-02 page 1 (2,857 raw /
2,810 normalized chars):

| Probe | Before the fix | Shipped |
|---|---|---|
| 98-char doc quote, one word inflected, not exactly present | `_ratio` **0.000** (Jaccard 0.0574, prefiltered at 0.20) | **0.9796**, `find_evidence` **0.98**, kept, `page=1` |
| verbatim run with one same-length typo (`scale` → `skip`) | 0.000 | **0.9672** |
| same run with `research ` deleted (mid elision) | 0.000 | **0.000** — 1.0 at round 2 |
| `"not "` prepended / `, not` appended to the run | 0.000 | **0.000** — 0.976 / 0.969 at round 3 |
| `"no "` prepended (a two-character word, polarity-reversing) | 0.000 | **0.000** — 0.9841 at round 4 |
| 20 characters swapped at equal length inside a 247-char quote | 0.000 | **0.000** — 0.9312 at round 4 |
| quote stitched from two disjoint page regions | 0.066 | **0.000** — 0.9919 at round 1 |
| 101-normalized-char quote absent from the page | 0.000 | **0.000** — 0.049 at round 2 |
| `'Research'` / `'Difficult'` — the arXiv-boilerplate bait | 0.000 | **0.000** (coverage would be 1.000; the 60-char floor declines before the branch runs) |
| two-word and mid-length value containment in a page | 0.75 | **0.75** unchanged, `test_two_word_value_inside_a_page_is_weak_never_strong`, `test_a_mid_length_phrase_in_a_page_is_capped` |
| transcript / OCR / caption path | `_ratio` | **byte-identical**, `test_transcript_spans_keep_their_tuned_fuzzy_behavior` |
| suite | 350 | **358 passed, 3 skipped** |
| `find_evidence` cost per fact | — | **3.7 ms** on the real 2-page paper, **12.0 ms** on one 66k-char span |

`MIN_LOCAL_RUN = 5` (matches shorter than a word are ignored) is a noise filter, not
a tuned threshold: sweeping it 3→6 leaves both true probes at 0.9919 while the
absent-quote probe moves 0.059 → 0.079 → 0.049 → 0.000, ~9× below the gate.

**Accepted limit, stated rather than hidden:** a substitution that changes the
length by **at most 2 characters** still certifies, because nothing about it looks
like an elision — measured `scale` → `size` inside a 247-character quote at
**0.9879**. A polarity pair of that shape (`not` ↔ `now`, `gain` ↔ `loss`) passes
identically. Differences of 3 characters or more break the diagonal and are caught
(`research` → `study` → 0.0000, `quadratically` → `linearly` → 0.0000). No string
measure can do better: this branch answers "is this the source's own text?", not
"does this entail the claim?". That residual is the owner's review UI, and the quote
plus its page locator is what it shows.

Every row above is a committed test, not a notebook number:
`test_a_quote_that_skips_source_text_is_not_verbatim` (mid elision, three edited
ends, and the typo that must still pass),
`test_a_swap_of_equal_length_which_moves_no_diagonal_still_costs`,
`test_a_quote_stitched_from_disjoint_page_regions_is_not_evidence`, and
`test_a_long_quote_absent_from_the_page_stays_rejected`.

**Row level** — `scripts/replay_matcher_rules.py --against d60b631^` (the commit
before this rule existed), one extraction
(94 raw facts over 20 rows) scored under both matchers: total kept **65 → 65, 0
facts lost, 0 count flips**, widest span 2,810. The rule fires once and it fires
exactly as designed: paper-02 `topic='Transformer model'` goes **0.75 → 0.98**,
weak support becomes strong support, so the confidence tier rises without the row
count moving. The three paper facts that stay at 0.000 stay for unrelated reasons
the rule must not touch — a 28-char quote under the floor, and values like
`'High'` whose claim terms appear in no span. Run again against `ee9be18`, before
the branch existed at all: **65 → 65** as well.

**Whether a row's *count* moves depends on the extraction, not on the rule.** This
run's paper-01 quotes came back byte-verbatim, so containment already scored them
1.0 and the rule had nothing to recover. Re-running that same cached extraction with
one word of a verbatim quote altered (autoregressive → auto-regressive) moves it
**0.75 → 0.979**, and pairing such a quote with a value the page does not contain
gives the 0.000 → 0.98 case in the table. So the honest reading is: on today's
library this change upgrades the *strength* of document evidence rather than the
*number* of facts, and the count effect will only be visible on the next fresh
extraction. That is the model-variance caveat recorded above, and it is why the
deterministic table — not the row count — is the evidence here.

`scripts/audit_rescue.py` re-run over the live library (30 reels, 73 extracted
values): document spans admit **0 foreign** values and **1 own** value. The
false-accept door that audit exists to watch did not widen — the new branch keys on
`len(quote) >= MIN_STRONG_QUOTE_CHARS`, and those probes are values.

**Effect on the owner's actual library: none, and that is the point.** Every one of
the 15 facts stored on the 5 document-bearing reels was replayed through `ee9be18`
and the shipped matcher against its own reel's spans: **0 scores changed, 0 kept,
0 dropped.** The stored quotes are byte-verbatim, so they reach the containment path
and never enter the branch. What the change buys is headroom for the next ingest —
a paper or article quote that differs from the source by a word ending or a hyphen
will no longer be silently discarded.


### Also corrected this session

`.gitignore` covered `.env` but not `.env.bak*`; a config backup containing a live
cloud key sat untracked in the repo root. Rule added. `.env.example` verified still
tracked (the pattern is deliberately narrower than `.env.*`).

## September 23, 2026 — fresh 20-row run after the matcher fix

`pytest tests/test_ai_eval.py -q -s` → **1 passed in 187.40 s**, 20 cases,
`router_served_by_counts: {"llamacpp": 20}` — local `qwen2.5-3b-instruct`, no cloud.
Results: `D:/Temp/user/rv_test_odw51jgi/eval_results.json` (private, not committed).
Run without `RV_EVAL_TRACE=1`, so per-case kept *values* are not in the file.

| Metric v2 | Sep 23 | Sep 22 | Sep 20 (19-row) |
|---|---:|---:|---:|
| Schema agreement | 0.700 (14/20) | 0.700 | 0.688 |
| Kept-fact named-field recall | 0.760 (19/25) | 0.760 | 0.810 |
| Content presence (taxonomy-gap, n=3) | **0.333** | 0.000 | 0.667 |
| Evidence-kept facts | **65** | 68 | 66 |
| Unsupported-kept rate | 0.108 (7/65) | 0.103 (7/68) | 0.036 |
| Minimum-kept rate | **0.850** (17/20) | 0.900 | 0.875 |
| Deadline parse recall | 1.000 (4/4) | 1.000 | 1.000 |
| Category multilabel / macro-F1 | 0.750 / 0.525 | 0.750 / 0.525 | 0.750 / 0.509 |
| Malformed JSON | 0.000 | 0.000 | 0.000 |
| Mean per-reel latency (classify + extract) | 9.33 s (0.80 + 8.53) | 8.47 s | 7.32 s |

Classification is byte-identical to Sep 22 on all five reported numbers, which is
what a temperature-0 classifier looks like; everything that moved is at fact level,
i.e. inside the run-to-run band this document already warns about.

**The matcher change is not what moved anything here.** Three rows now miss
`min_facts_kept`: `recipe-01` (needs 2, emitted 1 fact total), `edu-05` (needs 2,
emitted 7 facts, all 7 dropped as unsupported) and `hinglish-01` (needs 1, emitted
1, dropped). The two paper rows are fine: `paper-01` kept 3, `paper-02` kept 2
against `min_facts_kept: 1`. The deterministic replay (`--against ee9be18`, same
extraction scored both ways) remains the evidence for the matcher change: **65 → 65
kept, 0 lost**. `edu-05` is the row to look at next — 7 raw facts, 7 dropped, on a
real-source LinkedIn excerpt, is the accepted model-capability ceiling still holding.

**Content presence 0.000 → 0.333.** One of the three taxonomy-gap needles landed
this time; the trace needed to name which one was not captured (`RV_EVAL_TRACE=1`
next run if attribution matters). Recorded as the same structurally fragile n=3
metric, not as a gain: a single item moves it by 0.333 in either direction.

## September 23, 2026 — trace run, and what actually limits fact recall

`RV_EVAL_TRACE=1 pytest tests/test_ai_eval.py -q -s` → **1 passed in 193.50 s**, 20
llamacpp. Private trace: `D:/Temp/user/rv_test_ho3dd82l/eval_results.json` (contains
source text; not committed). Kept **69**, dropped 32, unsupported-kept 0.116 (8/69),
`min_facts_kept` 0.900, deadlines 4/4, malformed JSON 0, 9.61 s/reel. Classification
was **again** byte-identical to both earlier runs (0.75 / 0.85 / 0.525 / 0.39 / 0.70).

Three runs today, same code: kept **65 / 65 / 69**, content presence **0.000 / 0.333 /
0.000**, min-kept **0.900 / 0.850 / 0.900**. `edu-05` went 0 kept → 2 kept across runs
that differ only in sampling. This is the protocol finding restated with a third
sample: per-fact numbers on this box carry a ±4 band, so use
`scripts/replay_matcher_rules.py` for any matcher question.

**Why facts drop, measured rather than assumed.** The trace lists every dropped fact
with its quote, so each of the 32 was classified offline against its own row's spans:

| Cause | n | Whose fault |
|---|---:|---|
| Fact emitted with **no quote at all** | 10 | model emission — the ledger cannot rescue an uncited claim |
| Quote verbatim inside one span, but the **claim-term prerequisite** rejected it (abstractive values like `'AI in Networking'`, `'Intermediate'`) | 10 | policy, deliberate: a label the source never uses is not evidence |
| At least one quote line absent from every span, or lines stitched from non-adjacent spans | 9 | model emission — paraphrase or recombination |
| **Every quote line verbatim, in consecutive transcript segments** | 3 (all `edu-05`) | **matcher — a real false reject** |

So of 32 drops, 3 are the matcher's. `edu-05`'s two-field `topic`/`technologies` miss
is that same row, which is why it looks so bad in a summary: its quotes cross whisper
segment boundaries, and `find_evidence` scores one span at a time — a 120-character
quote cannot fit a 107-character segment no matter how faithful it is. That is the
document-span ceiling from the section above, reappearing on the transcript path.

**Not fixed here, on purpose.** Making transcripts cross-segment is a second
loosening of the Evidence Ledger, and the first one took four review rounds to make
safe. A defensible shape: concatenate consecutive segments (same source, contiguous
index) into a composite span, keep the timestamp of the first, and require the quote
to be exactly contained in that composite — no fuzzy measure involved. Before writing
it, the same offline classification above should say how many *live-library* facts it
would recover, because the number on the golden set is 3 and the golden set's video
rows are the ones that already pass.

