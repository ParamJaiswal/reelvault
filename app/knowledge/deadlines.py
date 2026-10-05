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
# Letter-bounded month token, used by the month-name branch below. A month is a month
# only when letters do not continue it, so `separate` supplies no `sep`.
_MONTH_TOKEN = rf"(?<![a-zA-Z])(?:{_MONTH_ALT})(?![a-zA-Z])"
_MONTH_WORD = re.compile(rf"\b(?:{_MONTH_ALT})\b", re.I)
# `\b` is what stops "2026" from supplying the day 20, and it must stay a word boundary
# rather than a digit-only guard: `20rs`, `11pm` and `August25` all adjoin a letter, and
# only the glued pair below may read those as a day. The colon guards keep a clock token
# out of both roles — `12:30` states minutes, not a day — which is what let
# "deadline 12:30 in june" come back 2027-06-09T12:30, the month from `june`, the day
# from `today`, the hour from a clock.
_STATED_DAY = re.compile(r"(?<!:)\b\d{1,2}(?:st|nd|rd|th)?\b(?!:)")
# OCR collapses word boundaries, so a real date arrives as `August25,2026` or
# `DUE15SEPTEMBER`, where the two `\b` rules above both fail: `\b` needs a non-word
# character, and the month's own first digit is one. This is the only relaxation, and it
# stays a *pair* rule — month directly abutting day, either order — because widening the
# two independent tests instead is what let `may` plus `11pm` invent May 9 at 0.85, and
# let `August2026 … closes 5` invent August 5. A 4-digit run still supplies no day.
_GLUED_MONTH_DAY = re.compile(
    rf"(?<![a-zA-Z])(?:{_MONTH_ALT})(?![a-zA-Z])(?<!\d)\d{{1,2}}(?!\d)(?!:)"
    rf"|(?<![:\d])\d{{1,2}}(?!\d)(?<![a-zA-Z])(?:{_MONTH_ALT})(?![a-zA-Z])", re.I)
# `[MM:SS]` and `[MM:SS] OCR:` are what the pipeline itself writes in front of every
# transcript and OCR line (`app/pipeline/stages.py:381-382`), so they reach this parser
# twice over: inside a rules-fallback candidate, and inside a model quote kept verbatim
# from a prefixed span. dateutil reads them as a clock and puts that hour on the stored
# deadline. Only our own marker, and only at line start — a time written in the source
# content is content and stays.
_LINE_MARKER = re.compile(r"(?:^|\n)\[\d{1,2}:\d{2}\](?:\s*OCR:)?[ \t]*")
# These state a year as part of the shape itself: a spaced triple, a separated triple
# (15/09/2026 and the two-digit-year 15/09/26), and the 8-digit form stamp. The separated
# triple backreferences its own separator — letting either slot be any of `/.-` admitted
# "ROUND 1/4 - 40s WORK" as a date, measured on the corpus.
_YEAR_BEARING_SHAPE = re.compile(
    r"\b\d{1,2}\s+\d{1,2}\s+\d{2,4}\b"                    # 15 09 2026, 15 09 26
    r"|(?<!\d)\d{1,2}([/.-])\s*\d{1,2}\s*\1\s*\d{2,4}(?!\d)"   # 15/09/2026, 12-09-26, 3.9.26
    r"|(?<!\d)\d{8}(?!\d)")                               # 20261015, form screenshots
# A bare pair, on its own, says nothing: `5.9` is a date and so is `3.5 days`,
# `GLM-5.2`, `4.92 25.7`, `03/07` and `1/4`. Screening the stored corpus found 24
# sentences whose only numeric evidence is a pair, every one of them a section number,
# version, ratio, duration or measurement, each parsed to a confident date. So a pair
# counts only when a deadline hint is in the same text — the ceiling is that `12/09`
# without one is kept as text.
# A year elsewhere in the sentence is NOT a cue: measured, `"2026 GLM 5.2"`,
# `"Table 1/4 (2026)"` and `"the 2026 report shows 4.92 growth"` all parse to a date
# once any 19xx/20xx run is treated as evidence, and papers state years constantly. The
# year has to be part of the shape, which is what `_YEAR_BEARING_SHAPE` above is for.
# Right edge rejects letters, not just digits: `5.30pm`, `3.5km` and `12.5hrs` abut a
# unit, and dateutil turns `5.30` into a day-and-month with the day from `today`. The
# LEFT edge stays digit-only, because OCR glues a collapsed label to the date it
# precedes — `LAST DATE12/09` states a real pair and must survive.
_PAIR_SHAPE = re.compile(r"(?<!\d)\d{1,2}\s*[/.-]\s*\d{1,2}(?![\da-zA-Z])")  # 15/09, 5.9


def _states_day_and_month(text: str) -> bool:
    if _YEAR_BEARING_SHAPE.search(text) or _GLUED_MONTH_DAY.search(text):
        return True
    if _PAIR_SHAPE.search(text) and any(h in text.lower() for h in DEADLINE_HINTS):
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
    raw = _LINE_MARKER.sub(" ", raw).strip()
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
        # Both halves of this rule are load-bearing. The digit and colon guards stop
        # "applications close September 2026" from cutting 20 out of the year and
        # "close September 12:30" from cutting 12 out of a clock — the same invention the
        # numeric gate exists to stop, arriving through the other branch. The closed month
        # vocabulary stops "6 separate steps" from cutting `sep` out of `separate`, which
        # the old `[A-Za-z]{3,9}` plus a 3-letter prefix lookup read as September 6.
        m = re.search(
            rf"(?<![:\d])(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_TOKEN})|"
            rf"({_MONTH_TOKEN})\s+(?<![:\d])(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?!:)",
            raw, re.I)
        month = day = None
        if m:
            if m.group(2):
                day, mon_name = int(m.group(1)), m.group(2)
            else:
                mon_name, day = m.group(3), int(m.group(4))
            month = (MONTHS.get(mon_name.lower())
                     or MONTH_ALIASES.get(mon_name.lower()))
        if month and 1 <= day <= 31:
            try:
                dt = today.replace(month=month, day=day)
            except ValueError:
                # An impossible date the text really did state — Feb 30, Apr 31, or
                # Feb 29 in a non-leap year. It has to come back as unparseable text:
                # `app/pipeline/stages.py:509` feeds this OCR and model output with no
                # guard, so a raise here failed the whole extraction stage.
                dt = None
            else:
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
