# AI Extraction Evaluation

**Last run:** September 17, 2026 · **Branch:** `v0-core` · **Status: metric v2 baseline; Phase 7 incomplete**

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

`tests/golden/*.json` — 16 items: 13 fictional reel-like fixtures plus three
real-source-derived fixtures (`job-03`, `edu-04`, `edu-05`). The latter contain
selected/edited excerpts and require provenance and label review; they are
not complete raw recordings.

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

`min_facts_kept` is the floor of evidence-kept facts expected per item.

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
