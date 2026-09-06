# ReelVault — All Challenges

Honest list. What can bite, where, how bad.

---

## 1. Ingestion (most fragile part)

- **yt-dlp breaks often.** Instagram changes HTML/API. Fix = update yt-dlp, sometimes wait for upstream. Forever maintenance.
- **Rate limits / IP blocks.** Many reels fast = temp block. Need throttle, delay, maybe proxy. None today.
- **Login-walled reels.** Anonymous fetch fails. Playwright fallback = disposable account. Risks: captcha, ban, IG scraping ToS. Account dies → feature dead.
- **Compliance line.** No saved-posts API exists. Adapter interface right, but every adapter is fragile.

## 2. AI layer

- **Whisper runs CPU only** (venv torch no CUDA). Slow. Fix = reinstall torch CUDA, or accept `base` model. Quality/speed tradeoff.
- **VRAM squeeze.** Qwen 3B + Whisper + OCR same box. 4-8GB VRAM = OOM risk. Needs sequencing.
- **Whisper hallucination** on music/noise segments. Invented text → fake evidence → bad facts.
- **Hinglish / mixed language.** Transcript + OCR messy. Extraction quality drops. Hard to fix fully.
- **OCR misses.** Stylized fonts, low-res frames, text on motion. Frames sampled every 1.5s — can skip fast text.
- **Qwen extraction drift.** 3B model: wrong JSON, missed facts, prompt tweaks change everything. No eval set = cannot measure regressions. **Biggest AI gap: no golden test set for extraction.**
- **Evidence matcher tension.** Strict = paraphrased facts dropped (false negatives). Loose = hallucinations pass. Current substring hole = too loose. Tuning never ends.
- **SLM fork.** 5.3M model undertrained. Road B (train) needs dataset (thousands of labeled reels — you don't have), eval harness, GPU time. High chance of wasted weeks.

## 3. Storage / data

- **SQLite contention.** Server + worker + backups + push scanner on one DB. WAL helps but `database is locked` will appear under load.
- **Disk growth.** video/audio/frames per reel pile up. Retention job unverified. Disk full = everything stops.
- **Backups restore untested.** Snapshot tested, restore-to-fresh-dir never done. Backup that never restored = hope, not backup.
- **Queue stuck jobs.** Heartbeat reclaim exists, but poison job looping retries = watch DLQ. No alerting.

## 4. Deploy / platform

- **Docker on Windows.** CUDA in container = WSL2 + nvidia toolkit + huge image. First build slow, path bugs certain (`D:/` hardcoded).
- **Hardcoded paths.** `D:/reelvault` in config/models. Container, other machines break. Must be env-driven.
- **Self-signed HTTPS friction.** Phone browser warnings, PWA install may refuse. Tailscale solves but = another tool + account.
- **LAN exposure.** `0.0.0.0` + CORS `*`. Auth covers, but bootstrap token never expires — file leak = permanent owner access.

## 5. Mobile / PWA

- **Android build env.** SDK, Gradle, JDK versions on Windows. Classic pain. APK signing for install.
- **Share-intake quirks.** Every Android OEM share sheet differs. Token storage via Keystore = device-specific bugs.
- **Push on LAN.** Web Push needs HTTPS, period. Plain HTTP = silent fail. Already known.

## 6. Long-term

- **Dependency drift.** yt-dlp, fastembed, torch, playwright — all move fast. Pin + periodic update sessions.
- **Docs rot.** AGENTS.md already says "Aug 26 2026". Stale docs mislead next session.
- **Bus factor 1.** Everything in your head + handoff docs. Keep them current.
- **Windows-only.** Whole stack tuned to one box. Any migration = retest everything.

---

## Top 5 by danger

1. **No extraction eval set** — you cannot see quality regressions. Fix before any model/prompt change.
2. **yt-dlp fragility** — core input dies without warning. Add fetch health check + alert.
3. **Untested backup restore** — data loss risk.
4. **Disk growth / retention unverified** — slow, silent, eventual full stop.
5. **SLM road B without dataset** — weeks of wasted work likely.

Rule: fragile external things (IG, yt-dlp, browser push) get monitoring. Local things get tests. Never trust model output — already built right, keep it.
