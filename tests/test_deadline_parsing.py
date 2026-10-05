"""D4 — parse_deadline() ISO and human date parsing (app/knowledge/deadlines.py).

History: the dateutil dayfirst quirk (ISO YYYY-MM-DD with two trailing
valid months flipped, 2026-06-01 -> Jan 6) was exposed by this file as an
xfail and has since been FIXED with an ISO fast-path in parse_deadline
itself (before dateutil runs). The former xfail is now a hard regression
test. For 2026-09-15 the flip would yield month=15, invalid, so dateutil
resolves it correctly — that case must parse as September 15, never
swapped.
"""
from datetime import datetime

import pytest

from app.knowledge.deadlines import best_deadline, parse_deadline

TODAY = datetime(2026, 9, 9)


def test_iso_date_parses_september_not_swapped():
    d = parse_deadline("2026-09-15", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 9, 15)
    assert d.confidence >= 0.8
    assert d.raw == "2026-09-15"


def test_iso_date_ambiguous_components_not_flipped():
    """Regression (was an xfail documenting the dateutil dayfirst bug):
    both trailing numbers are valid months. June 1 2026 is past relative
    to `today`, so the rolling-year policy must return 2027-06-01 — not
    January 6."""
    d = parse_deadline("2026-06-01", today=TODAY)
    assert (d.date.year, d.date.month, d.date.day) == (2027, 6, 1)
    assert d.confidence >= 0.8


def test_iso_date_invalid_components_fall_through():
    # 2026-13-45 is not a real date: must stay unparseable (kept as text),
    # not crash and not silently reinterpret.
    d = parse_deadline("2026-13-45", today=TODAY)
    assert d.date is None


def test_iso_date_embedded_in_sentence():
    # embedded ISO is unambiguous — must parse as October 1, not dayfirst-
    # flipped to Jan 10 (the quirk that motivated the ISO fast-path)
    d = parse_deadline("apply by 2026-10-01", today=TODAY)
    assert d.date is not None
    assert (d.date.year, d.date.month, d.date.day) == (2026, 10, 1)
    assert d.confidence >= 0.8


def test_ambiguous_slash_dates_keep_dayfirst_policy():
    # genuinely ambiguous D/M strings keep the project's dayfirst choice
    d = parse_deadline("15/09/2026", today=TODAY)
    assert (d.date.month, d.date.day) == (9, 15)


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


@pytest.mark.parametrize("text", ["5th of October", "October 5th", "CLOSES OCT 5"])
def test_education_deadline_surface_forms_parse_same_date(text):
    d = parse_deadline(text, today=TODAY)
    assert d.date is not None
    assert d.date.date() == datetime(2026, 10, 5).date()


def test_empty_input_is_unparseable_and_safe():
    d = parse_deadline("", today=TODAY)
    assert d.date is None and d.reminder_date is None
    assert d.confidence == 0.0
    assert "unparseable" in d.note


def test_a_number_that_is_not_a_year_never_becomes_a_deadline():
    """Regression, measured on the owner's own library: reel 29's OCR reads
    `INVOICE 4500 DUE 15 SEPTEMBER`. dateutil runs `fuzzy=True`, so it took the
    amount as the year and returned 4500-09-15 at confidence 0.85 — which
    `best_deadline` would have stored and handed the reminder scanner
    4500-09-10. A hint word is what puts a sentence in front of the parser at
    all, so the parse must refuse on its own, not rely on the scanner."""
    line = "Deadline for the payment: invoice 4500 due 15 September"
    d = parse_deadline(line, today=TODAY)
    assert d.date is None and d.reminder_date is None
    assert "outside plausible window" in d.note
    assert best_deadline([], line, today=TODAY) is None


def test_an_amount_below_this_year_is_not_promoted_into_a_deadline():
    """The harder half of the same bug. The rolling-year policy rewrites a past
    date into next year, so a window that looked at the *rewritten* year let
    "invoice 2000" through as 2027-09-15 at confidence 0.85 — and unlike 4500 that
    reminder is reachable, so it would have fired. The window is checked against
    the year the source actually stated."""
    line = "Deadline for the payment: invoice 2000 due 15 September"
    d = parse_deadline(line, today=TODAY)
    assert d.date is None and d.reminder_date is None
    assert "outside plausible window" in d.note
    assert best_deadline([], line, today=TODAY) is None


def test_a_date_the_text_does_not_state_is_not_parsed():
    """`fuzzy=True, default=today` makes dateutil fill any field the text left out,
    and the month-name branch had the same hole from the other side. Each of these
    used to produce a real date at confidence 0.75-0.85 plus a reminder:

      "the deadline is 2029"                    -> 2029-09-09  (today's month and day)
      "applications close September 2026"       -> 2026-09-20  (day 20 cut out of 2026)
      "deadline 2029 see remarks"               -> 2029-09-09  ("mar" inside "remarks")
      "apply by 22nd"                           -> 2026-09-22  (month from today)

    §8 keeps an ambiguous date as text, so a parse needs the day and the month to be
    in the sentence, not in the defaults."""
    for text in ("the deadline is 2029", "apply by 2030", "last date 1999",
                 "the deadline was 2019", "applications close September 2026",
                 "closing in October 2027", "apply by 22nd",
                 "deadline 2029 see remarks", "the deadline is 2029, may vary",
                 "you may apply by 2029", "2026 September"):
        d = parse_deadline(text, today=TODAY)
        assert d.date is None, text
        assert d.reminder_date is None, text


@pytest.mark.parametrize("text", [
    "deadline: 15 09 2026",      # day and month numerically, spaces only
    "deadline 15 09 26",         # two-digit year
    "apply by 15.09.2026",       # dotted separator
    "last date 5.9",
    "deadline 31st March",       # ordinal + month name
    "expiry 20261015",           # compact stamp, common in form screenshots
    "1 October 2026",
])
def test_a_stated_day_and_month_still_parses(text):
    """The gate must be a gate, not a ban: every form that does state a day and a
    month still reaches the parser."""
    assert parse_deadline(text, today=TODAY).date is not None


@pytest.mark.xfail(reason="residual, recorded in docs/EVAL.md: a duration can read as "
                          "the day-and-month pair the gate asks for, so "
                          "'deadline 2029 in 1-2 days' still yields 2029-02-01. "
                          "Distinguishing a pair from a range needs a unit-aware "
                          "scan, not a wider regex.", strict=False)
def test_a_duration_is_not_a_day_and_month_pair():
    assert parse_deadline("deadline 2029 in 1-2 days", today=TODAY).date is None


@pytest.mark.parametrize("text,expect_year", [
    ("closes 2036-05-05", 2036),   # inside the window: a real far-off deadline
    ("closes 2037-05-05", None),   # outside it: kept as text
    ("closes 2019-05-05", None),   # lower edge, and the ISO branch, not the gate
])
def test_the_year_window_is_a_window_not_a_ban(text, expect_year):
    d = parse_deadline(text, today=TODAY)
    if expect_year:
        assert d.date is not None and d.date.year == expect_year
    else:
        assert d.date is None and "outside plausible window" in d.note
