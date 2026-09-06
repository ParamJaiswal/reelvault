## What I understand from the second plan

The second plan is saying:

> **Do not rebuild ReelVault. Do not delete working code. Keep the existing architecture, but stop expanding it until the core intelligence loop is proven useful.**

More specifically, it understands that your project already has substantial real work:

- Working FastAPI app
- SQLite schema and durable queue
- Auth/session system
- Existing llama.cpp + Qwen setup
- Pipeline stages
- Evidence Ledger
- Search
- Tests
- Mobile/Docker/push code partly completed

So the recommendation is **not** “start from scratch with a simpler app.”

It is:

1. Keep what works.
2. Fix known bugs.
3. Freeze unfinished peripheral features.
4. Validate the most important part: extraction quality + evidence grounding + search usefulness.
5. Only then deploy Docker, Android, phone push, etc.

That is the main meaning.

---

## The real product problem

Your project is currently trying to be four products at once:

1. **Personal Reel archive**
   - Save/import videos and preserve them locally.

2. **AI knowledge extractor**
   - Transcript, OCR, facts, dates, summaries, structured data.

3. **Opportunity tracker**
   - Jobs, scholarships, deadlines, urgency alerts, reminders.

4. **Cross-device platform**
   - PWA, Android app, HTTPS, Docker, authentication, push.

All four are useful, but a solo builder should not develop all four equally.

The product should have one sentence:

> “ReelVault lets me turn useful short videos into searchable notes with proof.”

Everything else supports that sentence.

If a feature does not improve **capture**, **trust**, or **retrieval**, it is not urgent.

---

## What I think you should change overall

### 1. Add an explicit product scope document

Your current documentation is architecture-heavy, but you need a short product decision document.

Create:

```text
docs/V0_SCOPE.md
```

It should say:

```text
V0 user:
One person who saves useful Instagram Reels and cannot find them later.

V0 promise:
Upload or import a reel and later find useful information with an evidence quote.

V0 success:
A user imports 20 reels and successfully finds at least 5 useful items later,
without manually watching every reel again.

In scope:
- Video upload
- Best-effort reel URL import
- Transcript
- OCR
- Evidence-backed facts
- Deadline extraction
- FTS search
- Reel detail page and manual edits

Out of scope:
- Android release
- Docker distribution
- Push notifications
- Custom SLM training
- Multi-user product
- Bulk scraping
- Semantic/vector search unless FTS demonstrably fails
```

This will stop scope creep.

---

### 2. Insert a new phase before Docker, Android, and push

This is the biggest change.

Your existing plan should become:

```text
Phase 0 — Fix correctness bugs
Phase 1 — Security + configuration tightening
Phase 1.5 — Prove extraction and search quality
Phase 2 — Real-device / Docker / Android validation
Phase 3 — Decide custom SLM
Phase 4 — Polish and release
```

Your missing **Phase 1.5** should include:

- Pick 10 real reels.
- Include different types:
  - recipes,
  - jobs/deadlines,
  - fitness,
  - educational reels,
  - noisy music-heavy reels,
  - Hinglish/mixed-language content if relevant.
- Label the facts and deadlines manually.
- Run the full pipeline.
- Record results.
- Fix only the biggest failures.

Without this phase, you can improve infrastructure for weeks without knowing whether the AI output is good enough.

---

### 3. Treat the Evidence Ledger as the main feature

This is your strongest and most differentiated design choice.

Most local “video-to-notes” tools can summarize a transcript. ReelVault can say:

> “Here is the fact, here is the exact quote, and here is where it appeared.”

Make the UI show evidence clearly.

For every extracted fact, show:

- Claim
- Verbatim source quote
- Source type: transcript / OCR / caption
- Timestamp, when available
- Confidence / evidence-match score
- A button to jump the video to that point
- A delete or correction button

Do not hide the evidence in a technical metadata area. Make it the center of the detail page.

---

### 4. Add manual correction early

This is a change I strongly recommend.

Local 3B models, Whisper, and OCR will make mistakes. You should not try to solve every error with another prompt.

Add simple user actions:

