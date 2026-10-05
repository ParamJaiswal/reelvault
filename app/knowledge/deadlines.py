"""Deadline intelligence — deterministic date handling.

The LLM only *suggests* a deadline string; this module is the sole authority
for parsing, normalizing, validating and computing reminder dates.
No model ever does arithmetic here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

try:
    from dateutil import parser as _du_parser
except Exception:  # pragma: no cover - optional dep
    _du_parser = None

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
MONTH_ALIASES = {"sept": 9, "sep": 9, "jan": 1, "feb": 2, "mar": 3, "apr": 4,
                 "jun": 6, "jul": 7, "aug": 8, "oct": 10, "nov": 11, "dec": 12}

# dateutil is called with `fuzzy=True, default=today`, so it fills in every field the
# text left out: a bare year came back as a real date carrying today's month and day,
# and a bare month came back carrying today's day — both at confidence 0.85, with a
# reminder. Trust the numeric path only when the text states a day *and* a month, since
# those are the two fields dateutil would otherwise invent. §8 keeps an ambiguous date
# as text.
_MONTH_ALT = "|".join(sorted({*MONTHS, *MONTH_ALIASES}, key=lambda w: (-len(w), w)))
_MONTH_WORD = re.compile(rf"\b(?:{_MONTH_ALT})\b", re.I)
# `\b` is what stops "2026" from supplying the day 20; an ordinal suffix is a stated day.
_STATED_DAY = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\b")
# These carry a day and a month together, so no month word is needed.
_NUMERIC_DATE_SHAPE = re.compile(
    r"\b\d{1,2}\s*[/.-]\s*\d{1,2}\b"          # 15/09, 5.9, 15-09
    r"|\b\d{1,2}\s+\d{1,2}\s+\d{2,4}\b"       # 15 09 2026, 15 09 26
    r"|(?<!\d)\d{8}(?!\d)")                   # 20261015, common in form screenshots


def _states_day_and_month(text: str) -> bool:
    if _NUMERIC_DATE_SHAPE.search(text):
        return True
    return bool(_MONTH_WORD.search(text) and _STATED_DAY.search(text))

DEADLINE_HINTS = ("deadline", "last date", "apply before", "apply by",
                  "register by", "register before", "closes on", "closes",
                  "submit before")


@dataclass
class Deadline:
    raw: str
    date: datetime | None
    reminder_date: datetime | None
    confidence: float
    source: str            # transcript | ocr | caption | rules
    note: str = ""


def parse_deadline(raw: str, *, today: datetime | None = None,
                   source: str = "transcript") -> Deadline:
    """Parse human deadline text into a concrete date (this year, rolling)."""
    today = today or datetime.now()
    raw = (raw or "").strip()
    if not raw or not any(ch.isdigit() for ch in raw):
        return Deadline(raw, None, None, 0.0, source,
                        note="unparseable; kept as text only")

    dt: datetime | None = None
    # The year as the source wrote it. The rolling-year policy below rewrites dt.year,
    # and a rewritten year must not be what the plausibility window checks: "invoice
    # 2000" parses to 2000, rolls to 2027, and would walk through looking legitimate.
    parsed_year: int | None = None
    conf = 0.5

    # 0) ISO YYYY-MM-DD anywhere in the string — dateutil's dayfirst flips
    #    these when both trailing numbers are valid months (2026-06-01
    #    parsed as Jan 6, "apply by 2026-10-01" as Jan 10), so handle the
    #    unambiguous format directly, embedded or standalone
    m_iso = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if m_iso:
        try:
            dt = datetime(int(m_iso.group(1)), int(m_iso.group(2)),
                          int(m_iso.group(3)))
        except ValueError:
            dt = None
        else:
            parsed_year = dt.year
            if dt < today - timedelta(days=7):   # rolled over to next year
                try:
                    dt = dt.replace(year=today.year + 1)
                except ValueError:
                    pass
            conf = 0.85

    # 1) explicit numeric formats via dateutil (2026-09-15, 15/09/2026...)
    if dt is None and _du_parser is not None and _states_day_and_month(raw):
        import warnings

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # unknown-tz noise from OCR
                dt = _du_parser.parse(raw, dayfirst=True, fuzzy=True,
                                      default=today)
            parsed_year = dt.year
            if dt < today - timedelta(days=7):   # rolled over to next year
                try:
                    dt = dt.replace(year=today.year + 1)
                except ValueError:
                    pass
            conf = 0.85
        except Exception:
            dt = None

    # 2) "September 15" / "15 September" / "Sept 20"
    if dt is None:
        # The lookarounds are the point: without them "applications close September
        # 2026" matched with day=20 taken out of the year, producing today's-era
        # September 20 at confidence 0.75 — the same invention the numeric gate above
        # exists to stop, arriving through the other branch.
        m = re.search(
            r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})|"
            r"([A-Za-z]{3,9})\s+(?<!\d)(\d{1,2})(?:st|nd|rd|th)?(?!\d)", raw)
        month = day = None
        if m:
            if m.group(2):
                day, mon_name = int(m.group(1)), m.group(2)
            else:
                mon_name, day = m.group(3), int(m.group(4))
            month = MONTHS.get(mon_name.lower()) or MONTH_ALIASES.get(
                mon_name.lower()[:4]) or MONTH_ALIASES.get(mon_name.lower()[:3])
        if month and 1 <= day <= 31:
            dt = today.replace(month=month, day=day)
            if dt < today - timedelta(days=7):   # rolled over to next year
                try:
                    dt = dt.replace(year=today.year + 1)
                except ValueError:
                    pass
            conf = 0.75

    if dt is None:
        return Deadline(raw, None, None, 0.25, source,
                        note="unparseable; kept as text only")

    # A year outside this window was never a deadline: §8 keeps an ambiguous date as
    # text rather than inventing one, and a reminder must never fire on a guess. The
    # bare *plausible* year with no month or day ("the deadline is 2029") still borrows
    # them from today, which is a narrower exposure recorded in docs/EVAL.md rather
    # than patched here.
    stated_year = parsed_year if parsed_year is not None else dt.year
    if not today.year - 1 <= stated_year <= today.year + 10:
        return Deadline(raw, None, None, 0.25, source,
                        note="year outside plausible window; kept as text only")

    note = ""
    if dt < today - timedelta(days=30):
        note = "date far in past; likely wrong year guess"

    return Deadline(raw, dt, reminder_for(dt), round(conf, 2), source, note)


def reminder_for(deadline_dt: datetime, days_before: int = 5) -> datetime:
    """Deterministic reminder: N days before, never after the deadline.

    If that computed moment is already past but the deadline is still
    ahead, clamp to now+1h so the user still gets a same-day heads-up
    instead of silence until the deadline day itself.
    """
    now = datetime.now()
    r = deadline_dt - timedelta(days=days_before)
    if r >= now:
        return r
    if deadline_dt <= now:
        return deadline_dt  # already past — the scanner skips it anyway
    return min(now + timedelta(hours=1), deadline_dt)


def extract_deadline_candidates(text: str) -> list[str]:
    """Cheap regex scan for sentences near deadline hint words."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        low = sent.lower()
        if any(h in low for h in DEADLINE_HINTS):
            out.append(sent.strip()[:200])
    return out


