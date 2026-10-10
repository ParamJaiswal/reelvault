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


def test_the_year_window_rejection_is_terminal_rather_than_passed_on():
    """The obvious reading of `INVOICE 4500 DUE 15 SEPTEMBER` is that the window rejection is
    throwing away a date the line does state — `15 SEPTEMBER` is right there — and that the
    month-name branch should get a turn after the numeric branch fails it. That was built and
    measured on this library: the fall-through recovers the invoice line and invents
    `2027-08-02 @ 0.75` out of `arXiv:1706.03762v7 [cs.CL] 2 Aug 2023`, because an implausible
    stated year is the only evidence on that line that it is a submission stamp rather than a
    deadline, and keeping its day and month while dropping its year is precisely the guess §8
    forbids. Papers are the dominant v2 source and every one carries that stamp, so the corpus
    price was one real date recovered and one fabrication added. The rejection stays terminal;
    the invoice's deadline is an accepted recall loss for the UI correction path to handle."""
    for line in ("Deadline for the payment: invoice 4500 due 15 September",
                 "arXiv:1706.03762v7  [cs.CL]  2 Aug 2023"):
        d = parse_deadline(line, today=TODAY)
        assert d.date is None and d.reminder_date is None, line


@pytest.mark.parametrize("text", [
    "deadline may 11.5pm",         # a decimal followed by a clock suffix: the unit lookahead
    "deadline june 12.30pm",       # spans whitespace only, so it never saw the `pm` past the
    "deadline august 9.30am",      # dot and dateutil supplied the day from `today`
    "may11.5pm",                   # glued, the way an OCR line arrives
    "v2.5 march release",          # a version number, not the 2nd of March
    "deadline 2.5april",           # glued the same way, where `25august` must still survive
    "3.15 september",              # a section number, not the 3rd of September
])
def test_a_decimal_does_not_state_a_day(text):
    r"""`(?!\d)` at the end of a day capture stops digits, not a decimal point, so `11` inside
    `11.5pm` counted as a stated day and the first two cases below came back 2027-05-09T23:00,
    2027-06-09T12:00 and 2027-08-09T09:00 at confidence 0.85 — three reminders on times of day.
    The guard sits on both sides of the day, `(?!\.\d)` and `(?<!\d\.)`, because `v2.5 march`
    and `3.15 september` put the fraction where the day would otherwise be read. Measured on the
    corpus: no sentence changes either way, so this is not a recall trade, and every form that
    states a whole day next to a month (`Apply by Sept. 15`, `the 3rd of April 2027`,
    `closes 3.4 march`) still parses. What it does not reach is a decimal rescued by a hint word
    through the bare-pair clause — `deadline 11.5 may` -> 2027-05-11 — which is the same
    hint-riding-number residual as `3.5 august deadline`."""
    assert parse_deadline(text, today=TODAY).date is None


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
    "apply by the 3rd of April 2027",   # ordinal, `of`, and a year: the decimal guard's edge
    "expiry 20261015",           # compact stamp, common in form screenshots
    "1 October 2026",
    "closes 3 Sep",              # bare alias, no year
    "deadline August25,2026",    # OCR collapses word boundaries: 2 of 144 live rows
    "deadline Sep30,2026",
    "deadline 25august",         # ... in either order
])
def test_a_stated_day_and_month_still_parses(text):
    """The gate must be a gate, not a ban: every form that does state a day and a
    month still reaches the parser."""
    assert parse_deadline(text, today=TODAY).date is not None