- Edit summary
- Change category
- Delete an incorrect fact
- Edit a claim
- Add a missing fact manually
- Add or correct a deadline
- Mark a reel as important/favorite
- Mark extraction as “wrong” or “needs review”

This makes ReelVault useful even when automation is imperfect.

It also creates future evaluation/training data if you ever decide to improve the model.

---

### 5. Make upload the reliable ingestion path; URL import is best effort

Do not define success as “Instagram URL always downloads.”

Instagram can change behavior, throttle, login-wall content, or break yt-dlp. That is outside your control.

Your UX should communicate this honestly:

| Input method | Product status |
|---|---|
| Local file upload | Supported and reliable |
| Reel URL import | Best effort |
| Login-walled/private content | Unsupported or metadata-only |
| Bulk account/saved-reel imports | Not a v0 feature |

A good error should say:

> “We could not download this Reel. Instagram may require login or may have blocked automated download. You can upload the video file manually instead.”

That is much better than a mysterious failed job.

---

### 6. Do not replace your current LLM runtime

The second plan correctly says this.

You already have llama.cpp, Qwen GGUF, auto-start management, configuration, and likely code that speaks to OpenAI-compatible endpoints.

So:

- Keep `llama-server`.
- Keep Qwen2.5 3B for now.
- Keep the Business SLM disabled.
- Do not add Ollama unless llama.cpp becomes an actual blocker.

Changing runtimes is not product progress.

---

### 7. Make structured output stricter

Your extraction schema should remain small.

Use only:

```json
{
  "category": "job|recipe|fitness|finance|education|other",
  "summary": "string",
  "facts": [
    {
      "claim": "string",
      "quote": "string",
      "t_s": 12.4
    }
  ],
  "deadlines": [
    {
      "what": "string",
      "date_text": "string",
      "quote": "string"
    }
  ]
}
```

Important changes:

- `quote` must be a direct source quote, not model paraphrase.
- `t_s` can be `null`; do not invent timestamps.
- `date_text` should preserve what the source said.
- Deterministic code—not the model—should parse and normalize dates.
- Do not add ten categories and twenty extraction fields yet.

A small schema is much more reliable with Qwen 3B.

---

### 8. Create an evaluation harness before prompt work

This is non-negotiable if you want to call this an AI project rather than a demo.

Start with only 10 manually labeled reels.

Track:

| Metric | What it tells you |
|---|---|
| Category accuracy | Is the classifier broadly useful? |
| Fact precision | Are extracted facts mostly correct? |
| Fact recall | Does it find the important things? |
| Evidence failure rate | Are claims actually grounded? |
| Deadline accuracy | Does the system understand dates? |
| JSON failure rate | Is the model reliably producing usable output? |
| Processing duration | Is the pipeline usable on your hardware? |

Save a baseline in `docs/EVAL.md`.

Then make this rule:

> No extraction prompt, model, OCR, or evidence matcher change is accepted unless evaluation is run before and after.

---

### 9. Keep the durable queue and auth, but stop enhancing them

You do **not** need to simplify working infrastructure merely because the product is early.

Keep:

- Durable queue
- Existing auth
- SQLite WAL
- Existing backup work
- Existing logging/redaction

But do not expand them unless a real issue appears.

For example:

| Keep | Do not do now |
|---|---|
| Queue race fix | Rewrite queue |
| Bootstrap crash fix | Add more job priorities |
| Existing roles | Build invitation system |
| Existing token rotation | Add social login |
| Existing backup code | Build cloud sync |
| Existing PWA | Add complex push campaigns |

This is the difference between *preserving investment* and *continuing scope creep*.

---

### 10. Change your definition of “done”

Your current definition of done is technically ambitious:

- Docker pipeline end-to-end
- Android app installed and working
- Push/HTTPS verified
- SLM decision executed
- v1.0 tagged

That is fine for a **v1 platform release**, but too large for your first success milestone.

Define two milestones.

## v0.1 — Useful personal prototype

```text
- Upload video works
- Processing completes or fails cleanly
- Transcript + OCR stored locally
- Facts have evidence quotes
- Deadlines are deterministically parsed
- Search finds imported content
- User can correct/delete bad extraction
- Backup restore tested once
- 20 real reels processed
- Evaluation baseline exists
```

