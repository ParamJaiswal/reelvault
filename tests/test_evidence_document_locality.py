"""Verbatim containment must be LOCAL to the span that contains it.

Three doors used to turn "this string occurs in the source" into evidence with
no regard for how big the containing span was: the short-value rescue,
find_evidence's `value in span -> 0.75` boost, and `_sim`'s unbounded
`probe in span -> 1.0` shortcut. PDF pages run ~2800 characters and an article
body reached 66,000 (trafilatura hands back flat text), so they contain almost
any phrase by accident — the live library carried difficulty='Research' and
topic='Transformer model' on the arXiv license-boilerplate page.

Now: containment scores 1.0 only inside a span about the size of the match,
0.75 when the span is much larger, and contributes nothing to the short-value
doors there. Documents are also chunked so a real quote has a real locator.
Calibration and measurement: scripts/audit_rescue.py, docs/EVAL.md.
"""
from app.knowledge.evidence import (CONTAINMENT_SPAN_CHARS_PER_MATCHED,
                                    CONTAINMENT_SPAN_CHARS_SLACK,
                                    HALLUCINATION_THRESHOLD, MIN_QUOTE_CHARS,
                                    _containment_is_local, find_evidence, norm)
from app.pipeline.stages import _split_document, build_spans

# ~2400 characters of plausible paper text, the size of one PDF page.
_PAGE = ("p.1 Provided proper attribution is provided, Google hereby grants "
         "permission to reproduce the tables and figures in this paper solely "
         "for use in journalistic or scholarly work. "
         + "attention mechanisms and research on encoding schemes: the "
           "transformer model blocks scale quadratically with sequence length. " * 22)


def _doc(body):
    return build_spans([], [], "", body)


def test_document_pages_are_chunked_and_page_locators_kept():
    """Sanity for the fixtures below: a 2400-char page becomes several chunks,
    each still carrying its page number."""
    spans = _doc(_PAGE)
    assert len(spans) > 1
    assert all(s.source == "document" and s.page == 1 for s in spans)
    assert 300 < max(len(norm(s.text)) for s in spans) <= 620


def test_short_value_inside_a_page_is_not_evidence():
    """The live failure: 'Research' occurs on the page, so it proved nothing."""
    m = find_evidence("the paper is about research", "Research", _doc(_PAGE))
    assert m.similarity == 0.0 and m.span is None


def test_containment_in_a_span_much_larger_than_the_match_is_weak():
    """One unbroken sentence: the phrase really is in it, and the match still
    says nothing about the claim, so 0.75 is the ceiling. Chunking cannot help
    here (no sentence terminator to split on), which is the point."""
    clause = ("although attention mechanisms were proposed earlier the "
              "transformer model blocks scale quadratically with sequence "
              "length and require large amounts of memory which is precisely "
              "why the authors explored sparse approximations and reviewers "
              "asked for the additional ablations ")
    spans = _doc("p.1 " + clause * 3)
    vn = norm("transformer model")
    assert len(spans) == 1
    assert all(len(norm(s.text)) > (CONTAINMENT_SPAN_CHARS_PER_MATCHED
                                    * len(vn) + CONTAINMENT_SPAN_CHARS_SLACK)
               for s in spans)
    m = find_evidence(vn, vn, spans)
    assert m.similarity == 0.75, m


def test_a_phrase_in_a_local_chunk_is_strong_evidence():
    """After chunking, a page is several ~500-char chunks; a phrase verbatim in
    one of them is a real quote from the paper and keeps its 1.0."""
    vn = norm("transformer model blocks")
    spans = _doc(_PAGE)
    assert any(vn in norm(s.text)
               and _containment_is_local(vn, norm(s.text)) for s in spans)
    assert find_evidence(vn, vn, spans).similarity == 1.0


def test_value_in_a_short_document_span_is_weak_support():
    """A title line is about the value, so containment carries the fact."""
    spans = _doc("Attention Is All You Need\n\n" + _PAGE)
    m = find_evidence("title", "Attention", spans)
    assert m.similarity == 0.75 and m.span.source == "document"


def test_page_locator_survives_chunking():
    spans = _doc("p.4 Diffraction patterns of the crystal lattice\n\n" + _PAGE)
    m = find_evidence("Diffraction patterns of the crystal lattice",
                      "Diffraction patterns", spans)
    assert m.span is not None and m.span.page == 4


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


def test_caption_and_transcript_behavior_is_deliberately_unchanged():
    """The budget is scoped to document chunks. Applying it to captions and OCR
    lines as well removed genuine support in the live library (11 caption and
    8 OCR own-source hits), so a contained phrase in a long caption still
    matches strongly."""
    spans = build_spans([], [], "x" * 900 + " the deadline is september fifteen "
                        + "y" * 1200)
    m = find_evidence("the deadline is september fifteen",
                      "the deadline is september fifteen", spans)
    assert m.similarity == 1.0, m


# ----------------------------------------------------------------- edges
def test_containment_budget_is_inclusive_and_length_scaled():
    v = "twelve chars"
    room = (CONTAINMENT_SPAN_CHARS_PER_MATCHED * len(v)
            + CONTAINMENT_SPAN_CHARS_SLACK)
    assert _containment_is_local(v, "x" * room)
    assert not _containment_is_local(v, "x" * (room + 1))
    longer = v + " longer value"
    assert _containment_is_local(longer, "x" * (room + 50))


def test_no_probe_below_the_quote_floor_can_score_a_perfect_match():
    """Before this change any substring hit returned 1.0 regardless of length."""
    needle = "y" * (MIN_QUOTE_CHARS - 1)
    spans = build_spans([{"text": "x" * 40 + " " + needle + " " + "x" * 40,
                          "start_s": 1.0}], [], "")
    assert find_evidence(needle, needle, spans).similarity < 1.0
    exact = "y" * MIN_QUOTE_CHARS
    spans2 = build_spans([{"text": "x" * 20 + " " + exact, "start_s": 1.0}], [], "")
    assert find_evidence(exact, exact, spans2).similarity == 1.0


def test_flat_document_bodies_get_sentence_chunked():
    """trafilatura 2.2.0 returns one flat block; without chunking every article
    quote is scored against 66,000 characters and no chunk can be local."""
    body = ("Work happens in public. " * 200).strip()
    chunks = _split_document(body)
    assert len(chunks) > 1
    assert all(len(t) <= 620 for _, t in chunks)      # sentences are not cut
    assert "".join(t for _, t in chunks).replace(" ", "") == body.replace(" ", "")


def test_short_document_span_stays_one_chunk():
    assert [t for _, t in _split_document("Attention Is All You Need")] == \
           ["Attention Is All You Need"]


def test_page_text_drops_the_one_word_value_and_keeps_a_real_phrase():
    """The rule separates the two cases page text used to confuse: one common
    word on a page proves nothing, a multi-word verbatim phrase still carries
    the fact."""
    spans = _doc(_PAGE)
    assert find_evidence("the paper is about research", "Research",
                         spans).similarity < HALLUCINATION_THRESHOLD
    assert find_evidence("transformer model blocks", "transformer model blocks",
                         spans).similarity >= HALLUCINATION_THRESHOLD
