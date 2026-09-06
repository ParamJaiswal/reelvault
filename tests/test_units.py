"""Unit tests: adapters, URL parsing, evidence engine, schemas."""
import pytest

from app.ingest.adapters.base import (IngestRequest, ShareTargetAdapter,
                                      UrlIngestionAdapter, normalize_instagram_url,
                                      parse_shortcode, route)


class TestUrlParsing:
    def test_plain_reel(self):
        assert parse_shortcode("https://www.instagram.com/reel/Cxyz123abc/") == "Cxyz123abc"

    def test_share_garbage(self):
        u = "https://www.instagram.com/reel/DAbc123_-x/?igsh=MzRlODBiNWFlZA==&utm_source=share"
        assert normalize_instagram_url(u) == "https://www.instagram.com/reel/DAbc123_-x/"

    def test_mobile_subdomain_and_post(self):
        assert parse_shortcode("https://www.instagram.com/p/DQwerty123/?igsh=1") == "DQwerty123"

    def test_not_an_ig_link(self):
        assert normalize_instagram_url("https://youtube.com/watch?v=x") is None


class TestAdapters:
    def test_route_url(self):
        a, r = route(IngestRequest(user_id=1, kind="url",
                                   url="https://instagram.com/reel/ABCdef123/"))
        assert isinstance(a, UrlIngestionAdapter)
        assert r["shortcode"] == "ABCdef123"
        assert r["needs_download"] is True

    def test_route_share_target_extracts_link_from_text(self):
        a, r = route(IngestRequest(
            user_id=1, kind="share_target",
            caption="Check this out https://www.instagram.com/reel/Qrs567klm/?igsh=xyz"))
        assert r["shortcode"] == "Qrs567klm"

    def test_bad_url_raises_valueerror(self):
        with pytest.raises(ValueError):
            route(IngestRequest(user_id=1, kind="url", url="not a url"))

    def test_file_adapter_rejects_non_video(self):
        with pytest.raises(ValueError):
            route(IngestRequest(user_id=1, kind="file", local_path="x.txt"))


class TestEvidence:
    def make_spans(self):
        from app.knowledge.evidence import SourceSpan
        return [
            SourceSpan("Salary package 8 to 10 lakh per year for freshers", 17.0,
                       "transcript"),
            SourceSpan("WE'RE HIRING Data Analyst Intern", 2.5, "ocr"),
            SourceSpan("Apply at zylker.example.com/careers before 15 Sept", 12.0,
                       "transcript"),
        ]

    def test_exact_quote_match(self):
        from app.knowledge.evidence import find_evidence
        m = find_evidence("salary package 8 to 10 lakh", "₹8-10 LPA",
                          self.make_spans())
        assert m.similarity >= 0.9
        assert m.span.t_s == 17.0
        assert m.span.source == "transcript"

    def test_hallucinated_fact_dropped(self):
        from app.knowledge.evidence import HALLUCINATION_THRESHOLD, find_evidence
        m = find_evidence("salary is 50 crore dollars", "50Cr USD",
                          self.make_spans())
        assert m.similarity < HALLUCINATION_THRESHOLD

    def test_confidence_bounds(self):
        from app.knowledge.evidence import confidence_score
        assert confidence_score(1.0, 3, 0.9) <= 0.99
        assert confidence_score(0.0, 0, None) >= 0.05


class TestSchemas:
    def test_categories_capped(self):
        from app.knowledge.schemas import BaseExtraction
        e = BaseExtraction(categories=["Job"] * 10)
        assert len(e.categories) <= 6

    def test_schema_fields_registry(self):
        from app.knowledge.schemas import SCHEMA_FIELDS
        assert set(SCHEMA_FIELDS) == {"job", "education", "tool", "event"}
