"""Article/web page ingestion adapter.

Extracts article text from any URL. Uses trafilatura (Apache-2.0) when
available; falls back to basic HTML text extraction. Single GET per URL,
respects robots.txt intent. No bulk crawling.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.ingest.adapters.base import IngestionAdapter, IngestRequest


def _is_safe_url(url: str) -> bool:
    """Reject private/reserved IPs to prevent SSRF."""
    try:
        host = urlparse(url).hostname or ""
        if not host:
            return False
        # Resolve hostname and check all addresses
        import socket
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return False
        return True
    except Exception:
        return False

# Domains we don't try to ingest (social platforms have their own adapters)
SKIP_DOMAINS = {
    "instagram.com", "instagr.am",
    "twitter.com", "x.com",
    "linkedin.com",
    "youtube.com", "youtu.be",
    "tiktok.com",
}


def _is_blocked_host(host: str) -> bool:
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in SKIP_DOMAINS)


def _extract_text_basic(html: str) -> str:
    """Strip tags, collapse whitespace. Last resort when trafilatura missing."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:50_000]  # cap at 50K chars


class ArticleAdapter(IngestionAdapter):
    """Ingest a web article by URL."""

    name = "article"

    def matches(self, req: IngestRequest) -> bool:
        if not req.url or req.kind not in ("url", "share_target"):
            return False
        try:
            host = urlparse(req.url).hostname or ""
        except Exception:
            return False
        if _is_blocked_host(host):
            return False
        return req.url.startswith(("http://", "https://"))

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        url = req.url or ""
        # follow_redirects=False + re-checking every hop: a public host that
        # 302s to 127.0.0.1/169.254.169.254 would otherwise bypass the guard.
        html = ""
        try:
            for _ in range(5):
                if not _is_safe_url(url):
                    raise ValueError("URL resolves to a private or reserved address.")
                r = httpx.get(
                    url, timeout=30, follow_redirects=False,
                    headers={"User-Agent": "ReelVault/2.0 (+reelvault.local)"})
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc:
                        break
                    url = str(httpx.URL(url).join(loc))
                    continue
                r.raise_for_status()
                html = r.text
                break
            else:
                raise ValueError("Too many redirects.")
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Could not fetch article: {e}") from e

        body = ""
        title = ""
        try:
            import trafilatura
            body = trafilatura.extract(html) or ""
            meta = trafilatura.extract_metadata(html)
            if meta:
                title = getattr(meta, "title", "") or ""
        except ImportError:
            body = _extract_text_basic(html)
            m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            title = m.group(1).strip() if m else ""

        if len(body.strip()) < 100:
            raise ValueError("Page had too little extractable text.")

        return {
            "source_url": url,
            "caption": body[:500],
            "author_handle": req.author_hint,
            "needs_download": False,
            "content_kind": "article",
            "meta": {
                "body_text": body,
                "title": title,
            },
        }
