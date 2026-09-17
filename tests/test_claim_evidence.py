"""Claim-support regressions captured during Phase 7 trace review.

Root cause: a fabricated value ("Zylker") attached to a genuine but
unrelated quote scored similarity 1.0 — the quote matched the span, so
nothing checked whether the span actually contains the claim's terms.
The fix requires every meaningful claim term to appear in the matched span.
This is a lexical prerequisite, not a semantic entailment guarantee.
"""
import pytest

from app.knowledge.evidence import HALLUCINATION_THRESHOLD, SourceSpan, find_evidence


def test_genuine_networking_quote_cannot_support_fabricated_company():
    quote = ("I'm going to simplify networking for you and tell you how to "
             "mine your LinkedIn connections")
    match = find_evidence(quote, "Zylker", [SourceSpan(quote, 0.0, "transcript")])
    assert match.similarity < HALLUCINATION_THRESHOLD
    assert match.span is None
    assert match.n_sources_agreeing == 0


@pytest.mark.parametrize("value,quote", [
    # names, numbers (word/digit forms), dates, reordered terms, abbreviations
    ("Zylker", "Zylker is hiring data analysts"),
    ("25,000", "The stipend is twenty five thousand per month"),
    ("September 15", "Apply before September 15"),
    ("September 15", "Apply before September fifteenth"),
    ("November 30", "Applications close on NOV 30"),
    ("25k per month", "Stipend is twenty five thousand per month"),
    ("0-1 years", "This role requires zero to one years of experience"),
    ("September 1", "Apply before September first"),
    ("connections in LinkedIn", "Download your LinkedIn connections"),
    ("₹8-10 LPA", "Salary package 8 to 10 lakh per year for freshers"),
])
def test_supported_names_numbers_dates_and_reordered_terms_survive(value, quote):
    span = SourceSpan(quote, 12.0, "transcript")
    match = find_evidence(quote, value, [span])
    assert match.similarity >= HALLUCINATION_THRESHOLD
    assert match.span is span


@pytest.mark.parametrize("value", ["30,000", "September 16", "Zylker hiring"])
def test_genuine_quote_does_not_support_changed_numbers_or_added_names(value):
    quote = "Hiring analysts with a stipend of 25,000; apply before September 15"
    match = find_evidence(quote, value, [SourceSpan(quote, 1.0, "transcript")])
    assert match.similarity < HALLUCINATION_THRESHOLD


def test_support_cannot_be_pooled_across_spans():
    quote = "Download your LinkedIn connections"
    spans = [SourceSpan(quote, 0.0, "transcript"),
             SourceSpan("Meet the team at Zylker", 20.0, "transcript")]
    match = find_evidence(quote, "Zylker connections", spans)
    assert match.span is None


def test_edu05_conservative_claim_acceptance():
    from tests.test_ai_eval import load_golden, _unified_and_spans

    golden = next(g for g in load_golden() if g["id"] == "edu-05")
    _, spans = _unified_and_spans(golden)
    quote = golden["transcript"][0]["text"]
    supported = find_evidence(quote, "LinkedIn networking", spans)
    assert supported.similarity >= HALLUCINATION_THRESHOLD
    assert supported.span.text == quote
    assert supported.span.t_s == 0.0
    for value in ("Zylker", "Networking Simplification"):
        rejected = find_evidence(quote, value, spans)
        assert rejected.similarity < HALLUCINATION_THRESHOLD
        assert rejected.span is None