## v1.0 — Portable personal platform

```text
- Docker build verified
- HTTPS/Tailscale phone workflow verified
- PWA share target works
- Android intake works, if still needed
- Retention works
- Backup restore documented
- URL ingestion failure UX is clean
- README/deployment docs are current
```

Notice: Android should be conditional. If the PWA share target works well, you may not need a dedicated Android app at all.

---

## What I would postpone indefinitely

Be strict here.

### Custom Business SLM

Pick Road A:

- Keep `RV_SLM_ENABLED=false`.
- Do not train it.
- Do not improve it.
- Do not spend time making the routing smarter.
- Consider removing it only after v1.0 if it adds maintenance cost.

A 5.3M model is not a serious extraction model without substantial data, training, and evaluation. Qwen is the practical solution.

### Docker, until the local prototype proves value

Docker is useful if:

- you need reproducible deployment,
- another machine needs to run it,
- you want a portfolio demonstration,
- you want to move off Windows.

It is not useful simply because “real apps use Docker.”

### Android, until the browser workflow hurts

If phone workflow is the goal, first test:

```text
Phone → Tailscale HTTPS → PWA → share target / upload
```

If that feels bad in actual use, then build the Android app. Otherwise, Android is duplicate UI and build-maintenance work.

### Push notifications, until deadlines are trustworthy

A wrong reminder is worse than no reminder.

First verify:

- date extraction,
- timezone assumptions,
- past-date handling,
- duplicate reminders,
- real phone delivery.

Then add push.

---

## Final recommended order

```text
1. Run existing tests and make a v0-core branch
2. Complete your current Phase 0 review-bug fixes
3. Complete minimal Phase 1 security/config fixes
4. Build a 10-reel golden evaluation dataset
5. Improve extraction + Evidence Ledger based on measured failures
6. Add manual fact/deadline correction
7. Test search using 20 real reels
8. Test backup restore + retention
9. Release v0.1 for yourself
10. Use it for two weeks
11. Decide whether Docker, PWA share, Android, and push solve real pain
12. Build only the next feature that solves the biggest observed pain
```

## My honest advice

You have already done enough architecture. Your next achievement should not be another subsystem.

It should be this experience:

1. You see a useful Reel.
2. You upload it.
3. ReelVault extracts a useful fact with an exact supporting quote.
4. Two weeks later, you search a phrase.
5. You find the Reel and jump to the evidence in seconds.

If that works reliably for you, the project is worth finishing.

If that does **not** feel useful after 20–30 real reels, do not build Docker, Android, or custom models. Re-scope it—or stop.
## I read it. Honest review

The new text is **useful as a product critique**, but it should **not be copied into `AGENTS.md` as the implementation plan**.

It contains good instincts, but also dangerous instructions that conflict with your real repo and would cause a small agent to waste or destroy work.

Also, your existing guidance says current state is **August 26, 2026**. Today is **September 6, 2026**, so that status is already stale. An agent must verify it, not trust it.

---

## What the new plan correctly understands

These points are correct and should influence your project:

- The core value is not Android, Docker, auth, or push.
- The core value is:

  > Reel/video → transcript/OCR → evidence-backed facts → searchable retrieval.

- The custom 5.3M Business SLM is not ready for production. Keep it disabled.
- You need an extraction evaluation dataset before prompt/model changes.
- Upload is more reliable than Instagram URL ingestion.
- yt-dlp and Instagram are external fragile dependencies; URL ingestion must fail safely.
- FTS5 is enough before adding embeddings/vector search.
- You should use the system with real reels before expanding scope.
- Evidence quotes and timestamps are your key differentiator.
- Manual review/correction matters because local STT/OCR/LLMs will make errors.

These are all strong product decisions.

---

## What is wrong or risky in the new plan

### 1. Do not “move kill-list dirs to `_archive/`”

This is the biggest bad instruction.

The plan says to archive/remove:

- Android
- Docker
- Push
- Playwright
- Auth v2
- Encrypted backup
- Custom SLM
- Queue infrastructure

