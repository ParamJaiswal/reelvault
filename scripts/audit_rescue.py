"""Audit: does verbatim value containment authenticate a fact, or only prove
the words exist somewhere in the source?

Containment feeds three doors in app/knowledge/evidence.py, and before this
audit none of them looked at how large the containing span was: the short-value
rescue, find_evidence's `value in span -> 0.75` boost, and `_sim`'s
`probe in span -> 1.0` shortcut. On a transcript line or an OCR overlay that
containment is informative: the span is roughly the size of the value. Inside a
PDF page (~2800 chars) or an article body (66,000 before documents were
chunked) it is not — the live library carried difficulty='Research' and
topic='Transformer model' on the arXiv license-boilerplate page, and every
cloud-served article fact in the docs/EVAL.md A/B rode these doors.

Measurement: every extracted fact value is offered as a probe to every reel it
did NOT come from. A probe accepted by a foreign reel is a value that
containment alone would have authenticated with someone else's source text —
the false-accept rate of the door, grouped by the span source that carried it,
because that is what the shipped budget keys on.

Read-only. Usage:
    .venv/Scripts/python.exe scripts/audit_rescue.py
    .venv/Scripts/python.exe scripts/audit_rescue.py --factor 20 --slack 120
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.schema import get_db  # noqa: E402
from app.knowledge.evidence import (CONTAINMENT_SPAN_CHARS_PER_MATCHED,  # noqa: E402
                                    CONTAINMENT_SPAN_CHARS_SLACK,
                                    MIN_VALUE_CHARS, norm)
from app.pipeline.stages import build_spans  # noqa: E402

_SKIP_FIELDS = ("summary", "category", "deadline", "due", "date", "link",
                "email", "technologies")

_DOC_KINDS = {"paper", "article", "x_post", "note", "image_post"}


def _load() -> tuple[dict[int, list[tuple[str, str]]], dict[int, str],
                    list[tuple[int, str]]]:
    """Per reel: its spans as (source, normalized text) and its content_kind.
    Plus every model-extracted fact as (origin reel_id, normalized value)."""
    with get_db() as db:
        reels = [dict(r) for r in db.execute(
            "SELECT id, content_kind, caption FROM reels")]
        kinds = {r["id"]: r["content_kind"] or "video" for r in reels}
        spans: dict[int, list[tuple[str, str]]] = {}
        for r in reels:
            segs = [{"text": s["text"], "start_s": s["start_s"]} for s in db.execute(
                "SELECT text, start_s FROM transcript_segments"
                " WHERE reel_id=? ORDER BY start_s", (r["id"],))]
            ocrs = [{"text": o["text"], "t_s": o["t_s"]} for o in db.execute(
                "SELECT text, t_s FROM ocr_results WHERE reel_id=?", (r["id"],))]
            doc = db.execute("SELECT body_text FROM documents WHERE reel_id=?",
                             (r["id"],)).fetchone()
            built = build_spans(segs, ocrs, r["caption"] or "",
                                doc["body_text"] if doc else "")
            spans[r["id"]] = [(s.source, norm(s.text)) for s in built]
        facts = [(row[0], norm(row[1])) for row in db.execute(
            "SELECT reel_id, value FROM facts WHERE field NOT IN (%s)"
            % ",".join("?" * len(_SKIP_FIELDS)), _SKIP_FIELDS)]
    return spans, kinds, facts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor", type=float,
                    default=CONTAINMENT_SPAN_CHARS_PER_MATCHED)
    ap.add_argument("--slack", type=int, default=CONTAINMENT_SPAN_CHARS_SLACK)
    args = ap.parse_args()

    spans, kinds, facts = _load()
    probes = [(o, v) for o, v in facts if len(v) >= MIN_VALUE_CHARS]
    docs = sum(1 for k in kinds.values() if k in _DOC_KINDS)
    print("budget: span_chars <= %g * value_chars + %d" % (args.factor, args.slack))
    print("reels: %d (%d document/text), extracted values probed: %d"
          % (len(spans), docs, len(probes)))

    def report(title, same_reel):
        """Per span source: how often containment alone carried a value, how
        many of those hits the shipped rule admits (its budget applies to
        document chunks only), and what a uniform budget would have cost — the
        measurement behind that scope choice."""
        rows = {}
        for rid, haystack in spans.items():
            for source in {s for s, _ in haystack}:
                g = rows.setdefault(source, [0, 0, 0])
                for origin, vn in probes:
                    if (origin == rid) != same_reel:
                        continue
                    hit = [len(sn) for s, sn in haystack
                           if s == source and vn in sn]
                    if not hit:
                        continue
                    g[0] += 1
                    ok = [n for n in hit
                          if n <= args.factor * len(vn) + args.slack]
                    if ok or source != "document":
                        g[1] += 1
                    if ok:
                        g[2] += 1
        print("")
        print(title)
        for source in sorted(rows, key=lambda s: -rows[s][0]):
            hits, shipped, uniform = rows[source]
            print("  %-11s hits %4d   shipped rule admits %4d   "
                  "uniform budget would admit %4d" % (source, hits, shipped, uniform))

    report("FOREIGN values - containment that would authenticate someone "
           "else's claim:", False)
    report("OWN values - support the budget could cost:", True)


if __name__ == "__main__":
    main()
