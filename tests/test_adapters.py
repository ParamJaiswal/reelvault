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
