"""Deadline intelligence + router guardrail tests (no LLM required)."""
import pytest


class TestDeadlines:
    def test_month_name_parsing(self):
        from app.knowledge.deadlines import parse_deadline
        d = parse_deadline("Apply before September 15", today=__import__("datetime").datetime(2026, 8, 26))
        assert d.date is not None
        assert (d.date.month, d.date.day) == (9, 15)
        assert d.confidence >= 0.7

    def test_abbreviated_month_and_dayfirst(self):
        from app.knowledge.deadlines import parse_deadline
        d = parse_deadline("last date 15 Sept", today=__import__("datetime").datetime(2026, 8, 26))
        assert d.date and (d.date.month, d.date.day) == (9, 15)

    def test_past_date_rolls_forward(self):
        from datetime import datetime

        from app.knowledge.deadlines import parse_deadline
        d = parse_deadline("deadline January 10", today=datetime(2026, 8, 26))
        assert d.date and d.date.year == 2027

    def test_reminder_never_after_deadline(self):
        from datetime import datetime, timedelta

        from app.knowledge.deadlines import parse_deadline
        soon = datetime.now() + timedelta(days=3)
        d = parse_deadline(f"by {soon.strftime('%B %d')}")
        assert d.reminder_date <= d.date

    def test_unparseable_is_safe(self):
        from app.knowledge.deadlines import parse_deadline
        d = parse_deadline("sometime soon-ish maybe")
        assert d.date is None
        assert "unparseable" in d.note

    def test_best_deadline_prefers_facts(self):
        from app.knowledge.deadlines import best_deadline
        facts = [{"field": "deadline", "value": "September 15",
                  "confidence": 0.9, "evidence_source": "transcript"}]
        d = best_deadline(facts, "apply soon")
        assert d and d.date.month == 9


class TestRouterGuardrails:
    def test_category_normalization(self):
        from app.ai.router import _normalize_categories
        assert _normalize_categories(["ai/ml", "Job", "tutorial"]) == \
            ["AI/ML", "Job", "Tutorial"]
        assert _normalize_categories("Job") == ["Job"]
        assert _normalize_categories([42]) == []
        # blob splitting keeps both valid categories
        assert _normalize_categories(["AI/ML, Career"]) == ["AI/ML", "Career"]

    def test_schema_alias(self):
        from app.ai.router import SCHEMA_ALIAS, _valid_classification
        obj = {"categories": ["Tutorial"], "primary_schema": "Tutorial"}
        assert _valid_classification(obj) is True
        assert SCHEMA_ALIAS["internship"] == "job"

    def test_heuristic_classify(self):
        from app.ai.router import heuristic_classify
        r = heuristic_classify("Hiring alert! Data analyst internship in Bangalore, apply before Sept 15.")
        assert "Job" in r["categories"] or "Internship" in r["categories"]
        assert r["primary_schema"] in ("job", "education")
        r2 = heuristic_classify("whisper free offline transcription tool")
        assert "Tool" in r2["categories"]
