"""Regression tests: short factual values verbatim in source are rescued
with designed weak support (0.75) instead of being auto-dropped by the
MIN_QUOTE_CHARS floor.

Root cause (reel 12, DcgcqXfSVrI): values "Zylker", "Bangalore", "0-2 yrs",
"September 15" are all under 15 normalized chars, so find_evidence returned
0.0 before the value-in-span path could fire — 4 real facts dropped despite
being verbatim in transcript/OCR.
"""
from app.knowledge.evidence import (HALLUCINATION_THRESHOLD, MIN_QUOTE_CHARS,
                                    SourceSpan, find_evidence)

SPANS = [
    SourceSpan("Zylker is hiring data analysts in Bangalore", 3.0, "transcript"),
    SourceSpan("Apply by September 15", 41.0, "ocr"),
    SourceSpan("Freshers 0-2 yrs can apply", 12.0, "ocr"),
]


def test_short_value_in_transcript_span_is_rescued():
    m = find_evidence("", "Zylker", SPANS)
    assert m.similarity >= HALLUCINATION_THRESHOLD
    assert m.similarity == 0.75  # weak support, never 1.0
    assert m.span is not None and m.span.t_s == 3.0


def test_short_date_value_in_ocr_span_is_rescued():
    m = find_evidence("", "September 15", SPANS)
    assert m.similarity == 0.75
    assert m.span is not None and m.span.t_s == 41.0


def test_short_value_with_garbage_quote_is_rescued():
    # paraphrased short quote; the value itself is still verbatim in source
    m = find_evidence("the company", "Bangalore", SPANS)
    assert m.similarity >= HALLUCINATION_THRESHOLD
    assert m.span is not None


def test_multi_source_agreement_counted():
    spans = SPANS + [SourceSpan("Zylker careers page link in bio", None, "caption")]
    m = find_evidence("", "Zylker", spans)
    assert m.n_sources_agreeing == 2


def test_short_value_absent_from_source_dropped():
    m = find_evidence("", "Netflix", SPANS)
    assert m.similarity == 0.0
    assert m.span is None


def test_tiny_value_still_dropped():
    # "AI" inside a span must not ride `a in b` to 1.0 — Phase-1 floor holds
    spans = [SourceSpan("AI is going to change everything", 1.0, "transcript")]
    m = find_evidence("AI", "AI", spans)
    assert m.similarity < HALLUCINATION_THRESHOLD
    assert m.span is None


def test_word_boundary_no_partial_token_match():
    # "partial" (7 chars) must not match inside "partnership"
    spans = [SourceSpan("our partnership with the foundation grows",
                        5.0, "transcript")]
    m = find_evidence("", "partial", spans)
    assert m.similarity == 0.0
    assert m.span is None


def test_long_verbatim_quote_still_wins():
    q = "Zylker is hiring data analysts"
    assert len(q) >= MIN_QUOTE_CHARS
    m = find_evidence(q, "Zylker", SPANS)
    assert m.similarity == 1.0