@pytest.mark.parametrize("text", [
    "6 separate steps",             # sep -> September 6
    "3 decimal places",             # dec -> December 3
    "5 novel ideas",                # nov -> November 5
    "3 junior developers",          # jun -> June 3
    "2 markdown notes",             # mar -> March 2
    "5 augmented examples",         # aug -> August 5
    "@kevinnaughtonjr 1.",          # live OCR, reel 8 row 40: `aug` inside `nAUGHTONJR`
    "@kevinnaughtonjr 3.An inboxtriagebot",   # row 41, same reel
    "12 increments", "20 minutes",  # never parsed: a bare count is not a day either
])
def test_a_word_that_only_starts_with_a_month_alias_is_not_a_month(text):
    """The month-name branch matched `[A-Za-z]{3,9}` and then looked the token up by its
    first four and first three letters, so "<digit> <word>" became a date whenever the
    word merely *began* with an alias. Screening all 4,140 stored sentences of the live
    library plus the golden set found two doing it, both on reel 8's OCR —
    `@kevinnaughtonjr 1.` read as 2027-08-01 at confidence 0.75.

    The shapes are bare on purpose. With a hint word in front, the old branch matched
    that word first ("deadline 3") and its prefix lookup missed, so the same sentence
    returned None at HEAD for the wrong reason and the test would have passed with the
    bug still in. And 0.75 is not a harmless floor: `best_deadline` boosts a quote parse
    to the fact's own confidence, so a misread outranks a corroborated value-only parse
    (0.5) and is the value written to `deadline_iso` with a reminder attached."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "you may apply before 11pm",     # `may` is a month, 11 is an hour
    "submissions close 5pm in may",
    "apply by 6pm before june",
    "size 12kb in may",              # 12 counts bytes
    "last date 20rs in july",        # 20 is money
    "August2026 meeting, closes 5",  # the year is glued; the lone digit is unrelated
])
def test_a_digit_glued_to_a_letter_is_not_a_stated_day(text):
    """Relaxing the gate's day rule from `\b` to "not a digit" so `August25` supplies 25
    also let `11pm` and `20rs` supply a day while `may`/`june` supplied the month, and
    dateutil filled whatever was left from `today` — "you may apply before 11pm" came
    back 2027-05-09 at 0.85 with a reminder on text that states no date. The collapse is
    therefore allowed only as a *pair* (`_GLUED_MONTH_DAY`), never as two hits that
    happen to share a sentence."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "deadline 12:30 in june",        # 12 and 30 are hours and minutes
    "deadline june 12:30",
    "deadline 12:30 september",
    "[00:15] OCR: deadline in june",  # the pipeline's own prefix, seen as a clock
    "[01:05] deadline in june",
    "[00:45] OCR: last date in october",
])
def test_a_clock_token_does_not_state_a_day(text):
    """`deadline 12:30 in june` returned 2027-06-09T12:30 at confidence 0.85: the month
    from `june`, the hour from the clock, and the **day from `today`** — because the gate's
    day test used to count `12` and `30` as standalone numbers and dateutil then filled the
    field the text never stated. §8's rule is the same one that killed the bare-year case, and
    the colon guards are what hold it: a number touching a colon states minutes, not a day, in
    either position."""
    assert parse_deadline(text, today=TODAY).date is None


def test_a_time_written_in_the_source_is_still_a_time():
    """The fix must not throw away a deadline's real hour: what is stripped is ReelVault's
    own `[MM:SS]` line marker, not a time the content states. `at 12:30` survives the
    prefix strip, and the prefix itself no longer rides into the stored date."""
    d = parse_deadline("[01:20] deadline 15 September at 12:30", today=TODAY)
    assert (d.date.month, d.date.day, d.date.hour, d.date.minute) == (9, 15, 12, 30)
    d = parse_deadline("[00:15] OCR: CLOSES OCT 5", today=TODAY)   # edu-03's saved quote
    assert (d.date.month, d.date.day, d.date.hour, d.date.minute) == (10, 5, 0, 0)


