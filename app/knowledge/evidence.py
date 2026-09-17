"""Evidence Ledger engine.

For every fact the SLM extracts, we require a supporting quote. The quote is
fuzzily matched back to transcript segments / OCR lines / caption. Match
quality drives confidence:

conf = 0.35 * quote_similarity + 0.25 * has_timestamped_source
       + 0.2 * multiple_sources_agree + 0.2 * model_self_confidence

When no source timestamp exists the 0.25 weight is redistributed rather
than vanishing (caption-only reels are not capped at 0.75):

conf = 0.45 * quote_similarity + 0.35 * multiple_sources_agree
       + 0.2 * model_self_confidence

If no source matches at all (>0.45 sim), the fact is DROPPED as likely
hallucination and logged. This is the anti-hallucination backbone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

WORD_RE = re.compile(r"[a-z0-9\u0900-\u097F₹%./+-]+")
# "25,000" tokenizes as two tokens unless digit-group commas are joined first
_COMMA_IN_NUMBER_RE = re.compile(r"(?<=\d),(?=\d)")

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}
_MULTIPLIERS = ("hundred", "thousand")


def _compose_number_tokens(tokens: list[str]) -> list[str]:
    """Merge number-word runs into digit tokens so spoken numbers match
    their written form ("twenty five thousand" -> "25000"). Runs without
    a multiplier stay separate ("nine eight seven" -> "9 8 7") and digit
    runs are left alone ("15 09 2026"), so dates/phone digits don't fuse."""
    out: list[str] = []
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        starts_number = t in _NUM_WORDS or (
            t.isdigit() and i + 1 < n and tokens[i + 1] in _MULTIPLIERS)
        if not starts_number:
            out.append(t)
            i += 1
            continue
        cur = 0
        while i < n:
            t = tokens[i]
            if t in _NUM_WORDS:
                v = _NUM_WORDS[t]
            elif t.isdigit() and i + 1 < n and tokens[i + 1] in _MULTIPLIERS:
                v = int(t)                      # "25 thousand" -> 25000
            else:
                break
            i += 1
            if v == 100:
                cur = (cur or 1) * 100
            elif v == 1000:
                cur = (cur or 1) * 1000
                break
            elif cur >= 20 and cur % 10 == 0 and v < 10:
                cur += v                        # "twenty five" -> 25
            elif cur:
                out.append(str(cur))            # "twenty twenty" stays split
                cur = v
            else:
                cur = v
        if cur:
            out.append(str(cur))
    return out


def norm(text: str) -> str:
    flat = _COMMA_IN_NUMBER_RE.sub("", (text or "").lower())
    return " ".join(_compose_number_tokens(WORD_RE.findall(flat)))


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


JACCARD_MIN = 0.20  # min word-set overlap before char-level matching runs


def _jaccard(a: str, b: str) -> float:
    """Word-set Jaccard between two already-normalized strings."""
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    # Pre-filter: spans sharing almost no vocabulary with the probe cannot
    # reach the 0.45 hallucination threshold via char ratio, so skip the
    # O(len^2) SequenceMatcher for them (set ops are O(words)).
    if _jaccard(a, b) < JACCARD_MIN:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


MIN_QUOTE_CHARS = 15  # shorter normalized text can't be verified reliably
MIN_VALUE_CHARS = 6   # short-value rescue floor: "Zylker" qualifies, "AI" does not


def _phrase_in_tokens(hay: list[str], needle: list[str]) -> bool:
    """Whole-phrase token containment: ["partial"] must not match inside
    ["partnership"], unlike plain substring `in`."""
    m = len(needle)
    if m == 0 or m > len(hay):
        return False
    first = needle[0]
    for i in range(len(hay) - m + 1):
        if hay[i] == first and hay[i:i + m] == needle:
            return True
    return False


def _short_value_match(value_norm: str,
                       spans: list[SourceSpan]) -> EvidenceMatch | None:
    """Rescue path for short factual values (both quote and value are under
    MIN_QUOTE_CHARS). A value verbatim in a span is designed weak support
    (0.75, never 1.0), so short claims like company names or dates are not
    auto-dropped despite being present in the source. Without this, reel 12
    lost 'Zylker', 'Bangalore', '0-2 yrs', 'September 15' — all in source."""
    vt = value_norm.split()
    if not vt or len(value_norm) < MIN_VALUE_CHARS:
        return None
    best: SourceSpan | None = None
    agree = 0
    for sp in spans:
        if _phrase_in_tokens(norm(sp.text).split(), vt):
            agree += 1
            # prefer a timestamped span so click-to-seek works, then longer text
            if best is None or (sp.t_s is not None
                                and (best.t_s is None
                                     or len(sp.text) > len(best.text))):
                best = sp
    if best is None:
        return None
    return EvidenceMatch(similarity=0.75, span=best, n_sources_agreeing=agree)


