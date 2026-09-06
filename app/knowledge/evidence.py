"""Evidence Ledger engine.

For every fact the SLM extracts, we require a supporting quote. The quote is
fuzzily matched back to transcript segments / OCR lines / caption. Match
quality drives confidence:

conf = 0.35 * quote_similarity + 0.25 * has_timestamped_source
       + 0.2 * multiple_sources_agree + 0.2 * model_self_confidence

If no source matches at all (>0.45 sim), the fact is DROPPED as likely
hallucination and logged. This is the anti-hallucination backbone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

WORD_RE = re.compile(r"[a-z0-9\u0900-\u097F₹%./+-]+")


def norm(text: str) -> str:
    return " ".join(WORD_RE.findall((text or "").lower()))


@dataclass
class SourceSpan:
    text: str
    t_s: float | None
    source: str            # transcript | ocr | caption | metadata


@dataclass
class EvidenceMatch:
    similarity: float
    span: SourceSpan | None
    n_sources_agreeing: int


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


MIN_QUOTE_CHARS = 15  # shorter normalized text can't be verified reliably


def find_evidence(quote: str, value: str, spans: list[SourceSpan]) -> EvidenceMatch:
    """Find best-matching source span for a claimed quote/value."""
    qn = norm(quote)
    vn = norm(value)
    probe = qn if len(qn) >= len(vn) else vn
    alt = vn if probe == qn else qn
    if len(probe) < MIN_QUOTE_CHARS:
        # A one-word quote would otherwise ride the `a in b` shortcut
        # straight to a 1.0 similarity and slip past the drop threshold.
        return EvidenceMatch(similarity=0.0, span=None, n_sources_agreeing=0)
    best_span, best = None, 0.0
    agree = 0
    for sp in spans:
        sn = norm(sp.text)
        s = max(_sim(probe, sn), _sim(alt, sn))
        # value tokens appearing in span counts as weak support
        if vn and vn in sn:
            s = max(s, 0.75)
        if s >= 0.55:
            agree += 1
        if s > best:
            best, best_span = s, sp
    return EvidenceMatch(similarity=round(best, 3), span=best_span,
                         n_sources_agreeing=agree)


def confidence_score(match_sim: float, n_agree: int,
                     model_conf: float | None,
                     has_timestamp: bool = False) -> float:
    """Timestamp weight applies only when a source timestamp exists."""
    ts = 0.25 if has_timestamp else 0.0
    multi = 1.0 if n_agree >= 2 else 0.0
    mc = model_conf if model_conf is not None else 0.5
    score = 0.35 * match_sim + ts + 0.2 * multi + 0.2 * mc
    return round(min(max(score, 0.05), 0.99), 2)


HALLUCINATION_THRESHOLD = 0.45
