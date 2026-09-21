"""Verbatim containment inside a document span must be LOCAL to be evidence.

Three doors in app/knowledge/evidence.py used to turn "this string occurs in
the source" into evidence with no regard for how large the containing span was:
the short-value rescue, find_evidence's `value in span -> 0.75` boost, and
`_sim`'s `probe in span -> 1.0` shortcut. A PDF page is ~2800 characters and an
article body arrives as one 66,000-character block (trafilatura 2.2.0 returns
flat text), so they contain almost any phrase by accident — the live library
carried difficulty='Research' and topic='Transformer model' on the arXiv
license-boilerplate page, and the shortcut is what made those look like *strong*
matches rather than weak ones.

Now a document span can only prove a match when it is about the size of the
match. Transcript, OCR and caption behavior is deliberately untouched: the same
budget applied there removed genuine support in the live library (11 of 17 real
caption hits). Measurement and calibration: scripts/audit_rescue.py, docs/EVAL.md.
"""
from app.knowledge.evidence import (CONTAINMENT_SPAN_CHARS_PER_MATCHED,
                                    CONTAINMENT_SPAN_CHARS_SLACK,
                                    MIN_QUOTE_CHARS, MIN_STRONG_QUOTE_CHARS,
                                    SourceSpan, _containment_is_local,
                                    find_evidence, norm)
from app.pipeline.stages import build_spans

# One PDF page as the paper adapter emits it: a `p.N` marker paragraph, ~2400
# characters of boilerplate and body that contains almost any research word.
_PAGE = ("p.1 Provided proper attribution is provided, Google hereby grants "
         "permission to reproduce the tables and figures in this paper solely "
         "for use in journalistic or scholarly work. "
         + "attention mechanisms and research on encoding schemes: the "
           "transformer model blocks scale quadratically with sequence length. "
         * 22)


def _doc(body: str) -> list[SourceSpan]:
    return build_spans([], [], "", body)


def test_a_page_is_one_span_and_stays_page_located():
    spans = _doc(_PAGE)
    assert len(spans) == 1 and spans[0].page == 1
    assert len(norm(spans[0].text)) > 2000


def test_short_value_inside_a_page_is_not_evidence():
    """The live failure: 'Research' occurs on the page, so it proved nothing."""
    m = find_evidence("the paper is about research", "Research", _doc(_PAGE))
    assert m.similarity == 0.0 and m.span is None


def test_two_word_value_inside_a_page_is_weak_never_strong():
    """'Transformer model' clears the quote-length floor, so it used to score a
    perfect match against a page that merely happens to contain the phrase."""
    vn = norm("transformer model")
    spans = _doc(_PAGE)
    assert vn in norm(spans[0].text)
    assert not _containment_is_local(vn, norm(spans[0].text))
    m = find_evidence(vn, vn, spans)
    assert m.similarity == 0.75, m
    assert m.span.page == 1            # the locator survives the cap


def test_the_same_phrase_in_a_local_span_is_strong_evidence():
    spans = _doc("the transformer model blocks scale quadratically")
    vn = norm("transformer model")
    assert _containment_is_local(vn, norm(spans[0].text))
    assert find_evidence(vn, vn, spans).similarity == 1.0


def test_value_in_a_short_document_span_is_weak_support():
    """A title line is about the value, so containment carries the fact."""
    spans = _doc("Attention Is All You Need\n\n" + _PAGE)
    m = find_evidence("title", "Attention", spans)
    assert m.similarity == 0.75 and m.span.source == "document"


def test_a_real_quote_from_a_page_is_unaffected():
    """A verbatim run of ten words cannot be coincidence, so page size stops
    mattering: the long-quote escape keeps genuine paper quotes at 1.0."""
    quote = ("Google hereby grants permission to reproduce the tables and "
             "figures in this paper solely for use in journalistic or scholarly")
    assert len(norm(quote)) >= MIN_STRONG_QUOTE_CHARS
    m = find_evidence(quote, "Attribution permission", _doc(_PAGE))
    assert m.similarity == 1.0, m


def test_a_mid_length_phrase_in_a_page_is_capped():
    """Between the two floors: longer than a quote must be, shorter than a
    coincidence can be. Capped at weak support, not dropped."""
    phrase = "quadratically with sequence length"
    assert MIN_QUOTE_CHARS <= len(norm(phrase)) < MIN_STRONG_QUOTE_CHARS
    m = find_evidence(phrase, phrase, _doc(_PAGE))
    assert m.similarity == 0.75, m


def test_transcript_rescue_is_unchanged():
    """The Phase-4 guarantee: a short value verbatim in a spoken line stays
    weak evidence at 0.75 even when the quote is under the floor."""
    spans = build_spans([{"text": "Zylker Analytics is hiring in Bangalore",
                          "start_s": 3.0}], [], "")
    m = find_evidence("", "Zylker", spans)
    assert m.similarity == 0.75 and m.span.t_s == 3.0


def test_spoken_value_below_the_rescue_floor_still_needs_a_quote():
    """'forty' is too short to verify — that policy predates this change."""
    spans = build_spans([{"text": "the stipend is forty five thousand rupees",
                          "start_s": 8.0}], [], "")
    assert find_evidence("forty", "forty", spans).similarity == 0.0


def test_ocr_and_caption_behavior_is_deliberately_unchanged():
    """The budget is document-scoped by measurement: an OCR overlay of the
    author's own sentence is not a 15-page paper, so a contained phrase in a
    span larger than its budget still counts as a strong match there."""
    text = " ".join(f"collected lists of techniques for doing great work in "
                    f"field number {i} of many" for i in range(12))
    spans = build_spans([], [{"text": text, "t_s": None}], "")
    needle = "collected lists of techniques"
    assert len(norm(spans[0].text)) > (CONTAINMENT_SPAN_CHARS_PER_MATCHED
                                       * len(norm(needle))
                                       + CONTAINMENT_SPAN_CHARS_SLACK)
    assert find_evidence(needle, needle, spans).similarity == 1.0


# ----------------------------------------------------------------- edges
def test_containment_budget_is_inclusive_and_scales_with_the_match():
    v = "twelve chars"
    room = (CONTAINMENT_SPAN_CHARS_PER_MATCHED * len(v)
            + CONTAINMENT_SPAN_CHARS_SLACK)
    assert _containment_is_local(v, "x" * room)
    assert not _containment_is_local(v, "x" * (room + 1))
    longer = v + " and a longer value"
    assert _containment_is_local(longer, "x" * (room + 50))


def test_no_probe_below_the_quote_floor_can_score_a_perfect_match():
    """`probe in span -> 1.0` had no length floor, which is how one word became
    strong evidence anywhere, not just in documents."""
    needle = "y" * (MIN_QUOTE_CHARS - 1)
    spans = build_spans([{"text": "x" * 40 + " " + needle + " " + "x" * 40,
                          "start_s": 1.0}], [], "")
    assert find_evidence(needle, needle, spans).similarity < 1.0
    exact = "y" * MIN_QUOTE_CHARS
    spans2 = build_spans([{"text": "x" * 20 + " " + exact, "start_s": 1.0}], [], "")
    assert find_evidence(exact, exact, spans2).similarity == 1.0
