"""Offline matched-output comparison; opt in with RV_EVAL_REPLAY=<trace.json>.

No provider calls. Private replay results are written beside the input trace.
The old matcher is loaded from the pinned, locally reviewed Git revision.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from app.knowledge import evidence
from tests.test_ai_eval import _deadline_ok, _field_matches, _unified_and_spans, load_golden

BASELINE_REVISION = "f42fbef"


def replay(saved, golden, before):
    indexed = {g["id"]: g for g in golden}
    rows = []
    for case in saved["per_case"]:
        g = indexed[case["id"]]
        unified, spans = _unified_and_spans(g)
        calls = case["extraction_trace"]
        if len(calls) != 1:
            raise ValueError(f"{g['id']}: expected one captured extraction call")
        call = calls[0]
        users = [m["content"] for m in call["messages"] if m["role"] == "user"]
        if users != [unified[:7000]]:
            raise ValueError(f"{g['id']}: fixture does not match captured source")
        # Do not silently replay a different recovery path for malformed JSON.
        facts = json.loads(call["raw_completion"])["facts"]
        kept = {"before": [], "after": []}
        changes = []
        for f in facts:
            value, quote = (f.get("value") or "").strip(), (f.get("quote") or "").strip()
            if not value:
                continue
            old = before(quote, value, spans)
            new = evidence.find_evidence(quote, value, spans)
            old_ok = old.similarity >= evidence.HALLUCINATION_THRESHOLD
            new_ok = new.similarity >= evidence.HALLUCINATION_THRESHOLD
            if old_ok:
                kept["before"].append(f)
            if new_ok:
                kept["after"].append(f)
            if old_ok != new_ok:
                changes.append({
                    "field": f.get("field"), "value": value, "quote": quote,
                    "before_kept": old_ok, "after_kept": new_ok,
                    "before_span": old.span.text if old.span else None,
                    "missing_terms_in_before_span": sorted(
                        evidence._claim_terms(value) - evidence._claim_terms(old.span.text)
                    ) if old.span else [],
                })
        scores = {}
        for name, ff in kept.items():
            fields = _field_matches(g["expected"].get("fields", {}), ff)
            deadlines = [_deadline_ok(d["date_text"], ff)
                         for d in g["expected"].get("deadlines", [])]
            scores[name] = {"kept": len(ff), "fields": fields, "deadlines": deadlines,
                            "min_met": len(ff) >= g["expected"].get("min_facts_kept", 0)}
        rows.append({"id": g["id"], **scores, "changes": changes})
    totals = {}
    for name in ("before", "after"):
        totals[name] = {
            "kept": sum(r[name]["kept"] for r in rows),
            "field_hits": sum(sum(r[name]["fields"].values()) for r in rows),
            "field_total": sum(len(r[name]["fields"]) for r in rows),
            "deadline_hits": sum(sum(r[name]["deadlines"]) for r in rows),
            "deadline_total": sum(len(r[name]["deadlines"]) for r in rows),
            "minimum_kept_cases": sum(r[name]["min_met"] for r in rows),
        }
    return {"baseline_revision": BASELINE_REVISION, "totals": totals, "cases": rows}


def _sample():
    quote = "Download your LinkedIn connections"
    g = {"id": "test", "transcript": [{"t": 0.0, "text": quote}],
         "expected": {"fields": {"company": ["zylker"]}, "deadlines": [],
                      "min_facts_kept": 1}}
    unified, _ = _unified_and_spans(g)
    saved = {"per_case": [{"id": "test", "extraction_trace": [{
        "messages": [{"role": "user", "content": unified}],
        "raw_completion": json.dumps({"facts": [
            {"field": "company", "value": "Zylker", "quote": quote}]})}]}]}
    return saved, [g]


def test_replay_uses_identical_facts_and_source():
    saved, golden = _sample()

    def before(quote, value, spans):
        assert value == "Zylker" and quote == spans[0].text
        return evidence.EvidenceMatch(1.0, spans[0], 1)

    result = replay(saved, golden, before)
    assert result["totals"]["before"]["field_hits"] == 1
    assert result["totals"]["after"]["field_hits"] == 0
    assert result["cases"][0]["changes"][0]["missing_terms_in_before_span"] == ["zylker"]


def test_replay_rejects_source_drift():
    saved, golden = _sample()
    golden[0]["caption"] = "new source text"
    with pytest.raises(ValueError, match="does not match captured source"):
        replay(saved, golden, evidence.find_evidence)


@pytest.mark.skipif(not os.environ.get("RV_EVAL_REPLAY"), reason="requires saved private trace")
def test_saved_trace_replay():
    path = Path(os.environ["RV_EVAL_REPLAY"])
    saved = json.loads(path.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    source = subprocess.run(
        ["git", "--no-pager", "show", f"{BASELINE_REVISION}:app/knowledge/evidence.py"],
        cwd=root, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    module = types.ModuleType("_replay_baseline_evidence")
    sys.modules[module.__name__] = module
    try:
        exec(compile(source, "baseline_evidence.py", "exec"), module.__dict__)
        result = replay(saved, load_golden(), module.find_evidence)
    finally:
        del sys.modules[module.__name__]
    output = path.with_name(path.stem + "_replay.json")
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\nReplay output: {output}")
    print(json.dumps(result["totals"], indent=2))
    print("Changed decisions:", sum(len(c["changes"]) for c in result["cases"]))
    assert len(result["cases"]) == saved["golden_count"]
    assert not any(d["after_kept"] for c in result["cases"] for d in c["changes"]), (
        "guard unexpectedly rescued previously rejected facts")
