"""Score one set of real extractions under the old and the new evidence rule.

docs/EVAL.md requires an eval run before and after an evidence-matcher change,
but two `pytest tests/test_ai_eval.py` runs are not comparable: llama-server at
temperature 0 still returned different fact counts per reel across runs, so the
matcher effect is buried under model variance. This calls the model ONCE per
golden item, caches the extraction, and scores the identical facts twice —
under the pre-change matcher and under the document-locality matcher that ships.

Read-only against the DB; writes its report to the temp data dir.

Usage:
    .venv/Scripts/python.exe scripts/replay_matcher_rules.py
    .venv/Scripts/python.exe scripts/replay_matcher_rules.py --cache out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.knowledge.evidence import (HALLUCINATION_THRESHOLD, JACCARD_MIN,  # noqa: E402
                                    MIN_QUOTE_CHARS, MIN_VALUE_CHARS,
                                    EvidenceMatch, SourceSpan, _phrase_in_tokens,
                                    _claim_terms, _jaccard, find_evidence, norm)
from app.core.config import settings  # noqa: E402
from test_ai_eval import _unified_and_spans, load_golden  # noqa: E402


def find_evidence_legacy(quote: str, value: str,
                         spans: list[SourceSpan]) -> EvidenceMatch:
    """The matcher as it behaved before document locality: any substring hit is
    a perfect match regardless of probe length, and verbatim value containment
    forces 0.75 in a span of any size. Frozen here so the comparison stays
    possible after the shipped rule moves on."""
    qn, vn = norm(quote), norm(value)
    probe = qn if len(qn) >= len(vn) else vn
    alt = vn if probe == qn else qn
    if len(probe) < MIN_QUOTE_CHARS:
        vt = vn.split()
        if vt and len(vn) >= MIN_VALUE_CHARS:
            for sp in spans:
                if _phrase_in_tokens(norm(sp.text).split(), vt):
                    return EvidenceMatch(0.75, sp, 1)
        return EvidenceMatch(0.0, None, 0)
    best_span, best, agree = None, 0.0, 0
    claim = _claim_terms(value)
    for sp in spans:
        sn = norm(sp.text)
        if not claim or not claim <= _claim_terms(sp.text):
            continue

        def sim(a: str, b: str) -> float:
            if not a or not b:
                return 0.0
            if a in b:
                return 1.0
            if _jaccard(a, b) < JACCARD_MIN:
                return 0.0
            from difflib import SequenceMatcher
            return SequenceMatcher(None, a, b).ratio()

        s = max(sim(probe, sn), sim(alt, sn))
        if vn and vn in sn:
            s = max(s, 0.75)
        if s >= 0.55:
            agree += 1
        if s > best:
            best, best_span = s, sp
    return EvidenceMatch(round(best, 3), best_span, agree)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="", help="JSON file of cached extractions")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cache: dict = {}
    if args.cache and Path(args.cache).exists():
        cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))

    from app.ai.router import get_router
    router = get_router()
    items = load_golden()
    if args.limit:
        items = items[:args.limit]
    rows = []
    for g in items:
        gid = g["id"]
        unified, spans = _unified_and_spans(g)
        schema = (g.get("expected") or {}).get("schema") or "generic"
        if gid in cache:
            facts = cache[gid]
        else:
            ext = router.extract(unified, schema, reel_id=None) or {}
            llm = settings.llm_backend
            facts = [{"field": f.get("field"), "value": f.get("value") or "",
                      "quote": f.get("quote") or ""}
                     for f in ext.get("facts", [])]
            cache[gid] = facts
            print(f"  extracted {gid}: {len(facts)} facts (backend={llm})",
                  file=sys.stderr)
        flips = []
        for f in facts:
            old = find_evidence_legacy(f["quote"], f["value"], spans)
            new = find_evidence(f["quote"], f["value"], spans)
            keepl = lambda m: m.similarity >= HALLUCINATION_THRESHOLD  # noqa: E731
            if keepl(old) != keepl(new):
                flips.append((f["field"], f["value"][:34], round(old.similarity, 2),
                              round(new.similarity, 2),
                              new.span.source if new.span else "-",
                              old.span.source if old.span else "-"))
        rows.append((gid, len(facts),
                     sum(1 for f in facts
                         if find_evidence_legacy(f["quote"], f["value"], spans).similarity
                         >= HALLUCINATION_THRESHOLD),
                     sum(1 for f in facts
                         if find_evidence(f["quote"], f["value"], spans).similarity
                         >= HALLUCINATION_THRESHOLD),
                     flips))
    if args.cache:
        Path(args.cache).write_text(json.dumps(cache, indent=2), encoding="utf-8")

    print(f"\n{'item':<16}{'raw':>5}{'kept-legacy':>12}{'kept-now':>9}  flips")
    for gid, n, o, nw, flips in rows:
        print(f"{gid:<16}{n:>5}{o:>12}{nw:>9}  "
              + ("; ".join(f"{f[0]}={f[1]!r} {f[2]}->{f[3]} ({f[5]}->{f[4]})"
                           for f in flips) or "-"))
    tot_o = sum(r[2] for r in rows)
    tot_n = sum(r[3] for r in rows)
    doc_flips = [f for r in rows for f in r[4] if "document" in (f[4], f[5])]
    print(f"\ntotal kept: legacy {tot_o} -> now {tot_n}"
          f" ({tot_o - tot_n} facts lost, all now needing a real quote)")
    print(f"flips caused by document spans: {len(doc_flips)} of {sum(len(r[4]) for r in rows)}")


if __name__ == "__main__":
    main()