That is unnecessary and risky.

Your repo already has:

- 40 tests claimed passing,
- existing imports and configuration,
- a database schema,
- mobile API endpoints,
- deployment work,
- a durable queue.

Moving code can break:

- imports,
- tests,
- migrations,
- deployment docs,
- endpoint registration,
- configuration loading.

**Correct action:** do not delete or archive. Mark these components as **frozen / out of scope for v0.1**.

Example:

```text
Frozen for v0.1:
- android/
- Dockerfile / WSL Docker work
- app/core/push.py
- app/pipeline/ig_browser.py
- app/ai/slm_provider.py
- vendor/slm/
```

Frozen means:

- Do not add features.
- Do not rewrite.
- Do not remove unless it blocks the core path.
- Existing tests should still remain green.

---

### 2. Do not replace llama.cpp with Ollama now

The proposed plan tells you to install Ollama and remove `llama_manager.py`.

That is not the right move for *your* project.

You already have:

```text
llamacpp/llama-server.exe
models/qwen2.5-3b-instruct-q4_k_m.gguf
app/core/llama_manager.py
RV_LLM_SERVER_URL=http://127.0.0.1:8091/v1
```

Adding Ollama creates:

- a second model runtime,
- a second model copy/download,
- new startup logic,
- different structured-output behavior,
- another thing for agents to configure incorrectly.

**Correct action:** retain llama.cpp and Qwen for v0.1. Do not switch inference runtimes unless your existing server is actually broken.

---

### 3. Do not replace your durable queue with a thread/status column

The new plan says to simplify the queue.

That would throw away useful completed engineering:

- retries,
- backoff,
- dead-letter queue,
- heartbeats,
- durability,
- tests.

You already identified one queue race. Fix that one line and test it. Then leave the queue architecture alone.

**Correct action:**

```text
Keep existing durable queue.
Fix claim race.
Do not rewrite queue for v0.1.
Use its existing status/error reporting in the UI.
```

---

### 4. Do not remove Auth v2

The new plan says “single user, one password + token enough.”

That is technically true for a tiny prototype—but you have already built Auth v2 and say it is verified.

Removing it gives you little benefit and creates regression risk.

**Correct action:**

- Keep auth.
- Fix the empty-user/bootstrap crash.
- Restrict CORS.
- Warn about unchanged generated owner credentials.
- Do not enhance the auth system further until you have another user.

---

### 5. The phase order is internally wrong

It inserts “Phase 1.5 — Prove intelligence layer” **before** the transcription/OCR/extraction phases that it later defines.

You cannot fully evaluate extraction quality until the transcript, OCR, evidence, extraction, and search loop are working.

The correct ordering is:

```text
Baseline → correctness fixes → upload pipeline →
transcription/OCR → extraction/evidence →
golden evaluation → search/UI → real personal use →
Docker/mobile/push only if justified
```

---

### 6. `no_speech_prob` may not match your selected transcription runtime

The plan says:

```text
drop segments where no_speech_prob > 0.6
```

That depends on the exact Whisper implementation and data you expose. If you switch to `faster-whisper`, a better starting point is voice activity detection such as:

```python
vad_filter=True
```

Do not tell an agent to implement a field-based threshold until it verifies that field exists in the provider’s returned segment object.

**Correct agent rule:**

> Use the existing STT provider first. If changing to faster-whisper, implement VAD and write a provider-level test based on real returned segment fields.

---

### 7. “Every function ≤ 40 lines” is too rigid

This rule sounds clean but is not useful for a real pipeline.

Forcing every function under 40 lines can create:

- too many tiny helper functions,
- lost readability,
- needless cross-file jumping,
- artificial refactoring.

Use a better rule:

```text
Keep functions focused.
Split a function when it mixes more than one responsibility,
becomes hard to test, or exceeds roughly 80–100 lines.
```

---

### 8. “Touch only files phase names” is too restrictive

A good change can legitimately require:

- production file,
- schema migration,
- unit test,
- integration test,
- config field,
- docs/handoff update.

Do not constrain agents so tightly that they omit tests or migrations.

Use:

