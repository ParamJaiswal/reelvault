"""D4 — parse_deadline() ISO and human date parsing (app/knowledge/deadlines.py).

The dateutil dayfirst quirk is documented (docs/SESSION_HANDOFF.md,
docs/PHASE_REPORT.md): with dayfirst=True, an ISO YYYY-MM-DD whose two
trailing numbers are BOTH valid months is flipped (2026-06-01 -> Jan 6).
The PATCH /api/reels correction endpoint added an ISO fast-path, but
parse_deadline itself still exposes the quirk, so the quirk-sensitive case
is marked xfail (documents the bug; the fix is owned elsewhere). For
2026-09-15 the flip would yield month=15, invalid, so dateutil resolves it
correctly — that case must parse as September 15, never swapped.
"""
from datetime import datetime

import pytest

from app.knowledge.deadlines import parse_deadline

TODAY = datetime(2026, 9, 9)


def test_iso_date_parses_september_not_swapped():
    d = parse_deadline("2026-09-15", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 9, 15)
    assert d.confidence >= 0.8
    assert d.raw == "2026-09-15"


@pytest.mark.xfail(
    reason="documents bug: dateutil dayfirst=True flips ISO YYYY-MM-DD when "
           "both trailing numbers are valid months (2026-06-01 parsed as "
           "Jan 6, then year-rolled to 2027-01-06); the correction endpoint "
           "has an ISO fast-path but parse_deadline does not",
    strict=False)
def test_iso_date_ambiguous_components_not_flipped():
    # Quirk-sensitive ISO date: both trailing numbers are valid months.
    # June 1 2026 is past relative to `today`, so the rolling-year policy
    # must return 2027-06-01 — not January 6.
    d = parse_deadline("2026-06-01", today=TODAY)
    assert (d.date.year, d.date.month, d.date.day) == (2027, 6, 1)


def test_month_name_with_day():
    d = parse_deadline("September 15", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 9, 15)


def test_ordinal_day_before_month():
    d = parse_deadline("15th September", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 9, 15)


def test_day_first_slash_date():
    d = parse_deadline("15/09/2026", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 9, 15)


def test_empty_input_is_unparseable_and_safe():
    d = parse_deadline("", today=TODAY)
    assert d.date is None and d.reminder_date is None
    assert d.confidence == 0.0
    assert "unparseable" in d.note
