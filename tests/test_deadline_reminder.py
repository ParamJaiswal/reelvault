"""B4: best_deadline value-parse fallback at reduced confidence, plus
reminder_for clamp regression tests (A4 behavior — tests only, the
reminder_for implementation is frozen for this change)."""
from datetime import datetime, timedelta

import pytest

TODAY = datetime(2026, 8, 26)


class TestBestDeadlineFallback:
    def test_quote_parse_keeps_full_confidence(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "2026-09-15",
                  "confidence": 0.9, "evidence_source": "transcript",
                  "evidence_quote": "apply before September 15 2026"}]
        d = best_deadline(facts, "", today=TODAY)
        assert d and (d.date.month, d.date.day) == (9, 15)
        assert d.date.year == 2026
        # corroborated by the quote: max(0.85 parse, 0.9 fact) preserved
        assert d.confidence == pytest.approx(0.9)

    def test_value_fallback_when_quote_yields_no_date(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "September 15 2026",
                  "confidence": 0.9, "evidence_source": "transcript",
                  "evidence_quote": "go apply now, link in bio, thanks"
                                    " for watching"}]
        d = best_deadline(facts, "", today=TODAY)
        assert d and (d.date.month, d.date.day) == (9, 15)
        assert d.date.year == 2026
        assert d.confidence == pytest.approx(0.5)  # not ~0.85

    def test_fallback_confidence_not_boosted_by_fact_confidence(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "due date", "value": "March 3, 2027",
                  "confidence": 0.95, "evidence_source": "caption",
                  "evidence_quote": "no dates in here"}]
        d = best_deadline(facts, "", today=TODAY)
        assert d and d.date.year == 2027
        assert d.confidence == pytest.approx(0.5)

    def test_both_unparseable_still_dropped(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "sometime soon-ish",
                  "confidence": 0.9,
                  "evidence_quote": "no digits anywhere here"}]
        assert best_deadline(facts, "", today=TODAY) is None

    def test_fallback_raw_is_the_value(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "15 Sept 2026",
                  "confidence": 0.6,
                  "evidence_quote": "watch till the end"}]
        d = best_deadline(facts, "", today=TODAY)
        assert d and d.raw == "15 Sept 2026"
        assert d.date and (d.date.month, d.date.day) == (9, 15)

    def test_fact_without_quote_uses_fallback_path(self):
        """Facts lacking a quote key (older callers) parse the value at
        the reduced fallback confidence — the quote yields no date."""
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "September 15",
                  "confidence": 0.9, "evidence_source": "transcript"}]
        d = best_deadline(facts, "apply soon", today=TODAY)
        assert d and d.date.month == 9
        assert d.confidence == pytest.approx(0.5)


class TestReminderClamp:
    def test_far_future_deadline_reminds_days_before(self):
        from app.knowledge.deadlines import reminder_for

        deadline = datetime(2027, 3, 10, 12, 0)
        assert reminder_for(deadline) == deadline - timedelta(days=5)

    def test_near_deadline_clamped_to_now_plus_one_hour(self):
        from app.knowledge.deadlines import reminder_for

        deadline = datetime.now() + timedelta(days=2)
        r = reminder_for(deadline)
        assert r <= deadline
        delta = (r - datetime.now()).total_seconds()
        assert 3500 < delta <= 3660  # ~1h from now, never after deadline

    def test_past_deadline_returns_deadline_itself(self):
        from app.knowledge.deadlines import reminder_for

        past = datetime.now() - timedelta(days=3)
        assert reminder_for(past) == past