```text
Touch only files necessary for the scoped change.
If a schema changes, add a migration and schema-version bump.
Every behavior change requires relevant tests.
```

---

## Important contradictions in your current `AGENTS.md`

Your original guidance and the new plan disagree in important ways.

| Existing guidance | New plan | Correct decision |
|---|---|---|
| All paths must be absolute | Remove all `D:/` paths | Make paths config-driven; preserve Windows defaults |
| Existing llama.cpp server | Install Ollama and kill manager | Keep llama.cpp for v0.1 |
| Durable queue exists | Replace with simple thread | Keep durable queue |
| Auth v2 is live | Replace with simple token | Keep auth; freeze feature work |
| Docker build in progress | Stop Docker | Freeze Docker until core quality is proven |
| Android code complete | Archive Android | Freeze Android, do not delete |
| Push is live | Replace with dashboard badge | Freeze push, do not delete |
| Playwright fallback env-gated | Drop it | Keep gated but do not test/build further now |
| Current state: Aug 26 | New text assumes current facts | Verify status on September 6, 2026 |

You need to remove these contradictions before another agent works on the repo.

---

## The best merged strategy

Use this as your real direction:

> **Preserve the existing architecture. Fix correctness issues. Freeze peripheral features. Prove grounded extraction and retrieval on real data. Then deploy only the feature that solves an observed pain.**

That is neither “continue blindly” nor “restart lean.”

It is a **focused stabilization and validation plan**.

---

## Recommended v0.1 plan

### Phase 0 — Verify reality before touching code

Because the status is dated **August 26, 2026**, first verify it on **September 6, 2026**.

Run:

```powershell
cd D:\reelvault
.venv\Scripts\python.exe -m pytest tests\ -q --ignore=tests/test_ai_eval.py
curl http://127.0.0.1:8756/healthz
wsl.exe -d Ubuntu -u root -- bash -lc 'tail -20 /mnt/d/reelvault/data/docker_build3.log 2>/dev/null'
```

Record actual results in:

```text
docs/SESSION_HANDOFF.md
```

**Done:** you know what is truly working, broken, or incomplete.

---

### Phase 1 — Fix your original Phase 0 bugs

Do exactly the four original fixes:

1. Evidence quote minimum length.
2. Correct confidence when timestamp is missing.
3. Empty-users/bootstrap-token guard.
4. Queue claim heartbeat re-check.
5. Tests for all four.

**Done:** tests pass; no behavior is changed outside those fixes.

---

### Phase 2 — Configuration portability and security minimum

Do not force every path to be relative. Make them configurable.

Desired behavior:

```text
Windows local default:
D:\reelvault\models
D:\reelvault\media

Alternative environments:
RV_MODELS_DIR=/models
RV_MEDIA_DIR=/media
RV_DB_PATH=/data/reelvault.db
```

Add:

- clear startup checks,
- limited CORS,
- owner credential warning,
- no hardcoded path assumptions outside config defaults.

**Done:** app can start under an alternate configured test directory.

---

### Phase 3 — Core pipeline proof

Use the existing upload endpoint and existing durable queue.

For five local video files:

1. Upload video.
2. Confirm stored reel row and queue job.
3. Confirm media extraction.
4. Confirm transcript and OCR artifact storage.
5. Confirm structured facts are generated.
6. Confirm invalid claims are dropped by Evidence Ledger.
7. Confirm search can find the reel afterward.

**Done:** five videos process end-to-end or fail cleanly without killing the worker.

---

### Phase 4 — Golden extraction evaluation

Create a small dataset before changing prompts or model providers.

Start with **10 reels**, then grow to 20.

Include:

- one recipe,
- one workout,
- one job/deadline reel,
- one educational reel,
- one OCR-heavy reel,
- one music/noise-heavy reel,
- Hinglish or mixed-language content if relevant.

For each one, hand-label:

```json
{
  "expected_category": "education",
  "key_facts": [
    "..."
  ],
  "deadlines": [
    {
      "what": "...",
      "date_text": "..."
    }
  ]
}
```

Track:

- category accuracy,
- fact precision,
- fact recall,
- evidence failure rate,
- deadline accuracy,
- JSON/schema failure rate,
- processing time.

