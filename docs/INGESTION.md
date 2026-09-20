# Ingestion architecture (v2 multi-source)

## Routing

`app/ingest/adapters/base.py` keeps an ordered adapter list; `route()` returns
the first match. v2 adapters are registered lazily (before the Instagram ones)
so v0.1 URLs are unaffected:

| Adapter | Matches | content_kind | Stage plan |
|---|---|---|---|
| `paper` | arxiv.org, doi.org | `paper` | ocr, classify_extract, embed, finalize |
| `linkedin_post` | linkedin.com posts/activity | `linkedin_post` | ocr, classify_extract, embed, finalize |
| `article` | any other http(s) page | `article` | classify_extract, embed, finalize |
| `x_post` | x.com / twitter.com status URLs | `x_post` | ocr, classify_extract, embed, finalize |
| `share_target` / `url` / `watch_folder` / `file` | Instagram + local video | `video` | full v0.1 pipeline |

Stage plans live in `app/db/queue.py::STAGE_PLANS`; `media`/`transcribe` also
self-skip when `content_kind != 'video'`.

## Compliance lines (do not cross)

- **X**: public syndication CDN only (`cdn.syndication.twimg.com`), one
  user-initiated URL per request. No timelines, no search, no auth.
- **LinkedIn**: public posts through yt-dlp (video posts verified working
  anonymously); text fallback stores user-provided caption. No cookies, no
  feed/profile crawling.
- **Papers**: arXiv API and OpenAlex (both free, no key). Open-access PDFs
  only. Never bypass paywalls.
- **Articles**: single robots-respecting GET. SSRF guard rejects
  private/loopback/resolved-internal IPs before connecting.

## Evidence for text sources

Bodies live in `documents.body_text`. `build_spans()` turns paragraphs into
`SourceSpan(source='document')`; the Evidence Ledger verifies quotes against
them like any other span. Facts carry `evidence_page` for PDF page locators
(added in schema v9, populated when PDF OCR lands).

## Failure policy

Adapter `resolve()` raises `ValueError` with a readable message → HTTP 422.
No silent fallback to the wrong adapter (Instagram URLs are domain-blocklisted
in `article_adapter.SKIP_DOMAINS`).
