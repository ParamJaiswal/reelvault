"""Backfill reels.summary_grounding for rows written before schema v5.

v5 added the column, so every pre-existing row is NULL and the UI reports
"not source-checked" on all of them. That is truthful but uninformative when
it fires on every reel in the library. Measuring the ratio needs no model
call: transcript, OCR and caption are already stored, so existing summaries
can be scored against their own sources.

Only NULL rows are written. A re-extracted or user-corrected summary already
carries a real measurement and is never overwritten, so re-running is a no-op
and an interrupted run can simply be repeated.

Usage:
    .venv/Scripts/python.exe scripts/backfill_summary_grounding.py
    .venv/Scripts/python.exe scripts/backfill_summary_grounding.py --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.schema import get_db  # noqa: E402
from app.knowledge.evidence import (SUMMARY_GROUNDING_MIN,  # noqa: E402
                                    grounding_ratio)
from app.pipeline.stages import build_spans  # noqa: E402

_PENDING_SQL = (
    "SELECT id, summary, caption FROM reels"
    " WHERE summary IS NOT NULL AND TRIM(summary) != ''"
    "   AND summary_grounding IS NULL"
)


def backfill(apply: bool) -> list[dict]:
    """Score every unmeasured summary. Returns one row per reel touched."""
    report: list[dict] = []
    with get_db() as db:
        pending = [dict(r) for r in db.execute(_PENDING_SQL)]
        for row in pending:
            segs = [dict(x) for x in db.execute(
                "SELECT start_s, text FROM transcript_segments"
                " WHERE reel_id=? ORDER BY start_s", (row["id"],))]
            ocrs = [dict(x) for x in db.execute(
                "SELECT t_s, text FROM ocr_results"
                " WHERE reel_id=? ORDER BY t_s", (row["id"],))]
            spans = build_spans(segs, ocrs, row["caption"] or "")
            ratio = grounding_ratio(row["summary"], spans)
            report.append({"reel_id": row["id"], "grounding": ratio,
                           "verified": ratio >= SUMMARY_GROUNDING_MIN,
                           "n_spans": len(spans)})
            if apply:
                db.execute("UPDATE reels SET summary_grounding=? WHERE id=?",
                           (ratio, row["id"]))
    return report


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    report = backfill(apply)
    if not report:
        print("nothing to backfill: every summary already has a measurement")
        return 0
    flagged = [r for r in report if not r["verified"]]
    for r in sorted(report, key=lambda x: x["grounding"]):
        note = "" if r["verified"] else "  <- would be marked not source-checked"
        print(f"reel {r['reel_id']:>4}  grounding={r['grounding']:.3f}"
              f"  spans={r['n_spans']}{note}")
    verb = "wrote" if apply else "would write"
    print(f"\n{verb} {len(report)} row(s); {len(flagged)} below the "
          f"{SUMMARY_GROUNDING_MIN:.2f} gate.")
    if not apply:
        print("dry run — pass --apply to persist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
