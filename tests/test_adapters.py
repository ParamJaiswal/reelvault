"""V2-5: Multi-source adapter tests.

Verifies X and article adapters match/resolve correctly, content_kind
propagation, and document body storage.
"""
from unittest.mock import patch, MagicMock


def test_x_adapter_matches_twitter_url():
    from app.ingest.adapters.x_adapter import XPostAdapter
    from app.ingest.adapters.base import IngestRequest
    a = XPostAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://x.com/elonmusk/status/1234567890")
    assert a.matches(req) is True


def test_x_adapter_matches_old_twitter_domain():
    from app.ingest.adapters.x_adapter import XPostAdapter
    from app.ingest.adapters.base import IngestRequest
    a = XPostAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://twitter.com/user/status/9876543210")
    assert a.matches(req) is True


def test_x_adapter_rejects_non_tweet_url():
    from app.ingest.adapters.x_adapter import XPostAdapter
    from app.ingest.adapters.base import IngestRequest
    a = XPostAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://example.com/article")
    assert a.matches(req) is False


def test_x_adapter_resolve_returns_content_kind():
    from app.ingest.adapters.x_adapter import XPostAdapter
    from app.ingest.adapters.base import IngestRequest
    a = XPostAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://x.com/user/status/1234567890")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "__typename": "Tweet",
        "text": "Hello world",
        "user": {"screen_name": "testuser", "name": "Test"},
        "mediaDetails": [],
    }
    mock_resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.x_adapter.httpx.get", return_value=mock_resp):
        result = a.resolve(req)
    assert result["content_kind"] == "x_post"
    assert result["shortcode"] == "1234567890"
    assert result["caption"] == "Hello world"
    assert result["author_handle"] == "testuser"


def test_x_adapter_tombstone_raises():
    from app.ingest.adapters.x_adapter import XPostAdapter
    from app.ingest.adapters.base import IngestRequest
    import pytest
    a = XPostAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://x.com/user/status/1234567890")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"__typename": "TweetTombstone"}
    mock_resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.x_adapter.httpx.get", return_value=mock_resp):
        with pytest.raises(ValueError, match="unavailable"):
            a.resolve(req)


def test_article_adapter_matches_http_url():
    from app.ingest.adapters.article_adapter import ArticleAdapter
    from app.ingest.adapters.base import IngestRequest
    a = ArticleAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://arstechnica.com/science/article")
    assert a.matches(req) is True


def test_article_adapter_skips_instagram():
    from app.ingest.adapters.article_adapter import ArticleAdapter
    from app.ingest.adapters.base import IngestRequest
    a = ArticleAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://www.instagram.com/reel/ABC123/")
    assert a.matches(req) is False


def test_article_adapter_skips_x():
    from app.ingest.adapters.article_adapter import ArticleAdapter
    from app.ingest.adapters.base import IngestRequest
    a = ArticleAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://x.com/user/status/123")
    assert a.matches(req) is False


def test_article_adapter_resolve_stores_body(tmp_db):
    from app.ingest.adapters.article_adapter import ArticleAdapter
    from app.ingest.adapters.base import IngestRequest
    a = ArticleAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://example.com/article")
    html = "<html><head><title>Test</title></head><body>" + \
           "<p>This is a long article body with enough text to pass the minimum threshold for extraction.</p>" * 5 + \
           "</body></html>"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.article_adapter.httpx.get", return_value=mock_resp):
        result = a.resolve(req)
    assert result["content_kind"] == "article"
    assert len(result["meta"]["body_text"]) > 100
    assert result["needs_download"] is False


def test_v2_adapters_registered():
    """X and article adapters are in the ADAPTERS list."""
    from app.ingest.adapters.base import ADAPTERS, _ensure_v2_adapters
    _ensure_v2_adapters()
    names = [a.name for a in ADAPTERS]
    assert "x_post" in names
    assert "article" in names


def test_route_x_url_to_x_adapter():
    """route() sends X URLs to XPostAdapter, not Instagram."""
    from app.ingest.adapters.base import route, IngestRequest
    req = IngestRequest(user_id=1, kind="url",
                        url="https://x.com/user/status/1234567890")
    adapter, _ = route(req)
    assert adapter.name == "x_post"


def test_document_body_stored_for_text_source(tmp_db):
    """Text source ingest stores body in documents table."""
    import sqlite3
    from app.db.schema import get_db

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, source_url,"
            " status, current_stage)"
            " VALUES(?,'url','article','http://example.com','completed','done')",
            (uid,)).lastrowid
        db.execute(
            "INSERT INTO documents(reel_id, body_text, mime_type)"
            " VALUES(?,'transformer attention mechanism paper','text/html')",
            (rid,))

    db = sqlite3.connect(str(tmp_db))
    row = db.execute("SELECT body_text FROM documents WHERE reel_id=?", (rid,)).fetchone()
    db.close()
    assert row is not None
    assert "transformer" in row[0]


def test_paper_adapter_matches_arxiv():
    from app.ingest.adapters.paper_adapter import PaperAdapter
    from app.ingest.adapters.base import IngestRequest
    a = PaperAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://arxiv.org/abs/2301.08745")
    assert a.matches(req) is True


def test_paper_adapter_matches_doi():
    from app.ingest.adapters.paper_adapter import PaperAdapter
    from app.ingest.adapters.base import IngestRequest
    a = PaperAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://doi.org/10.1038/s41586-021-03819-2")
    assert a.matches(req) is True


def test_paper_adapter_rejects_non_paper():
    from app.ingest.adapters.paper_adapter import PaperAdapter
    from app.ingest.adapters.base import IngestRequest
    a = PaperAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://example.com/blog/post")
    assert a.matches(req) is False


