# V0 Retrospective — Personal-Use Validation

**Status:** IN PROGRESS — do not answer the questions below until the owner has
processed >= 20 real reels/videos over at least two weeks of actual use.

**Usage window opened:** 2026-09-06
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
| Real reels processed | completed reels added from real usage | |
| Search hits that recovered real content | each time search found something you needed | |
| Search misses on content you knew existed | note each miss | |
| Evidence quotes inspected before trusting a fact | manual count | |
| Facts corrected or deleted | `user_corrected=1` count + deletions | |
| Upload vs URL ingestion split | reels imported by file vs URL | |
| Top 3 recurring failures | failure log below | |
| Honest time saved vs scrolling saved content | weekly estimate | |

## Failure log (append as they happen)

| Date | Reel | What failed | Detail |
|---|---|---|---|

## Phase 6 deliverable questions (answer at the end)

1. Did search recover content that would otherwise be lost?
2. Did evidence make facts trustworthy?
3. Was upload or URL ingestion the practical path?
4. What failed most often?
5. What single next feature would save the most time?

## Decision at the end

- Chosen next feature (from AGENTS.md §11 Phase 7 table): _
- Evidence that drove the choice: _
