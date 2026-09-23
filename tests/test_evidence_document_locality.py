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


# ---------------------------------------------------------------------------
# Near-verbatim quotes in a large document span.
#
# Both similarity measures in `_ratio` divide by a quantity the SPAN dominates:
# Jaccard by |A∪B| and SequenceMatcher.ratio() by len(a)+len(b). Measured on a
# 2,857-char page, a quote that is fully present there except for one inflected
# word scores Jaccard 0.053 (prefiltered to 0.0) and ratio 0.006 — so it was
# dropped as a hallucination. docs/EVAL.md, 2026-09-22. `_sim` now judges a
# long document quote by how much of the QUOTE the span explains.


_QUOTE = ("attention mechanisms and research on encoding schemes: the "
          "transformer model blocks scale quadratically with sequence lengths")
# same run with the one inflected word restored, i.e. exactly verbatim
_VERBATIM = _QUOTE[:-1]


def test_a_long_near_verbatim_document_quote_is_recovered():
    """One inflected word must not destroy an otherwise verbatim 120-char run."""
    spans = _doc(_PAGE)
    assert _QUOTE not in spans[0].text          # not exact: the whole point
    ev = find_evidence(_QUOTE, "transformer model", spans)
    assert ev.similarity >= 0.90, ev.similarity
    assert ev.span is not None and ev.span.page == 1


def test_a_short_value_in_that_same_page_is_still_not_evidence():
    """The recovery must not re-open the door the containment budget closed:
    'research' occurs in this page dozens of times and proves nothing."""
    spans = _doc(_PAGE)
    assert find_evidence("research", "research", spans).similarity == 0.0


def test_local_coverage_is_not_applied_below_a_long_quote():
    """Below MIN_STRONG_QUOTE_CHARS a probe is satisfied incidentally by any
    large span, so it keeps the old verdict — asserted on `_sim` directly, because
    `find_evidence` caps a contained probe at 0.75 before scoring ever sees it."""
    from app.knowledge.evidence import _ratio, _sim
    probe = norm(_VERBATIM[:MIN_STRONG_QUOTE_CHARS - 20]) + " zzz absent"
    assert MIN_QUOTE_CHARS <= len(probe) < MIN_STRONG_QUOTE_CHARS
    assert probe not in norm(_PAGE)
    assert _sim(probe, norm(_PAGE), True) == _ratio(probe, norm(_PAGE))


def test_a_quote_stitched_from_disjoint_page_regions_is_not_verbatim():
    """`_local_coverage` must not sum matches from anywhere in the span: a probe
    made of the licence sentence followed by the body sentence is contiguous
    nowhere, and an unbounded sum scored it 0.99 — indistinguishable from a real
    quote. Partial support is the honest verdict; strong is not."""
    from app.knowledge.evidence import _sim
    stitch = norm("Provided proper attribution is provided the transformer model "
                  "blocks scale quadratically with sequence lengths")
    page = norm(_PAGE)
    assert _sim(stitch, page, True) < 0.75, _sim(stitch, page, True)
    ev = find_evidence(stitch, "transformer model", _doc(_PAGE))
    # 0.75 here is the designed weak tier, reached by the VALUE being on the
    # page — not by the stitched quote passing as verbatim.
    assert ev.similarity <= 0.75, ev.similarity


def test_a_long_quote_absent_from_the_page_stays_rejected():
    """The 0.45 hallucination gate has to hold for a probe this branch now
    scores, not just for short values."""
    from app.knowledge.evidence import HALLUCINATION_THRESHOLD
    fake = ("the quarterly revenue guidance for fiscal year twenty twenty-six "
            "exceeded analyst consensus by twelve percent")
    assert len(norm(fake)) >= MIN_STRONG_QUOTE_CHARS
    ev = find_evidence(fake, "revenue guidance", _doc(_PAGE))
    assert ev.similarity < HALLUCINATION_THRESHOLD and ev.span is None


def test_transcript_spans_keep_their_tuned_fuzzy_behavior():
    """Scoped to documents by measurement, not assumption — a transcript segment
    is short, so the global ratio is fine there and must stay byte-identical."""
    from app.knowledge.evidence import _ratio, _sim
    seg = norm("the transformer blocks scale quadratically with sequence length")
    probe = norm("the transformer blocks scale quadratically with sequence lengths")
    assert _sim(probe, seg, False) == _ratio(probe, seg)
    spans = [SourceSpan(text=seg, t_s=3.0, source="transcript")]
    ev = find_evidence(probe, "transformer blocks", spans)
    assert ev.similarity >= 0.90 and ev.span.t_s == 3.0
