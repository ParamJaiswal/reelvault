# Decisions log
1. SQLite WAL over Postgres: personal-scale, zero-ops; schema portable.
2. No saved-Reels scraping: API doesn't expose it; share/paste UX instead.
3. Evidence Ledger as separate facts table w/ quote+ts+source per fact.
4. Drop facts below 0.45 evidence similarity (anti-hallucination).
5. Per-op queue connections (thread-safe) instead of shared conn.
6. AutoTranscriber permanent-fallback pattern for hostile DLL environments.
7. Vanilla JS PWA over React: single artifact, instant load, share-target first.
8. FTS5 external-content table w/ triggers (migration v2).
9. Idempotent stages: delete-then-insert derived rows on re-run.
10. Metadata-only terminal state when media unfetchable (never crash-loop).
11. Phase 7 selection: best-effort URL ingest reliability (owner pain: importing real reels by URL). First step is reproducing with a real owner-provided reel URL, then fixing narrowly by observed failure mode.