_CLAIM_FUNCTION_WORDS = frozenset(
    "a an the and or of for to in on at by with from is are was were be "
    "been being it its this that these those".split()
)

# Deterministic claim-term canonicalization: the model writes "September 15"
# where the source says "September fifteenth", "25k" where it says "twenty
# five thousand", "NOV 30" where it says "November 30". Same fact, different
# surface form — the claim check must not reject those.
_CLAIM_ORDINALS = {
    "first": "1", "second": "2", "third": "3", "fifth": "5",
    "eighth": "8", "ninth": "9", "twelfth": "12",
}
_CLAIM_MONTHS = {
    "jan": "january", "feb": "february", "mar": "march", "apr": "april",
    "jun": "june", "jul": "july", "aug": "august", "sep": "september",
    "sept": "september", "oct": "october", "nov": "november",
    "dec": "december",
}


def _canonical_claim_token(token: str) -> str:
    if token in _CLAIM_MONTHS:
        return _CLAIM_MONTHS[token]
    if token == "zero":
        return "0"
    if token in _CLAIM_ORDINALS:
        return _CLAIM_ORDINALS[token]
    if token in _NUM_WORDS:
        return str(_NUM_WORDS[token])
    if token.endswith("ieth") and token[:-4] + "y" in _NUM_WORDS:
        return str(_NUM_WORDS[token[:-4] + "y"])     # twentieth -> twenty
    if token.endswith("th") and token[:-2] in _NUM_WORDS:
        return str(_NUM_WORDS[token[:-2]])           # fifteenth -> fifteen
    if len(token) > 1 and token.endswith("k") and token[:-1].isdigit():
        return str(int(token[:-1]) * 1000)           # 25k -> 25000
    return token


def _claim_terms(text: str) -> set[str]:
    # Token boundaries prevent names matching inside unrelated words. Reuse
    # number normalization and recognize the existing salary abbreviation.
    normalized = re.sub(r"\blpa\b", "lakh per year", norm(text))
    return {
        _canonical_claim_token(t)
        for t in re.findall(r"[a-z0-9\u0900-\u097F]+", normalized)
    } - _CLAIM_FUNCTION_WORDS


def find_evidence(quote: str, value: str, spans: list[SourceSpan]) -> EvidenceMatch:
    """Find best-matching source span for a claimed quote/value."""
    qn = norm(quote)
    vn = norm(value)
    probe = qn if len(qn) >= len(vn) else vn
    alt = vn if probe == qn else qn
    if len(probe) < MIN_QUOTE_CHARS:
        # Phase-1 floor: a one-word quote must never ride the `a in b`
        # shortcut to 1.0. But a short factual VALUE verbatim in a span is
        # still designed weak support (0.75) — rescue it instead of dropping.
        rescued = _short_value_match(vn, spans)
        if rescued is not None:
            return rescued
        return EvidenceMatch(similarity=0.0, span=None, n_sources_agreeing=0)
    best_span, best = None, 0.0
    agree = 0
    claim_terms = _claim_terms(value)
    for sp in spans:
        sn = norm(sp.text)
        # A genuine quote cannot authenticate an unrelated claim. This is a
        # lexical prerequisite, not a semantic entailment guarantee.
        if not claim_terms or not claim_terms <= _claim_terms(sp.text):
            continue
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
    """Timestamp weight applies only when a source timestamp exists.
    Without one, its 0.25 weight is redistributed: +0.10 to match_sim,
    +0.15 to multi-source agreement."""
    ts = 0.25 if has_timestamp else 0.0
    multi = 1.0 if n_agree >= 2 else 0.0
    mc = model_conf if model_conf is not None else 0.5
    if has_timestamp:
        score = 0.35 * match_sim + ts + 0.2 * multi + 0.2 * mc
    else:
        score = 0.45 * match_sim + 0.35 * multi + 0.2 * mc
    return round(min(max(score, 0.05), 0.99), 2)


HALLUCINATION_THRESHOLD = 0.45