**Done:** baseline metrics live in `docs/EVAL.md`.

---

### Phase 5 — Improve the user-facing proof

The Reel detail page should make evidence visible.

For each fact show:

- claim,
- source quote,
- source type,
- timestamp,
- click-to-seek video action,
- delete/edit action.

Add manual corrections:

- edit summary,
- change category,
- delete false fact,
- add a fact,
- correct/add deadline.

**Done:** bad AI output can be corrected without changing prompts or rerunning the pipeline.

---

### Phase 6 — Personal-use validation

Use ReelVault for **two weeks** and import at least **20 real reels**.

Answer these questions honestly:

1. Did you find a Reel you would otherwise have lost?
2. Did search save time versus scrolling?
3. Were evidence quotes trustworthy?
4. Did deadline extraction help?
5. Was phone ingestion actually the main pain?
6. Did FTS search fail on useful queries?
7. Was CPU transcription too slow in practice?
8. Did URL ingestion matter more than manual upload?

**Done:** create a short `docs/V0_RETROSPECTIVE.md`.

---

### Phase 7 — Select exactly one expansion

Only after Phase 6, choose the largest real pain:

| Observed pain | Next feature |
|---|---|
| Phone upload is annoying | PWA share target / Tailscale HTTPS |
| Need installation on another PC | Docker |
| Need native share sheet/offline mobile UX | Android build |
| Search misses meaning/paraphrases | vector embeddings |
| Deadline reminders are useful and dates reliable | web push |
| Jobs repeatedly fail | queue hardening/monitoring |
| URL import is essential | improve best-effort yt-dlp workflow |
| Extraction is poor | grow eval set and improve evidence/prompting |

Do **one**. Not all.

---

## What to change in `AGENTS.md`

Your current file should be split into three documents:

```text
AGENTS.md
docs/ARCHITECTURE.md
docs/V0_PLAN.md
```

### `AGENTS.md` should contain only

- How to start the app.
- How to run tests.
- File/folder conventions.
- Configuration rules.
- Secrets/logging rules.
- Migration rules.
- The current active phase.
- A small “frozen scope” list.

It should not contain several long competing plans.

### `docs/ARCHITECTURE.md` should contain

- Current project layout.
- Auth/queue/push/backups design rationale.
- Existing API surface.
- Deployment details.
- Component status.

### `docs/V0_PLAN.md` should contain

- The phased roadmap.
- Done checks.
- Scope exclusions.
- Evaluation requirements.
- Decision criteria for post-v0 features.

---

## Suggested replacement for the conflicting agent direction

Add this to `AGENTS.md`:

```text
## v0.1 Product Direction

ReelVault v0.1 is a single-user, local-first prototype whose goal is:
“Import a short video, extract evidence-backed notes, and find them later.”

Active priorities:
1. Correctness of evidence grounding.
2. Extraction quality measured by a golden evaluation set.
3. Reliable local upload processing.
4. FTS retrieval and evidence-visible UI.
5. Personal use with at least 20 real reels.

Frozen until v0.1 validation:
- Android build/release work
- Docker work
- Web Push expansion
- Playwright/Instagram login fallback work
- Custom Business SLM work and training
- New auth features
- Vector-search expansion

Do not delete frozen components. Do not replace working llama.cpp,
auth, queue, or backup systems unless a scoped issue requires it.
```

---

## Final answer

The “Build, but not this project” text is **right about priorities** but **wrong about deleting/replacing your working systems**.

Keep your real project. Do not restart it. Do not archive major folders. Do not switch to Ollama. Do not replace the queue or auth.

Make these changes:

1. Add a clear v0.1 scope.
2. Fix the four review bugs.
3. Add a golden extraction evaluation phase before deployment/mobile work.
4. Make upload the guaranteed path and URL intake best effort.
5. Add manual fact/deadline correction.
6. Freeze—not remove—Android, Docker, push, Playwright, and Business SLM.
7. Use the product with 20 real reels before deciding what to build next.

That gives you a realistic path to a genuinely useful prototype while preserving the good engineering work you have already completed.
