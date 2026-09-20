"""Research paper ingestion adapter.

Supports arXiv and DOI-based lookup via OpenAlex (free, no key).
Downloads OA PDFs when available; stores metadata + abstract as document body.
Compliance: open-access only, never bypass paywalls.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from app.ingest.adapters.base import IngestionAdapter, IngestRequest

ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")
DOI_RE = re.compile(r"(?:doi\.org/|doi:)(10\.\d{4,}/[^\s]+)", re.I)


def _parse_arxiv(url: str) -> str | None:
    m = ARXIV_RE.search(url)
    return m.group(1) if m else None


def _parse_doi(url: str) -> str | None:
    m = DOI_RE.search(url)
    return m.group(1) if m else None


class PaperAdapter(IngestionAdapter):
    """Ingest research papers by arXiv ID or DOI."""

    name = "paper"

    def matches(self, req: IngestRequest) -> bool:
        if not req.url:
            return False
        return _parse_arxiv(req.url) is not None or _parse_doi(req.url) is not None

    def resolve(self, req: IngestRequest) -> dict[str, Any]:
        url = req.url or ""
        arxiv_id = _parse_arxiv(url)
        doi = _parse_doi(url)

        title = ""
        abstract = ""
        authors = ""
        pdf_url = ""

        if arxiv_id:
            try:
                r = httpx.get(
                    f"https://export.arxiv.org/api/query?id_list={arxiv_id}",
                    timeout=15, follow_redirects=True)
                r.raise_for_status()
                xml = r.text
                # The Atom feed's top-level <title> is feed metadata ("arXiv
                # Query: ..."), not the paper title — parse inside <entry>.
                import html as _html
                entry = re.search(r"<entry>(.*?)</entry>", xml, re.S)
                entry = entry.group(1) if entry else xml
                t = re.search(r"<title>(.*?)</title>", entry, re.S)
                a = re.search(r"<summary>(.*?)</summary>", entry, re.S)
                title = _html.unescape(re.sub(r"\s+", " ", t.group(1))).strip() if t else ""
                abstract = _html.unescape(re.sub(r"\s+", " ", a.group(1))).strip() if a else ""
                auth_tags = re.findall(r"<name>(.*?)</name>", entry)
                authors = ", ".join(auth_tags[:5])
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            except Exception as e:
                raise ValueError(f"arXiv lookup failed: {e}") from e

        elif doi:
            try:
                r = httpx.get(
                    f"https://api.openalex.org/works/doi:{doi}",
                    timeout=15,
                    headers={"User-Agent": "ReelVault/2.0"})
                r.raise_for_status()
                data = r.json()
                title = data.get("title", "")
                abstract_inv = data.get("abstract_inverted_index")
                if abstract_inv:
                    words = {}
                    for word, positions in abstract_inv.items():
                        for pos in positions:
                            words[pos] = word
                    abstract = " ".join(words[k] for k in sorted(words))
                auth_list = data.get("authorships", [])
                authors = ", ".join(
                    a.get("author", {}).get("display_name", "")
                    for a in auth_list[:5])
                oa = data.get("open_access", {})
                if oa.get("is_oa"):
                    pdf_url = oa.get("oa_url", "")
            except Exception as e:
                raise ValueError(f"OpenAlex lookup failed: {e}") from e

        if not title and not abstract:
            raise ValueError("Could not retrieve paper metadata.")

        body = f"{title}\n\n{abstract}"
        return {
            "shortcode": arxiv_id or doi,
            "source_url": url,
            "caption": abstract[:500],
            "author_handle": authors,
            "needs_download": False,
            "content_kind": "paper",
            "meta": {
                "body_text": body,
                "title": title,
                "pdf_url": pdf_url,
            },
        }
