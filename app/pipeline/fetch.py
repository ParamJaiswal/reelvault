"""Media fetcher for public Instagram URLs via yt-dlp.

Design: best-effort. If the fetch fails (login-walled, removed, geo, ToS
automation wall), ingestion still succeeds with metadata-only mode — the UI
tells the user to attach the file manually. We NEVER bypass auth walls.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.core.config import settings
from app.pipeline.media import MAX_VIDEO_MB

log = logging.getLogger("rv.fetch")

YT_DLP_ERRORS_LOGINWALL = ("login required", "private", "rate-limit", "checkpoint")


def verify_size(path: Path) -> Path:
    """Reject a fetched file that breaks the storage ceiling, deleting it so
    an oversized download can never reach the media stage or linger on disk.

    Measured on the file rather than via a yt-dlp format filter
    (max_filesize): Instagram's reported filesize is frequently absent, and a
    filter that silently matches nothing fails as a misleading 'unavailable'
    download error. The bytes on disk are the only trustworthy measurement.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return path          # vanished; the caller's existence check reports it
    if size > MAX_VIDEO_MB * 1000 * 1000:
        path.unlink(missing_ok=True)
        log.info("rejected %s (%.0f MB over the %d MB limit)",
                 path.name, size / 1e6, MAX_VIDEO_MB)
        raise FetchError(
            f"That video is bigger than the {MAX_VIDEO_MB} MB limit, so it "
            f"wasn't imported. Trim it, or attach a smaller file instead."
        )
    return path


def download_reel(url: str, shortcode: str) -> dict:
    """Returns {path} on success; raises FetchError with user-safe message.

    Order: yt-dlp (anonymous) -> Playwright w/ disposable IG account
    (only if IG_USERNAME/IG_PASSWORD env vars are set). We NEVER use the
    user's own account.
    """
    outdir = settings.media_dir / "video"
    outtmpl = str(outdir / f"{shortcode}.%(ext)s")
    try:
        import yt_dlp
    except ImportError as e:
        raise FetchError("Downloader not installed.") from e

    opts = {
        "outtmpl": outtmpl,
        "format": "mp4/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 2,
        # NOTE: no cookies, no auth impersonation — compliant-by-design
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as ydl_err:  # noqa: BLE001
        msg = str(ydl_err).lower()
        loginwalled = any(k in msg for k in YT_DLP_ERRORS_LOGINWALL)
        # ---- fallback: disposable-account browser fetch (env-gated) ----
        from app.pipeline import ig_browser

        if ig_browser.has_ig_credentials():
            log.info("yt-dlp failed (%s); trying Playwright fallback", msg[:60])
            result = ig_browser.fetch_reel(url, outdir)
            if result:
                return {"path": str(verify_size(Path(result["path"]))),
                        "caption": "",
                        "author_handle": "",
                        "title": result.get("title") or ""}
        if loginwalled:
            raise FetchError(
                "This Reel needs login to view, so it can't be fetched "
                "automatically. Save the video file and drop it in instead."
            )
        raise FetchError(
            "Couldn't download this Reel (it may be private, deleted, or "
            "region-locked). You can attach the video file manually."
        )
    filename = ydl_prepare_filename(info)
    p = Path(filename)
    if not p.exists():
        # ext may differ; find newest matching prefix
        cands = sorted(outdir.glob(f"{shortcode}.*"), key=lambda x: x.stat().st_mtime)
        if not cands:
            raise FetchError("Download reported success but no file found.")
        p = cands[-1]
    # verify the file we actually settled on, not just yt-dlp's first guess
    verify_size(p)
    meta = {
        "path": str(p),
        "caption": (info or {}).get("description") or "",
        "author_handle": (info or {}).get("uploader") or "",
        "title": (info or {}).get("title") or "",
    }
    log.info("downloaded %s -> %s", url, p.name)
    return meta


def ydl_prepare_filename(info: dict) -> str:
    # yt_dlp.YoutubeDL.prepare_filename without re-instantiating
    from yt_dlp import YoutubeDL

    return YoutubeDL({"outtmpl": "%(id)s"}).prepare_filename(info)


class FetchError(Exception):
    pass
