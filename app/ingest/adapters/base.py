"""IngestionAdapter interface + registry.

Compliance note (see docs/INGESTION.md):
Instagram's official APIs do NOT expose a user's saved Reels/collections.
Therefore the implemented adapters are:
  - UrlIngestionAdapter      (paste / share-sheet a reel URL)  [primary]
  - FileIngestionAdapter     (drop an mp4 you already have)
  - WatchFolderAdapter       (auto-ingest files dropped in a folder)
  - ShareTargetAdapter       (PWA share-target endpoint = Android/iOS share)
  - InstagramOfficialAdapter (stub; activates only if Meta ever grants
                              saved-content access via Graph API)
No undisclosed scraping of private content happens anywhere.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestRequest:
    user_id: int
    kind: str                      # url | file | watch_folder | share_target
    url: str | None = None
    local_path: str | None = None
    caption: str = ""
    author_hint: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class IngestionAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def matches(self, req: IngestRequest) -> bool: ...

    @abstractmethod
    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        """Return dict with keys: shortcode?, source_url?, caption?,
        author_handle?, media_path? (local file), needs_download(bool),
        oembed?(dict). Raise ValueError for unusable input."""


SHORTCODE_RE = re.compile(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]{5,})")


def parse_shortcode(url: str | None) -> str | None:
    if not url:
        return None
    m = SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def normalize_instagram_url(url: str) -> str | None:
    """Accept instagram.com / instagr.am links incl. share garbage
    (?igsh=..., &utm_...) and return a clean https://www.instagram.com/reel/<code>/"""
    code = parse_shortcode(url)
    if not code:
        return None
    return f"https://www.instagram.com/reel/{code}/"


class UrlIngestionAdapter(IngestionAdapter):
    """Paste any Instagram reel/post URL. Media fetch is attempted with
    yt-dlp (public content only); if unavailable we still store metadata +
    caption and let the user optionally attach a file."""

    name = "url"

    def matches(self, req: IngestRequest) -> bool:
        return req.kind == "url" and bool(req.url)

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        clean = normalize_instagram_url(req.url or "")
        if not clean:
            raise ValueError(
                "That doesn't look like an Instagram Reel/Post URL."
            )
        return {
            "shortcode": parse_shortcode(clean),
            "source_url": clean,
            "caption": req.caption,
            "author_handle": req.author_hint,
            "needs_download": True,
        }


class FileIngestionAdapter(IngestionAdapter):
    name = "file"

    def matches(self, req: IngestRequest) -> bool:
        return req.kind == "file" and bool(req.local_path)

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        p = req.local_path or ""
        if not p.lower().endswith((".mp4", ".mov", ".webm", ".m4v", ".mkv")):
            raise ValueError("Unsupported file type. Use mp4/mov/webm.")
        code = parse_shortcode(req.url or "") or req.meta.get("shortcode")
        return {
            "shortcode": code,
            "source_url": req.url,
            "caption": req.caption,
            "author_handle": req.author_hint,
            "media_path": p,
            "needs_download": False,
        }


class WatchFolderAdapter(FileIngestionAdapter):
    """Scans a configured folder; each new video file becomes an ingest."""

    name = "watch_folder"

    def matches(self, req: IngestRequest) -> bool:
        return req.kind == "file" or (
            req.kind == "watch_folder" and bool(req.local_path))


class ShareTargetAdapter(IngestionAdapter):
    """PWA share_target: OS share sheet sends text/url/title to our endpoint."""
    name = "share_target"

    def matches(self, req: IngestRequest) -> bool:
        return req.kind == "share_target" and bool(req.url or req.caption)

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        text = (req.caption or "") + " " + (req.url or "")
        clean = normalize_instagram_url(text)
        if not clean:
            raise ValueError("Shared content had no recognizable Reel link.")
        return {
            "shortcode": parse_shortcode(clean),
            "source_url": clean,
            "caption": (req.caption or "").replace(clean, "").strip(),
            "author_handle": req.author_hint,
            "needs_download": True,
        }


class InstagramOfficialAdapter(IngestionAdapter):
    """Placeholder for Meta's official Graph API. NOT ACTIVE: the API does
    not expose saved collections (verified Aug 2026). If Meta adds it, wire
    OAuth here without touching the rest of the system."""

    name = "instagram_official"
    active = False

    def matches(self, req: IngestRequest) -> bool:
        return False

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        raise NotImplementedError(
            "Official saved-Reels access is not offered by Instagram's API."
        )


ADAPTERS: list[IngestionAdapter] = [
    ShareTargetAdapter(),
    UrlIngestionAdapter(),
    WatchFolderAdapter(),
    FileIngestionAdapter(),
]

_v2_loaded = False


def _ensure_v2_adapters() -> None:
    """Load v2 multi-source adapters once. Called lazily by route() to avoid
    circular imports when adapter modules import base.py."""
    global _v2_loaded
    if _v2_loaded:
        return
    _v2_loaded = True
    try:
        from app.ingest.adapters.x_adapter import XPostAdapter
        ADAPTERS.insert(0, XPostAdapter())
    except ImportError:
        pass
    try:
        from app.ingest.adapters.article_adapter import ArticleAdapter
        ADAPTERS.insert(0, ArticleAdapter())
    except ImportError:
        pass
    try:
        from app.ingest.adapters.paper_adapter import PaperAdapter
        ADAPTERS.insert(0, PaperAdapter())
    except ImportError:
        pass
    try:
        from app.ingest.adapters.linkedin_adapter import LinkedInAdapter
        ADAPTERS.insert(0, LinkedInAdapter())
    except ImportError:
        pass


def route(req: IngestRequest) -> tuple[IngestionAdapter, dict[str, Any]]:
    _ensure_v2_adapters()
    for a in ADAPTERS:
        if a.matches(req):
            return a, a.resolve(req)
    raise ValueError("No ingestion adapter matched this request.")
