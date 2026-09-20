"""X (Twitter) post ingestion adapter.

Uses the public syndication CDN endpoint — no API key, no auth.
Only works for public tweets. Returns tweet text + media URLs.
Compliance: one user-initiated URL per request, no bulk scraping.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.ingest.adapters.base import IngestionAdapter, IngestRequest

TWEET_ID_RE = re.compile(r"(?:twitter|x)\.com/\w+/status(?:es)?/(\d+)")
SYNDICATION_URL = "https://cdn.syndication.twimg.com/tweet-result"


def parse_tweet_id(url: str) -> str | None:
    m = TWEET_ID_RE.search(url)
    return m.group(1) if m else None


class XPostAdapter(IngestionAdapter):
    """Ingest a public X/Twitter post by URL."""

    name = "x_post"

    def matches(self, req: IngestRequest) -> bool:
        if not req.url:
            return False
        return parse_tweet_id(req.url) is not None

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        tweet_id = parse_tweet_id(req.url or "")
        if not tweet_id:
            raise ValueError("Not a valid X/Twitter post URL.")

        try:
            r = httpx.get(
                SYNDICATION_URL,
                params={"id": tweet_id, "token": "0"},
                headers={"User-Agent": "ReelVault/2.0"},
                timeout=15,
                follow_redirects=True,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            raise ValueError(f"Could not fetch tweet: {e}") from e

        if data.get("__typename") == "TweetTombstone":
            raise ValueError("Tweet is unavailable (deleted, private, or restricted).")

        text = data.get("text", "")
        author = data.get("user", {}).get("screen_name", "")
        name = data.get("user", {}).get("name", "")
        media_urls = []
        for m in data.get("mediaDetails", []):
            if m.get("type") == "photo":
                media_urls.append(m.get("media_url_https", ""))
            elif m.get("type") == "video":
                variants = m.get("video_info", {}).get("variants", [])
                mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
                if mp4s:
                    best = max(mp4s, key=lambda v: v.get("bitrate", 0))
                    media_urls.append(best.get("url", ""))

        return {
            "shortcode": tweet_id,
            "source_url": f"https://x.com/i/status/{tweet_id}",
            "caption": text,
            "author_handle": author or name,
            # Text-kind pipeline never downloads; media URLs kept in meta for
            # a future image-OCR pass.
            "needs_download": False,
            "content_kind": "x_post",
            "meta": {
                "tweet_text": text,
                "media_urls": media_urls,
                "tweet_id": tweet_id,
            },
        }
