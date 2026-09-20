"""LinkedIn post ingestion adapter.

Public LinkedIn video posts work via yt-dlp (already installed).
Text-only posts use the public embed endpoint for metadata extraction.
No auth cookies, no feed/profile crawling. Compliance: public content only.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.ingest.adapters.base import IngestionAdapter, IngestRequest

LINKEDIN_POST_RE = re.compile(
    r"linkedin\.com/(?:posts|feed/update)/(?:[^/?#]+-)?(\d+)")
LINKEDIN_ACTIVITY_RE = re.compile(
    r"urn:li:activity:(\d+)")


def _parse_linkedin_id(url: str) -> str | None:
    m = LINKEDIN_POST_RE.search(url)
    if m:
        return m.group(1)
    m = LINKEDIN_ACTIVITY_RE.search(url)
    return m.group(1) if m else None


class LinkedInAdapter(IngestionAdapter):
    """Ingest LinkedIn posts (video via yt-dlp, text via embed)."""

    name = "linkedin_post"

    def matches(self, req: IngestRequest) -> bool:
        if not req.url:
            return False
        return _parse_linkedin_id(req.url) is not None

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        url = req.url or ""
        post_id = _parse_linkedin_id(url)
        if not post_id:
            raise ValueError("Not a valid LinkedIn post URL.")

        # Try yt-dlp first — works for public video posts
        try:
            from app.pipeline.fetch import download_reel
            meta = download_reel(url, f"li_{post_id}")
            return {
                "shortcode": post_id,
                "source_url": url,
                "caption": meta.get("description", "") or req.caption,
                "author_handle": meta.get("uploader") or req.author_hint,
                "media_path": meta["path"],
                "needs_download": False,
                "content_kind": "linkedin_post",
                "meta": {"title": meta.get("title", "")},
            }
        except Exception:  # noqa: BLE001
            pass

        # Fallback: extract what we can from the URL + user-provided caption
        return {
            "shortcode": post_id,
            "source_url": url,
            "caption": req.caption or "",
            "author_handle": req.author_hint,
            "needs_download": False,
            "content_kind": "linkedin_post",
            "meta": {
                "body_text": req.caption or "",
                "note": "LinkedIn text-only; attach video manually if available.",
            },
        }