@pytest.mark.parametrize("text", [
    "5.4 Regularization",                      # a paper section number
    "3.1 Encoder and Decoder Stacks",
    "and use them with cheaper open source models like GLM 5.2",   # a model version
    "REVERSE 03/07 ENGINEERING DOJO",          # a slide counter
    "ROUND 1/4 - 40s WORK / 20s REST",         # a ratio
    "(3.5 days).",                             # a duration
    "(E) positional embedding instead of sinusoids 4.92 25.7",    # measurements
])
def test_a_bare_pair_is_a_date_only_when_something_says_it_is(text):
    """`_NUMERIC_DATE_SHAPE` used to accept `<1-2 digits>[/.-]<1-2 digits>` as proof of a
    day and a month, which is true of `5.9` and equally true of `3.5 days`, `GLM-5.2`,
    `03/07` and every section number in the two paper rows. Screening the corpus found
    **24 such sentences** parsed to a confident date, all of them content; a bare pair now
    counts only with a `DEADLINE_HINTS` word in the same text. Forms that carry a third
    numeric component (`15/09/26`, `15 09 2026`) or name a month (`August25,2026`) still
    parse on their own shape — which is shape evidence, not semantic proof: `3 5 20
    minutes apart` rides through, and it is named in the residual list rather than hidden.

    The ceiling is deliberate and it is a recall one: `registrations 12/09` with no year
    and no hint word stays text. §8 prefers text over a guess, so the test pins both
    sides of that line rather than only the half that looks like progress."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "registrations 12/09",                     # the ceiling: no year, no hint word
    "the form shows 12/09",
])
def test_a_year_less_hint_less_pair_is_kept_as_text(text):
    """Named separately from the bug above because it is not one: this is what the gate
    gives up. If a future change makes these parse, that is a policy decision made in
    passing, not a fix."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "apply by 15/09/26",                 # two-digit year: a triple, not a bare pair
    "closes 3.9.26",
    "12-09-2026",
    "LAST DATE12/09",                    # OCR glues the label to the pair, and it is a pair
])
def test_a_separated_triple_states_a_year_and_needs_no_hint(text):
    """The pair gate must not swallow real dates on the way to the section numbers. A
    first version of it required a `19xx/20xx` run *anywhere* in the sentence instead of
    requiring the year inside the shape, which both refused `15/09/26` (a complete date,
    no cue) and admitted `2026 GLM 5.2` (a stray year next to a version). Adjacency is
    the whole distinction, so both directions are pinned here."""
    assert parse_deadline(text, today=TODAY).date is not None


# A slice of reel 16's stored OCR row 95 (`ocr_results`, t_s 9.0), where the source carries
# no spaces at all.
_COLLAPSED_OCR_RUN = ("comingtotheAnthropicAcademyon July13.Checkbackthen,orreview "
                      "theexamguideabovetostart preparingnow")


