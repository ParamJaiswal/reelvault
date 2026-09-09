ReelVault — Multi-Agent Parallel Work Briefing
Purpose: Give this document to every agent working on the plan. Each agent gets its assigned group + the shared rules below.

Agent Assignments
🟢 Agent 1 — "Router & Extraction"
Items: A5, A6, B1, B3, B4
Files you OWN (exclusive write):

app/ai/router.py
app/knowledge/schemas.py
What to do:

A5: Fix _normalize_categories — after alias lookup fails, try case-insensitive match against VALID_CATEGORIES before dropping. Test with "Personal Advice", "Data Science", "Data Analytics".
A6: Delete VALID_CATEGORIES from router.py L26-30. Import from app.knowledge.schemas instead. Grep the entire codebase for other copies.
B1: Add 1 compact few-shot example to the extraction system prompt (L216-233). Must run golden eval before AND after.
B4: Modify extraction prompt to require "date_text" as verbatim source text, not normalized. Must run golden eval.
B3: Add "finance", "recipe", "fitness" entries to SCHEMA_ALIAS mapping to "generic". Optionally add new schema classes in schemas.py.
New test files you create: tests/test_category_normalization.py

🟢 Agent 2 — "Evidence & Deadlines"
Items: A1, A3, B2, A4
Files you OWN (exclusive write):

app/knowledge/evidence.py
app/knowledge/deadlines.py
What to do:

A1: Optimize _sim() — add a cheap Jaccard pre-filter before SequenceMatcher. Only call .ratio() on spans with ≥20% word overlap. Use stdlib only (no rapidfuzz unless owner approved it).
A3: When has_timestamp=False, redistribute the 0.25 weight: give 0.10 to match_sim (→0.45) and 0.15 to multi (→0.35). Run golden eval to verify no gated metric regresses.
B2: Add number word-to-digit normalization in norm() function. Implement a small lookup dict ({"one": "1", "two": "2", ... "thousand": "1000"}) — no new dependency needed. Apply to both quote and span before comparison.
A4: In reminder_for(), when computed reminder is in the past, return max(now + 1 hour, deadline - 1 day) instead of deadline_dt itself.
New test files you create: tests/test_evidence_optimization.py, tests/test_deadline_reminder.py

🟢 Agent 3 — "Test Coverage"
Items: D1, D2, D3, D4
Files you OWN (exclusive write):

tests/test_search_ranking.py (NEW)
tests/test_dup_check.py (NEW)
tests/test_deadline_parsing.py (NEW — separate from existing test_deadlines.py)
What to do:

D1: Test hybrid_search blend ordering — create 3 mock reels with known FTS ranks and embedding vectors, verify blend produces correct order.
D2: Test _normalize_categories with edge cases: "Personal Advice", "Data Science", comma-separated "AI/ML, Career", garbage input, empty list.
D3: Test semantic_duplicate_check — short summary (<40 chars) returns None; long identical summary returns dup id.
D4: Test parse_deadline("2026-09-15") returns Sept 15 not day/month swapped. Test parse_deadline("September 15"), parse_deadline("15th September").
IMPORTANT: You ONLY create new test files. Do NOT modify any existing source files. If a test reveals a bug, document it in the test docstring — don't fix it.

🟡 Agent 4 — "Pipeline & Reliability" (runs AFTER agents 1-3 merge)
Items: A8, B5, B6, C1, C2, C3
Files you OWN (exclusive write):

app/pipeline/stages.py
app/pipeline/media.py
app/db/queue.py
app/api/main.py
Why sequential: All these items touch stages.py. Running this agent in parallel with others editing stages.py will cause merge conflicts.

Shared Rules — EVERY Agent Must Follow These
🔴 Rule 1: Read AGENTS.md First
Every agent MUST read D:\reelvault\AGENTS.md before writing any code. It's the project constitution. Key constraints:

No new dependencies without owner approval
No changes to: SQLite WAL, durable queue, auth, llama.cpp runtime, logging, backups, schema/migrations
RV_SLM_ENABLED stays false
Temperature 0 for extraction
Evidence Ledger is non-negotiable
🔴 Rule 2: Never Edit Files You Don't Own
Your agent assignment lists exactly which files you can write to. If you need a change in another file, document it as a TODO and tell the coordinating agent. Two agents editing the same file = merge conflict = lost work.

🔴 Rule 3: Test Before and After
powershell

# Run BEFORE your first change
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_ai_eval.py
# Run AFTER every change
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_ai_eval.py
If any existing test breaks, STOP and fix before continuing. Don't leave broken tests for the merge.

🔴 Rule 4: Golden Eval for AI Changes (Agents 1 & 2 only)
Any change to prompts, evidence thresholds, or extraction schemas:

powershell