def best_deadline(facts: list[dict], unified_text: str,
                  today: datetime | None = None) -> Deadline | None:
    """Pick the highest-confidence parseable deadline among facts + text.

    A date parseable from the evidence quote is fully trusted and keeps
    the normal parse confidence (boosted by the fact's own confidence).
    B4: when the quote yields no date but the fact's normalized value
    does, the value is parsed anyway at reduced confidence (0.5) instead
    of being dropped.
    """
    best: Deadline | None = None

    def consider(d: Deadline) -> None:
        nonlocal best
        if d.date and (best is None or d.confidence > best.confidence):
            best = d

    for f in facts or []:
        field = (f.get("field") or "").lower()
        val = f.get("value") or ""
        if ("deadline" in field or "due" in field) and val:
            source = f.get("evidence_source", "transcript")
            quote = (f.get("evidence_quote") or f.get("quote") or "").strip()
            if quote:
                d = parse_deadline(quote, today=today, source=source)
                if d.date:
                    d.confidence = max(d.confidence,
                                       f.get("confidence", 0.5))
                    consider(d)
                    continue
            d = parse_deadline(val, today=today, source=source)
            if d.date:
                d.confidence = 0.5   # value-only parse: not corroborated
                consider(d)
    if best is None:
        for cand in extract_deadline_candidates(unified_text or ""):
            consider(parse_deadline(cand, today=today, source="rules"))
    return best