def test_a_glued_month_and_day_parses_alone_but_not_inside_a_collapsed_ocr_run():
    """The year is not what `July13.` needs: `_GLUED_MONTH_DAY` opens the gate by itself
    (no hint word, no month-word-plus-day) and dateutil separates the month from the day,
    giving 2027-07-13 @ 0.85 through the rolling-year policy. The same date inside a run
    glued on **both** sides still returns None, because dateutil raises ParserError on the
    whole token. That is tokenization, so the remedy is an OCR-aware split; a wider regex
    that made the stored sentence parse would be inventing word boundaries the source never
    had, which is why the refusal is pinned as firmly as the parse."""
    d = parse_deadline("July13.", today=TODAY)
    assert d.date == datetime(2027, 7, 13) and d.confidence == 0.85
    assert parse_deadline(_COLLAPSED_OCR_RUN, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "closes at 5.30pm sharp",            # `closes` is a hint; 5.30 is a time written with a dot
    "deadline 3.5km",
    "deadline 12.5hrs",
    "2026 GLM 5.2",                      # a year that belongs to nothing
    "batch 2030 mAh, ratio 1/4",
    "the 2026 report shows 4.92 growth",
    "Table 1/4 (2026)",
    "ROUND 1/4 - 40s WORK / 20s REST",   # two different separators is not a date triple
])
def test_a_stray_year_is_not_a_date_cue_and_a_unit_is_not_a_day(text):
    """Every case here parsed as a confident date at some point during this change:
    `5.30pm`/`3.5km`/`12.5hrs` because the pair's right edge checked for digits only, and
    the four stray-year ones because a 19xx/20xx run anywhere in the text was treated as
    evidence the pair was a date. Papers state years constantly, so position-blindness
    re-opened the exact family the gate exists to close."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "deadline feb 30", "deadline 30 feb", "deadline 31 april", "deadline june 31",
])
def test_an_impossible_date_is_kept_as_text_instead_of_raising(text):
    """`today.replace(month=2, day=30)` raises, and `app/pipeline/stages.py:509` calls
    this with OCR and model text inside no try — one such line failed the whole
    extraction stage. Month-first order crashed at HEAD; day-first joined it as soon as
    the branch stopped swallowing "deadline 30" as a month name. §8's answer to a date
    the text cannot mean is text, not an exception."""
    d = parse_deadline(text, today=TODAY)
    assert d.date is None and d.reminder_date is None
    assert "unparseable" in d.note


@pytest.mark.parametrize("text", [
    "deadline 2029 in 1-2 days",           # a span of days, not a day and a month
    "deadline 3.5 days",
    "closes in 2-3 weeks",
    "the store closes 5.4 times a week",   # a frequency, and `closes` is a hint
    "the store closes 5.4 time a week",    # the same word singular: the list is `times?`
    "deadline 3.5 secs",                   # the plural abbreviations stay refused…
    "deadline 5.9 mins",                   # …and the singular ones are deliberately absent
    "3 5 20 minutes apart",                # the spaced triple, read as a date until now
    "takes 1-2-2026 weeks",                # the separated triple, same reason
    # The same five in the case this library's OCR actually arrives in. The unit lookahead
    # sits in patterns compiled without re.I, so uppercase is not a formatting nit: every one
    # of these still parsed when the rule was written lowercase-only.
    "DEADLINE 3.5 DAYS", "CLOSES IN 2-3 WEEKS", "THE STORE CLOSES 5.4 TIMES A WEEK",
    "3 5 20 MINUTES APART",
])
def test_a_number_run_before_an_elapsed_time_unit_is_not_a_date(text):
    """These were the standing residuals in docs/EVAL.md, and they all needed the same thing
    to notice: the word after the numbers. A hint clause cannot tell `deadline 3.5 days` from
    `deadline 3.5` — the unit is the only evidence that one is a length of time. Refusing a
    numeric run that abuts a unit costs no real form, because a month is not in the unit list
    (`closes 3.4 march` still parses at 2027-03-03, and it is the case that proves the escape,
    where `closes 12-13 march` returns None under both rules and proves nothing). The residual
    this leaves is a decimal read as a day *without* a unit after it: `3.5 august deadline` ->
    2027-08-03, which needs sense about the number rather than a rule about its neighbours."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "Apply by Sept. 15",              # the abbreviation's period is punctuation, not a word
    "Deadline: Oct. 5, 2026",
    "register by Nov. 30",
    "closes Dec. 31",
    "applications close Oct. 20th",
    "15 Sept.",                       # and in the other order, with the dot at the end
])
def test_a_month_abbreviation_still_abuts_its_day(text):
    """The first version of the adjacency rule required whitespace straight after the month
    token, so `Sept. 15` had a dot in the way and stopped parsing — a recall loss against the
    rule it replaced, on the single most common written form of an abbreviated deadline. Pinned
    because the corpus cannot show it: no stored sentence uses a dotted abbreviation, which is
    exactly how this diff shipped it once already.

    The confidence is the assertion, not the date. The month-name branch reads `Sept. 15`
    whichever way the gate is configured, so `date is not None` passes even when the gate
    refuses the form; 0.85 is what says dateutil reached it, which is what the rule this
    replaced did."""
    d = parse_deadline(text, today=TODAY)
    assert d.date is not None and d.confidence == pytest.approx(0.85)


