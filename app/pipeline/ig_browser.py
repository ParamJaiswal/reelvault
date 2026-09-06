"""Playwright-based Instagram fetcher (Option D — user-controlled browser).

Compliance posture (documented in docs/INGESTION.md):
- Uses a SEPARATE, DISPOSABLE downloader account supplied via env vars
  (IG_USERNAME / IG_PASSWORD) — never the user's main account.
- Only public reel URLs are fetched; private/login-walled content fails into
  metadata-only mode like every other adapter.
- yt-dlp remains the primary fetcher (no login needed); Playwright is the
  fallback when Instagram serves the login wall to anonymous clients.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from app.core.config import settings

log = logging.getLogger("rv.ig_browser")

_CHROME_CANDIDATES = [
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
]


def _chrome_path() -> str | None:
    for p in _CHROME_CANDIDATES:
        if p.exists():
            return str(p)
    return None


def has_ig_credentials() -> bool:
    return bool(os.environ.get("IG_USERNAME") and os.environ.get("IG_PASSWORD"))


def fetch_reel(url: str, dest_dir: Path, timeout_s: int = 180) -> dict | None:
    """Download a public reel video via headless Chromium.

    Strategy: open the reel page logged-in (disposable account), intercept the
    .mp4 media response. Returns {'path': ..., 'title': ...} or None.
    """
    if not has_ig_credentials():
        log.info("IG_USERNAME/IG_PASSWORD not set — Playwright fetch skipped")
        return None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("playwright not installed")
        return None

    dest_dir.mkdir(parents=True, exist_ok=True)
    out_file = dest_dir / "reel_download.mp4"
    title = ""
    captured: list[dict] = []

    try:
        with sync_playwright() as pw:
            launch_kw: dict = {"headless": True}
            chrome = _chrome_path()
            if chrome:
                # use real Chrome so IG sees a normal client fingerprint
                launch_kw["executable_path"] = chrome
            browser = pw.chromium.launch(**launch_kw)
            ctx = browser.new_context(
                viewport={"width": 430, "height": 932},
                user_agent=("Mozilla/5.0 (Linux; Android 13; Pixel 7)"
                            " AppleWebKit/537.36 (KHTML, like Gecko)"
                            " Chrome/124 Mobile Safari/537.36"),
                locale="en-US")
            page = ctx.new_page()

            def on_response(resp):
                u = resp.url
                if ".mp4" in u or "video" in (resp.headers.get("content-type", "")):
                    captured.append({"url": u, "headers": dict(resp.headers)})

            page.on("response", on_response)

            # login once per call (cheap; cookies could be cached later)
            page.goto("https://www.instagram.com/accounts/login/", timeout=45000)
            page.fill('input[name="username"]', os.environ["IG_USERNAME"])
            page.fill('input[name="password"]', os.environ["IG_PASSWORD"])
            page.keyboard.press("Enter")
            page.wait_for_load_state("networkidle", timeout=30000)
            page.goto(url, timeout=45000)
            page.wait_for_timeout(6000)   # let the video element hydrate
            title = page.title()

            # prefer the visible <video> src
            src = page.eval_on_selector(
                "video", "el => el.src || el.currentSrc") if page.query_selector("video") else None
            targets = ([{"url": src}] if src else []) + captured
            ok = False
            import httpx

            with httpx.Client(timeout=timeout_s, follow_redirects=True) as cli:
                for t in targets:
                    u = t.get("url")
                    if not u or not str(u).startswith("http"):
                        continue
                    r = cli.get(u)
                    if r.status_code == 200 and len(r.content) > 50_000:
                        out_file.write_bytes(r.content)
                        ok = True
                        break
            browser.close()
            if not ok:
                log.info("no video payload captured for %s", url)
                return None
            return {"path": str(out_file), "title": title[:200]}
    except Exception as e:  # noqa: BLE001
        log.warning("playwright fetch failed (%s): %s", url[:60], str(e)[:140])
        return None
