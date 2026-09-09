# V0 Retrospective — Personal-Use Validation

**Status:** COMPLETED — window closed early at owner instruction (day 3 of 14,
2026-09-09). Reel-count target met (21 ≥ 20); answers below reflect 3 days of
batch-import usage, not two weeks of organic use — noted per question where
it limits confidence.

**Usage window opened:** 2026-09-06 (closed early: 2026-09-09, owner instruction)
**Rule (AGENTS.md §11 Phase 6):** the next feature is selected from observed
usage, not architecture preference.

## Starting point (2026-09-06, verified read-only)

- Database: 4 reels `status=completed`, 24 facts (1 user-corrected), 4 with
  normalized deadlines.
- Reels id 1–8 that remain are earlier-phase verification artifacts
  (migration/share-target/demo fixtures), not real saved content. The real-reel
  count for this window starts at 0.
- Baseline extraction quality (`docs/EVAL.md`, golden set v1):
  category accuracy 0.846, macro-F1 0.680, field recall 0.824,
  deadline recall 0.75, malformed-JSON rate 0.0, unsupported facts kept 0.178.
- Live smoke test 2026-09-06: upload → queue → transcription/OCR → extraction
  → PATCH summary/deadline → 422 on unparseable deadline → manual fact add →
  DELETE reel with media purge. All passed. One transient DELETE 401 during the
  first smoke pass did not reproduce (same token succeeded on retry); treated
  as an invocation issue, not a code defect.

## Metrics to fill during the window

| Metric | How to measure | Value at end |
|---|---|---|
| Real reels processed | completed reels added from real usage | **21** (final; window closed day 3) |
| Search hits that recovered real content | each time search found something you needed | 10/10 agent-verified queries hit correct reels; owner-driven organic searches: not exercised |
| Search misses on content you knew existed | note each miss | 0 observed |
| Evidence quotes inspected before trusting a fact | manual count | all 88 verified programmatically (agent); owner-driven inspection pending |
| Facts corrected or deleted | `user_corrected=1` count + deletions | 0 (owner corrections not yet needed; UI controls smoke-tested Phase 5) |
| Upload vs URL ingestion split | reels imported by file vs URL | 0 upload / 21 URL (22/23 submissions succeeded; 1 duplicate-guard no-op) |
| Top 3 recurring failures | failure log below | prompt-example parroting (FIXED), deadline extraction recall, zero-fact low-signal reels |
| Honest time saved vs scrolling saved content | weekly estimate | unknown at day 3 — cannot honestly estimate yet |

## Failure log (append as they happen)

| Date | Reel | What failed | Detail |
|---|---|---|---|
| 2026-09-09 | 12 | Deadline not extracted | Summary says "apply by September 15"; 0 facts, 0 deadlines produced — root-caused 2026-09-09: prompt few-shot example leak (model parroted Zylker example) + short-value evidence floor; both fixed, reel reprocessed as id 22 with 4 evidence-backed facts |
| 2026-09-09 | 1-21 (all) | Facts lacked timestamps in reports | AGENT REPORTING ERROR: 63/84 facts do carry evidence_t_s (OCR 22/22, transcript 41/46, caption 0/16 — captions legitimately untimed); earlier report scripts read a nonexistent field |
| 2026-09-09 | 21 | Zero facts despite content | Hiring-tips talking-head reel: 0 facts, conf 0.35; also reel 12 miscategorized (Tutorial/Product, is job content) |

## Phase 6 deliverable questions (answer at the end)

1. Did search recover content that would otherwise be lost?
2. Did evidence make facts trustworthy?
3. Was upload or URL ingestion the practical path?
4. What failed most often?
5. What single next feature would save the most time?

## Answers (window closed early at owner instruction, day 3)

**1. Did search recover content that would otherwise be lost?**
Yes — mechanism proven: 10/10 verification queries hit the correct reels,
including semantic matches ("data analysts Bangalore" → job summary),
content living only in OCR/transcript, and summary-level recovery on reels
with zero facts. Caveat: all queries were agent-run; the owner did not drive
organic searches in the 3-day window, so daily-reliance value is unproven.

**2. Did evidence make facts trustworthy?**
Yes, structurally. 88/88 kept facts carry verbatim source quotes, and the
ledger demonstrably rejected fabrication: 8 hallucinated facts (Zylker
prompt-leak case) were dropped across two runs of the same reel. Known gaps:
reel summaries are NOT evidence-checked (the one leak path), and weak-but-
anchored facts pass the floor (e.g. "location=United States" from OCR noise).
Confidence scores behaved honestly after the A3 redistribution.

**3. Was upload or URL ingestion the practical path?**
URL — 22/23 submissions completed end-to-end with zero download failures
(one duplicate-guard no-op). Upload was never needed. Caveat: URL reliability
depends on the yt-dlp path surviving Instagram changes; upload remains the
designed fallback and is untested in this window.

**4. What failed most often?**
(a) Extraction fidelity on low-signal reels: the prompt-example parroting
bug (root-caused and fixed Sep 9), 2 zero-fact reels (meme; talking-head
advice), 1 miscategorization (job reel → Tutorial/Product).
(b) Deadline extraction: only 1 deadline fact captured across 21 real reels,
and it carries no parseable date — the v0.1 promise most underdelivered.
(c) Observability: raw model output is not logged on fact-drop, which slowed
root-cause work (only dropped values are recorded, not the quotes).

**5. What single next feature would save the most time?**
Extraction/evidence quality (§11 Phase 7 row "Facts are inaccurate"): grow
the golden set with real-window failures (deadline-bearing reels, low-signal
talking-heads) and harden extraction + evidence thresholds accordingly.
Rationale: search (10/10) and ingestion (22/23) both measure as working; the
bottleneck is how much trustworthy structured knowledge each reel yields —
especially deadlines, where real-content recall is currently ~0.

## Decision at the end

- Chosen next feature (from AGENTS.md §11 Phase 7 table): **"Facts are
  inaccurate → Expand golden set and improve extraction/evidence"**
- Evidence that drove the choice: search and ingestion measured as working
  (10/10 search hits; 22/23 URL ingest); extraction quality is the observed
  bottleneck — 8 hallucinated facts from prompt leak (fixed but showed the
  failure mode), 0 parseable deadlines captured from 21 real reels,
  2 zero-fact reels, 1 miscategorization, weak-but-anchored facts passing
  the evidence floor.
- CONFLICT FLAGGED: AGENTS.md §11 pre-marks "Best-effort ingest reliability"
  as the Phase 7 selection (owner pain recorded before the window opened).
  Measured window data contradicts that premise (URL ingest 22/23). Per the
  Phase 6 rule — "the next feature is selected from observed usage, not
  architecture preference" — this retrospective recommends the
  extraction/evidence row. The AGENTS.md pre-mark should be updated only on
  owner confirmation.