# Needs llama-server running on :8091
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_ai_eval.py -q -s
Record the before/after metrics. If any gated metric drops below its gate, revert.

🔴 Rule 5: Don't Touch These Files (Anyone)
app/core/auth.py — auth system is frozen
app/core/push.py — push is frozen
app/core/backups.py — just verified, don't break it
app/db/schema.py — no schema changes in this plan
app/static/* — no frontend changes in this plan
docs/SESSION_HANDOFF.md — only the coordinating agent updates this
🔴 Rule 6: Settings Mutation
Never do settings.some_field = value in production code. Use monkeypatch.setattr in tests only. Direct settings mutation leaks across tests (known bug A7).

Codebase Pitfalls (Copy to Every Agent)
These are traps that have already bitten previous sessions:

Pitfall	Detail
confidence_score rounding	0.665 rounds to 0.66, not 0.67. Don't assert 0.67.
Jobs table NOT NULL columns	created_at, updated_at, run_after are NOT NULL. Include them in test fixtures.
embeddings table required columns	Needs owner_id, text_used, dim, model — all NOT NULL.
test_pipeline.py leaks	It sets settings.retry_backoff_s = 0 globally. Your queue tests may get wrong backoff.
StageCancelled vs PermanentJobError	stage_media converts permanent media errors to StageCancelled. Expect StageCancelled at that boundary, not PermanentJobError.
Windows path separators	Use Path objects, not string concatenation. Forward slashes in config, backslashes in subprocess.
Console encoding	Use python -X utf8 if output has emoji (Whisper segments contain 🎵).
get_db() context manager	Always use with get_db() as db:. Never hold a connection across stages.
datetime.now() in tests	Mock it or use today= parameter where available. Tests run at different times.
Merge Order Protocol

Step 1: Agent 3 (Tests) merges first
         ↓  reason: tests only, zero risk, establishes new coverage
Step 2: Agent 1 (Router) and Agent 2 (Evidence) merge
         ↓  reason: no file overlap between them
         ↓  run full suite after each merge
Step 3: Run golden eval
         ↓  verify no gated metric regressed
Step 4: Agent 4 (Pipeline) starts work
         ↓  reason: needs the merged codebase as its base
Step 5: Final full suite + golden eval
After Each Merge
powershell

# Fast suite
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_ai_eval.py
# If AI changes were merged, also run:
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_ai_eval.py -q -s
What to Tell the Coordinating (Main) Agent
Give your main agent this checklist:

Pre-Flight
 Verify services are up: curl http://127.0.0.1:8756/healthz and curl http://127.0.0.1:8091/v1/models
 Run baseline test suite: expect 84 passed, 1 skipped
 Run baseline golden eval: record current metrics
 Confirm git is clean: git status on branch v0-core
 Create a checkpoint: git tag pre-parallel-work
During Parallel Work
 Each agent works on a separate git branch (e.g., agent1-router, agent2-evidence, agent3-tests)
 Main agent does NOT write code — only coordinates and merges
 If an agent needs a change outside their owned files, they raise it to main agent
Post-Merge
 All tests pass (should be 84 + new tests from D1-D4 + regression tests from each agent)
 Golden eval metrics meet or exceed baseline
 Update docs/SESSION_HANDOFF.md with what was done
 Update docs/EVAL.md if any metric changed
 Single commit per agent's work, then a merge commit
If Things Go Wrong
 Two agents edited the same file → use git diff to manually merge, test after
 A gated eval metric dropped → revert the responsible agent's changes, investigate
 Tests from Agent 3 fail after Agent 1/2 merge → the test caught a real bug, good! Fix in the responsible agent's branch

 Here's the summary of what to tell each agent:
 
 Key Things to Communicate
 To every agent:
 
 Read AGENTS.md first — it's the project constitution
 You can ONLY write to your assigned files — anything else = merge conflict
 Run the test suite before and after every change
 Never mutate settings.* directly in production code
 To your main/coordinating agent:
 
 Create a git tag pre-parallel-work before anyone starts
 Each agent gets its own branch (agent1-router, agent2-evidence, etc.)
 Merge order matters: Tests first → Router + Evidence (parallel) → Pipeline last
 You don't write code — you merge, run the full suite, and run golden eval after AI changes land
 The biggest risks if you skip this:
 
 Risk	What Happens	Prevention
 Two agents edit stages.py	Git conflict, manual resolution needed	Agent 4 waits for 1-3 to finish
 Agent changes a prompt without eval	Silent quality regression	Rule 4 — golden eval mandatory
 Agent imports from wrong VALID_CATEGORIES	A6 fix gets undone	Agent 1 owns both copies
 Tests leak settings	Flaky failures in other agent's tests	monkeypatch only, never direct mutation
 The briefing document has everything in copy-paste-ready format for each agent. Want me to also split it into separate per-agent files?
