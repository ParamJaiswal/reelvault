"""best_deadline requires an evidence quote before a date can be stored (the B4
value-only fallback is reversed — owner decision 2026-10-10), plus reminder_for clamp
regression tests (A4 behavior — tests only, the reminder_for implementation is frozen
for this change)."""
from datetime import datetime, timedelta

import pytest

TODAY = datetime(2026, 8, 26)


class TestBestDeadlineCorroboration:
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

    def test_the_stored_raw_is_the_source_quote_not_the_model_value(self):
        """What reaches `deadline_raw` is the text the date was read from, so the
        Evidence Ledger's quote and the deadline agree in the UI."""
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "15 Sept 2026",
                  "confidence": 0.6,
                  "evidence_quote": "the last date is Sept 15 2026"}]
        d = best_deadline(facts, "", today=TODAY)
        assert d and d.raw == "the last date is Sept 15 2026"
        assert d.date and (d.date.month, d.date.day) == (9, 15)

    def test_uncorroborated_value_is_not_a_deadline(self):
        """§8: model output never determines a normalized deadline. The model's value
        parses perfectly and still produces nothing, because the quote it cites holds no
        date. B4 (`7130b4c`) returned this at 0.5; reversing it is the owner's decision
        that a dropped deadline beats an unevidenced one."""
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "September 15 2026",
                  "confidence": 0.9, "evidence_source": "transcript",
                  "evidence_quote": "go apply now, link in bio, thanks"
                                    " for watching"}]
        assert best_deadline(facts, "", today=TODAY) is None

    def test_a_high_fact_confidence_does_not_reopen_the_value_path(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "due date", "value": "March 3, 2027",
                  "confidence": 0.95, "evidence_source": "caption",
                  "evidence_quote": "no dates in here"}]
        assert best_deadline(facts, "", today=TODAY) is None

    def test_both_unparseable_still_dropped(self):
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "sometime soon-ish",
                  "confidence": 0.9,
                  "evidence_quote": "no digits anywhere here"}]
        assert best_deadline(facts, "", today=TODAY) is None

    def test_a_fact_with_no_quote_is_not_a_deadline(self):
        """Facts lacking a quote key cannot corroborate anything, so they no longer fall
        through to the value. A deadline stated in the source is still found by the rules
        path, which is text-derived rather than model-derived."""
        from app.knowledge.deadlines import best_deadline

        facts = [{"field": "deadline", "value": "September 15",
                  "confidence": 0.9, "evidence_source": "transcript"}]
        assert best_deadline(facts, "apply soon", today=TODAY) is None

        d = best_deadline(facts, "the last date is September 15", today=TODAY)
        assert d and d.date.month == 9 and d.source == "rules"


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