@pytest.mark.parametrize("text", [
    "deadline may 11pm",         # an hour after a month word, in both orders
    "deadline 11pm may",
    "closes sept 5pm",
    "deadline may11pm",          # glued, the way an OCR line arrives
    "june 30mins left",          # a duration, and no hint word to hide behind
    "DEADLINE MAY 11PM",         # uppercase: the lookahead compiles with re.I
])
def test_a_clock_or_unit_after_a_month_name_supplies_no_day(text):
    r"""The elapsed-unit lookahead started out only on the numeric shapes, and the month-name
    positions kept reading a clock as a day: `deadline may 11pm` gave 2027-05-11 at 0.85
    through the adjacency gate and again at 0.75 through the month branch when the gate was
    closed, because both take `\d{1,2}` and stopped at `(?!\d)`. A day needs the same neighbour
    check a pair needs."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "deadline 12/09 Sec.",         # `Sec.` is a section, and the pair beside it is the date
])
def test_a_section_abbreviation_is_not_an_elapsed_time_unit(text):
    r"""The unit list is `secs` and `mins`, not `secs?` and `mins?`, and the difference is the
    two sources v0.2.0 ingests: `sec` is a section and `min` is a minimum, both constant in a
    paper, while a bare `3.5 sec` duration in deadline text is not. With the optional `s` the
    list swallowed them, and the review that found it had to point at `deadline 15/09/2026
    sec 3` to show what the rule was eating — though the honest version of that finding is that
    dateutil then refuses those two forms for its own reasons (a stray `500` read as a year, a
    trailing token it cannot parse), so they returned text at HEAD as well. The measurable
    difference is the pair-plus-abbreviation above, which has to parse and does."""
    assert parse_deadline(text, today=TODAY).date is not None


@pytest.mark.parametrize("text", [
    "deadline is the 30th of next month, in October",
    "the deadline is on the 5th, in September",
    "Apply by the 15th, September 2026",      # the same class, not the same sentence
    "closes June 2026, on the 30th",
    "[11] There may be some very unworldly people who do great work without consciously "
    "trying",
    "[29] It may be helpful always to walk or run the same route, because that frees "
    "attention",
])
def test_a_month_and_a_day_that_do_not_abut_are_kept_as_text(text):
    """The ceiling the owner accepted on 2026-10-10, stated as a pair of cases because it
    closes both an invention and a real date, and pretending otherwise would read as
    progress. The first are genuine deadlines in word orders the stored library and the golden
    set never use, so they now stay text for a user to correct in the UI — and they are a
    *class*, not a list: every month-and-day that does not touch is refused, four instances are
    pinned here because two were named at adoption and two more turned up when review asked how
    wide the class actually was. The last two are the inventions, which are the whole reason for
    the rule — `may` is in the month vocabulary and a bracketed list index is a standalone
    number, and the two of them were the last dates the corpus screen still found. Nothing that
    abuts changes: `the 15th of September`, `5th of October` and `October 5th` all still
    parse."""
    assert parse_deadline(text, today=TODAY).date is None


@pytest.mark.parametrize("text", [
    "due 15 September",              # `due` selects the sentence; the shape makes the date
    "due 15/09/2026",
    "due date 12/09",
    "DUE15SEPTEMBER",
    "the last date is September 15",
])
def test_due_selects_a_sentence_without_making_a_bare_pair_a_date(text):
    """Owner decision 2026-10-10 admitted `due` to `DEADLINE_HINTS`, and the hint list does
    double duty — it selects sentences for the rules scan *and* rescues a bare numeric pair.
    For `due` only the first is safe: it is ordinary prose in invoices and papers, and as pair
    evidence it invented a dated reminder out of `Due to the 3.2% increase in volume`,
    `Balance due of 4.5 is shown in table 2` and `no amount due 03/07 remains` — none of which
    the corpus screen could see, because none of its 2,638 sentences puts `due` beside a pair.
    So `due` is a cue, `due date` is a cue strong enough to rescue a pair, and a bare `due
    12/09` stays text until something can tell an amount from a date."""
    assert parse_deadline(text, today=TODAY).date is not None


@pytest.mark.parametrize("text", [
    "due 12/09",                                  # the ceiling the cue carve-out leaves
    "Due to the 3.2% increase in volume, the invoice is unchanged.",
    "Balance due of 4.5 is shown in table 2.",
    "Due to demand, GLM 5.2 shipped.",
    "no amount due 03/07 remains",
    "Due to the 1-2 gap in the series",
])
def test_due_is_ordinary_prose_and_supplies_no_date(text):
    """The invention class `due` would have opened, recorded as refusals rather than as a
    footnote in `docs/EVAL.md`: each of these parsed to a confident date with a reminder
    attached when the cue list was used for the pair rescue. The first case is the honest cost
    — an invoice that says only `due 12/09` is kept as text — and the rest are the reason."""
    assert parse_deadline(text, today=TODAY).date is None


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
