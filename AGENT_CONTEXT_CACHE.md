# ReelVault — Compact Context Cache (Agent Coordination)
# Created: 2026-09-19 | Main agent: v0-core branch | Phase: 7 close / 6 prep

## State (verified from SESSION_HANDOFF.md + EVAL.md + git)
- Branch: v0-core @ 5e36bf4 ("docs: deterministic platform-hint path")
- Uncommitted: router.py temperature=0 (now committed)
- Phases 0-5: DONE | Phase 6: SETUP ONLY (0 real reels) | Phase 7: LIVE (URL ingest fixed, real URL verified)
- Golden: 16 items | Latest metrics: cat 0.769-0.882, recall 0.882, unsupported 0.085, malformed 0.0
- Accepted limits: edu-05 LinkedIn 0/2 (3B ceiling), schema drift generic, content presence 0.667

## Sub-agent assignments (worktree branches exist: phase4-agentic, phase5-agentic, phase6-agentic)
- Agent 1 (bug fixes): A1 (SequenceMatcher perf), A4 (reminder_for), B5 (Whisper filter), D1-D4 (tests)
- Agent 2 (pipeline/perf): B6 (OCR pHash), C1 (timing), C2 (WAL), C3 (shutdown), A2 (dup memory guard)
- Main (this agent): Phase 7 deterministic recovery, Phase 6 real-URL testing, commit/merge, final report

## Critical rules (do NOT violate)
- No Ollama, no queue replacement, no Auth v2 removal, no model change
- Every prompt/evidence change needs before/after eval (AGENTS.md §9)
- Temperature=0 committed; evidence floor 0.45 preserved; no threshold relaxation
- Source quotes ≠ proof of claim; entity guard (602e54a) stays

## Next 3 actions (ordered, minimal)
1. Commit/verify temperature=0 + A8 fix (done)
2. Implement deterministic platform recovery (SKILL_HINTS in stages.py) — Phase 7 remaining
3. Process first real reel URL (user will provide) — Phase 6 start

## Model / hardware
- Qwen2.5-3B-Instruct Q4_K_M (~2.1GB VRAM) via llama.cpp @ 8091
- RTX 3050 10GB — sequential stages safe; concurrent = OOM risk (documented, not changed)
- Alternative low-RAM models considered: Qwen2.5-1.5B (~0.9GB) — not needed for v0.1

## Open questions for user
- When to provide real reel URLs for Phase 6?
- Confirm sub-agent branches are current (phase4/5/6-agentic)?
- Accept Phase 7 limitation (edu-05) and proceed to Phase 6?