def test_paper_adapter_arxiv_resolve():
    from app.ingest.adapters.paper_adapter import PaperAdapter
    from app.ingest.adapters.base import IngestRequest
    a = PaperAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://arxiv.org/abs/1706.03762")
    xml = """<?xml version="1.0"?>
    <entry>
      <title>Attention Is All You Need</title>
      <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
      <author><name>Ashish Vaswani</name></author>
      <author><name>Noam Shazeer</name></author>
    </entry>"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = xml
    mock_resp.raise_for_status = MagicMock()
    with patch("app.ingest.adapters.paper_adapter.httpx.get", return_value=mock_resp):
        result = a.resolve(req)
    assert result["content_kind"] == "paper"
    assert "Attention" in result["meta"]["title"]
    assert "transduction" in result["meta"]["body_text"]
    assert result["shortcode"] == "1706.03762"


def test_paper_adapter_registered():
    from app.ingest.adapters.base import ADAPTERS, _ensure_v2_adapters
    _ensure_v2_adapters()
    names = [a.name for a in ADAPTERS]
    assert "paper" in names


def test_linkedin_adapter_matches_post_url():
    from app.ingest.adapters.linkedin_adapter import LinkedInAdapter
    from app.ingest.adapters.base import IngestRequest
    a = LinkedInAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://www.linkedin.com/posts/user-some-1234567890-abcd")
    assert a.matches(req) is True


def test_linkedin_adapter_matches_activity_url():
    from app.ingest.adapters.linkedin_adapter import LinkedInAdapter
    from app.ingest.adapters.base import IngestRequest
    a = LinkedInAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://www.linkedin.com/feed/update/urn:li:activity:9876543210")
    assert a.matches(req) is True


def test_linkedin_adapter_rejects_non_linkedin():
    from app.ingest.adapters.linkedin_adapter import LinkedInAdapter
    from app.ingest.adapters.base import IngestRequest
    a = LinkedInAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://example.com/post/123")
    assert a.matches(req) is False


def test_linkedin_text_fallback_returns_content_kind():
    from app.ingest.adapters.linkedin_adapter import LinkedInAdapter
    from app.ingest.adapters.base import IngestRequest
    a = LinkedInAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="https://www.linkedin.com/posts/user-1234567890-abcd",
                        caption="Great insights on AI safety")
    # yt-dlp will fail on this fake URL, so fallback path runs
    result = a.resolve(req)
    assert result["content_kind"] == "linkedin_post"
    assert result["shortcode"] == "1234567890"


def test_linkedin_adapter_registered():
    from app.ingest.adapters.base import ADAPTERS, _ensure_v2_adapters
    _ensure_v2_adapters()
    names = [a.name for a in ADAPTERS]
    assert "linkedin_post" in names


def test_build_spans_includes_document_body():
    """build_spans creates document-source spans from doc body text."""
    from app.pipeline.stages import build_spans
    spans = build_spans([], [], "", "First paragraph.\n\nSecond paragraph.")
    doc_spans = [s for s in spans if s.source == "document"]
    assert len(doc_spans) == 2
    assert doc_spans[0].text == "First paragraph."
    assert doc_spans[1].text == "Second paragraph."
    assert doc_spans[0].t_s is None
    assert doc_spans[0].page is None


def test_source_span_has_page_field():
    """SourceSpan supports page locator for PDF sources."""
    from app.knowledge.evidence import SourceSpan
    span = SourceSpan(text="test", t_s=None, source="document", page=3)
    assert span.page == 3
    # Default is None for backward compat
    span2 = SourceSpan(text="test", t_s=1.0, source="transcript")
    assert span2.page is None


def test_article_adapter_rejects_localhost():
    """SSRF protection: localhost URLs are rejected."""
    import pytest
    from app.ingest.adapters.article_adapter import ArticleAdapter
    from app.ingest.adapters.base import IngestRequest
    a = ArticleAdapter()
    req = IngestRequest(user_id=1, kind="url",
                        url="http://127.0.0.1:8756/healthz")
    with pytest.raises(ValueError, match="private or reserved"):
        a.resolve(req)


def test_error_sanitization_strips_bearer():
    """API keys and Bearer tokens are redacted from error strings."""
    from app.ai.providers import _sanitize_error
    err = "HTTP 401: Bearer sk-abc123xyz invalid"
    assert "sk-abc123xyz" not in _sanitize_error(err)
    assert "[REDACTED]" in _sanitize_error(err)


def test_error_sanitization_strips_api_key():
    from app.ai.providers import _sanitize_error
    err = "api_key=sk-secret123 failed"
    assert "sk-secret123" not in _sanitize_error(err)


def test_fts_shadow_table_exists(tmp_db):
    """Migration 8 creates reels_fts_shadow table."""
    import sqlite3
    db = sqlite3.connect(str(tmp_db))
    tables = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    db.close()
    assert "reels_fts_shadow" in tables


def test_refresh_fts_updates_shadow(tmp_db):
    """refresh_fts writes to shadow table for correct delete-on-update."""
    import sqlite3
    from app.db.schema import get_db, refresh_fts

    with get_db() as db:
        uid = db.execute(
            "INSERT INTO users(username, display_name, api_key_hash)"
            " VALUES('u','U','')").lastrowid
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, source_url,"
            " title, status, current_stage)"
            " VALUES(?,'upload','video','http://x','Test','completed','done')",
            (uid,)).lastrowid

    refresh_fts(rid)

    db = sqlite3.connect(str(tmp_db))
    row = db.execute("SELECT title FROM reels_fts_shadow WHERE reel_id=?",
                     (rid,)).fetchone()
    db.close()
    assert row is not None
    assert row[0] == "Test"
