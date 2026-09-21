"""Score one set of real extractions under the old and the new evidence rule.

docs/EVAL.md requires an eval run before and after an evidence-matcher change,
but two `pytest tests/test_ai_eval.py` runs are not comparable: llama-server at
temperature 0 returned different raw fact counts per reel across runs, so the
matcher effect is buried under model variance. This calls the model ONCE per
golden item, caches the extraction, and scores the identical facts twice — under
the matcher at `--against` (the commit before the rule shipped) and the current
working tree.

Needs a live LLM backend (the extraction call) and reads the golden JSON files
only; writes nothing except the optional --cache file. Point --against at the
parent of the rule commit, otherwise both sides are the same code.

Usage:
    .venv/Scripts/python.exe scripts/replay_matcher_rules.py
    .venv/Scripts/python.exe scripts/replay_matcher_rules.py --cache out.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from app.ai.router import get_router  # noqa: E402
from app.knowledge.evidence import (HALLUCINATION_THRESHOLD,  # noqa: E402
                                    find_evidence, norm)
from test_ai_eval import _unified_and_spans, load_golden  # noqa: E402


def _legacy_matcher(rev: str):
    """`git show <rev>:app/knowledge/evidence.py`, executed in its own namespace
    rather than retyped, so this comparison cannot drift from the code it
    snapshots. evidence.py depends on nothing outside the stdlib, so the module
    body runs unmodified (it does have to be registered in sys.modules first —
    @dataclass looks the defining module up there)."""
    src = subprocess.run(["git", "show", f"{rev}:app/knowledge/evidence.py"],
                         capture_output=True, text=True, check=True).stdout
    mod = types.ModuleType("evidence_at_rev")
    sys.modules["evidence_at_rev"] = mod
    try:
        exec(compile(src, "evidence_at_rev.py", "exec"), mod.__dict__)
    finally:
        del sys.modules["evidence_at_rev"]
    return mod.find_evidence


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="", help="JSON file of cached extractions")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--against", default="HEAD",
                    help="rev whose evidence.py is the legacy side; must be the"
                         " PARENT of the commit that shipped the rule")
    args = ap.parse_args()

    legacy = _legacy_matcher(args.against)
    cache: dict = {}
    if args.cache and Path(args.cache).exists():
        cache = json.loads(Path(args.cache).read_text(encoding="utf-8"))

    router = get_router()
    items = load_golden()[:args.limit or None]
    kept = lambda m: m.similarity >= HALLUCINATION_THRESHOLD  # noqa: E731
    rows = []
    for g in items:
        gid = g["id"]
        unified, spans = _unified_and_spans(g)
        schema = (g.get("expected") or {}).get("schema") or "generic"
        if gid in cache:
            facts = cache[gid]
        else:
            ext = router.extract(unified, schema, reel_id=None) or {}
            facts = [{"field": f.get("field"), "value": f.get("value") or "",
                      "quote": f.get("quote") or ""} for f in ext.get("facts", [])]
            cache[gid] = facts
            print(f"  extracted {gid}: {len(facts)} facts", file=sys.stderr)
        flips = []
        for f in facts:
            old = legacy(f["quote"], f["value"], spans)
            new = find_evidence(f["quote"], f["value"], spans)
            if kept(old) != kept(new):
                flips.append(
                    f"{f['field']}={f['value'][:26]!r}"
                    f" {round(old.similarity, 2)}->{round(new.similarity, 2)}"
                    f" ({old.span.source if old.span else '-'}"
                    f"->{new.span.source if new.span else '-'})")
        rows.append((gid, len(facts),
                     sum(1 for f in facts
                         if kept(legacy(f["quote"], f["value"], spans))),
                     sum(1 for f in facts
                         if kept(find_evidence(f["quote"], f["value"], spans))),
                     flips, max((len(norm(s.text)) for s in spans), default=0)))
    if args.cache:
        Path(args.cache).write_text(json.dumps(cache, indent=2), encoding="utf-8")

    print(f"\n{'item':<16}{'raw':>5}{'legacy':>8}{'now':>5}"
          f"{'widest span':>13}  flips")
    for gid, n, o, nw, flips, widest in rows:
        print(f"{gid:<16}{n:>5}{o:>6}{nw:>5}{widest:>13}  "
              + ("; ".join(flips) or "-"))
    tot_o, tot_n = sum(r[2] for r in rows), sum(r[3] for r in rows)
    print(f"\ntotal kept: HEAD {tot_o} -> now {tot_n}"
          f" ({tot_o - tot_n} facts lost, all now needing a real quote)")
    longest = max((r[5] for r in rows), default=0)
    print(f"widest span across these rows: {longest} chars"
          + ("  <- too small to exercise a document-locality rule;"
             " the live library is the test (scripts/audit_rescue.py)"
             if longest < 1000 else ""))


if __name__ == "__main__":
    main()
