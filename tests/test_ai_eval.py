"""AI evaluation harness — golden-set benchmark against the real extraction path.

Run explicitly (needs llama-server up on RV_LLM_SERVER_URL):
    pytest tests/test_ai_eval.py -q -s
Add RV_EVAL_WITH_SLM=1 to also exercise the business-SLM path in the router.

Golden set: tests/golden/*.json — 12 hand-labeled reel-like items spanning
job/scholarship/education/tool/recipe/fitness/Hinglish/finance/event content,
each with transcript segments (+t), OCR lines, caption, expected categories,
expected schema, key fields (needle substrings), deadlines (date_text), and a
minimum number of evidence-kept facts.

The harness mirrors app/pipeline/stages.py::stage_classify_extract exactly:
same unified text format, same SourceSpan building, same Evidence Ledger call,
same confidence semantics. Measured: category accuracy/macro-F1, schema
agreement, field recall, content presence, evidence kept/dropped/unsupported
rates, deadline parse recall, malformed-JSON rate, latency.

Results are written to <data_dir>/eval_results.json.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.ai_eval

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def load_golden() -> list[dict]:
    items = [json.loads(p.read_text(encoding="utf-8"))
             for p in sorted(GOLDEN_DIR.glob("*.json"))]
    assert items, f"no golden items found in {GOLDEN_DIR}"
    return items


def _norm(s) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower())


def _unified_and_spans(g: dict):
    """Reproduce stage_classify_extract's unified text + Evidence spans."""
    from app.knowledge.evidence import SourceSpan

    caption = g.get("caption") or ""
    transcript = "\n".join(f"[{_fmt_ts(s['t'])}] {s['text']}"
                           for s in g.get("transcript", []))
    overlay = "\n".join(f"[{_fmt_ts(o['t'])}] OCR: {o['text']}"
                        for o in g.get("ocr", []))
    doc_body = g.get("document") or ""
    unified = (f"CAPTION: {caption}\n\nTRANSCRIPT:\n{transcript or '(no speech detected)'}"
               f"\n\nON-SCREEN TEXT:\n{overlay or '(none)'}")
    if doc_body:
        unified += f"\n\nDOCUMENT:\n{doc_body[:10000]}"

    spans = [SourceSpan(text=s["text"], t_s=s["t"], source="transcript")
             for s in g.get("transcript", [])]
    spans += [SourceSpan(text=o["text"], t_s=o["t"], source="ocr")
              for o in g.get("ocr", [])]
    if caption:
        spans.append(SourceSpan(text=caption, t_s=None, source="caption"))
    if doc_body:
        # Mirror build_spans: paragraph chunks, source='document'.
        spans += [SourceSpan(text=p.strip(), t_s=None, source="document")
                  for p in doc_body.split("\n\n") if p.strip()]
    return unified, spans


def _fmt_ts(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def caption_text(g: dict) -> str:
    return g.get("caption") or ""


def _cat_score(expected_sets, preds):
    """Multi-label accuracy: |pred ∩ exp| / |exp| averaged, plus strict hit."""
    accs, hits = [], 0
    for exp, pred in zip(expected_sets, preds):
        inter = len(exp & pred)
        accs.append(inter / len(exp) if exp else 1.0)
        near = bool(exp & pred) and len(pred - exp) <= 1
        hits += 1 if (exp == pred or near) else 0
    return sum(accs) / len(accs), hits / len(preds)


def _macro_f1(expected_sets, preds):
    labels = set().union(*expected_sets, *preds)
    f1s = []
    for lab in labels:
        tp = sum(lab in p and lab in e for e, p in zip(expected_sets, preds))
        fp = sum(lab in p and lab not in e for e, p in zip(expected_sets, preds))
        fn = sum(lab not in p and lab in e for e, p in zip(expected_sets, preds))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s)


def _deadline_ok(expected_date_text: str, kept_facts: list[dict]) -> bool:
    """True if any kept fact's value deterministically parses to the same
    date as the hand-labeled date_text (app.knowledge.deadlines logic)."""
    from datetime import datetime

    from app.knowledge.deadlines import parse_deadline

    exp = parse_deadline(expected_date_text,
                         today=datetime(2026, 9, 6)).date
    if exp is None:
        return False
    for f in kept_facts:
        val = f.get("value") or ""
        try:
            got = parse_deadline(val, today=datetime(2026, 9, 6)).date
        except Exception:
            got = None
        if got and got.date() == exp.date():
            return True
    return False


def _field_matches(expected_fields: dict, kept_facts: list[dict]) -> dict[str, bool]:
    """Score each field once; evidence-rejected facts cannot earn recall.

    Match separate values independently so duplicates neither overwrite a
    valid match nor combine into a phrase that no individual fact contains.
    """
    return {
        field: any(
            _norm(fact.get("field")) == _norm(field)
            and any(_norm(needle) in _norm(fact.get("value"))
                    for needle in needles)
            for fact in kept_facts
        )
        for field, needles in expected_fields.items() if field != "*"
    }


def _extract_with_trace(router, llm, unified: str, schema: str, capture: bool):
    """Opt-in local trace of the actual provider boundary, without re-inference."""
    if not capture:
        return router.extract(unified, schema), []

    from copy import deepcopy
    from unittest.mock import patch

    calls = []
    chat = llm.chat

    def traced_chat(messages, **kwargs):
        request = {"messages": deepcopy(messages), "options": deepcopy(kwargs)}
        raw = chat(messages, **kwargs)
        calls.append({**request, "raw_completion": raw})
        return raw

    with patch.object(llm, "chat", side_effect=traced_chat):
        extraction = router.extract(unified, schema)
    return extraction, calls


def run_eval(with_slm: bool | None = None):
    """with_slm: None=env RV_EVAL_WITH_SLM, True/False force."""
    from app.ai.providers import get_llm
    from app.ai.router import get_router
    from app.core.config import settings
    from app.knowledge.evidence import (HALLUCINATION_THRESHOLD,
                                        find_evidence)

    if with_slm is None:
        with_slm = os.environ.get("RV_EVAL_WITH_SLM", "") == "1"
    settings.slm_enabled = with_slm

    llm = get_llm()
    assert llm.available(), "Qwen llama-server must be running for eval"
    router = get_router()
    golden = load_golden()

    cat_sets, preds = [], []
    acc_sets = []  # full wish-list labels (reported only, not gated)
    lat_cls, lat_ext = [], []
    served_bys, schema_agree = [], 0
    rows, malformed = [], 0
    field_hits = field_total = 0
    content_hits = content_total = 0
    dl_hits = dl_total = 0
    kept_total = dropped_total = unsupported_total = 0
    min_kept_met = 0
    capture_trace = os.environ.get("RV_EVAL_TRACE", "") == "1"
    from app.knowledge.schemas import SCHEMA_FIELDS

    for g in golden:
        exp = g["expected"]
        unified, spans = _unified_and_spans(g)

        t0 = time.time()
        cls, served_by = router.classify(unified)
        lat_cls.append(time.time() - t0)
        served_bys.append(served_by)
        pred_cats = {c.strip() for c in cls.get("categories", [])}
        preds.append(pred_cats)
        cat_sets.append(set(exp.get("primary") or exp["categories"]))
        acc_sets.append(set(exp["categories"]))
        if (str(cls.get("primary_schema") or "").strip().lower()
                == exp["schema"]):
            schema_agree += 1

        t0 = time.time()
        extraction, trace = _extract_with_trace(
            router, llm, unified, exp["schema"], capture_trace)
        lat_ext.append(time.time() - t0)
        if not extraction:
            malformed += 1

        raw_facts = extraction.get("facts", []) or []
        kept_facts, dropped = [], []
        for f in raw_facts:
            val, quote = ((f.get("value") or "").strip(),
                          (f.get("quote") or "").strip())
            if not val:
                continue
            m = find_evidence(quote, val, spans)
            if m.similarity < HALLUCINATION_THRESHOLD:
                dropped.append({"field": f.get("field"), "value": val,
                                "quote": quote[:120], "sim": m.similarity})
                continue
            kept_facts.append(f)
        kept_total += len(kept_facts)
        dropped_total += len(dropped)

        # unsupported heuristic: kept fact value shares <40% tokens with source
        src_tokens = set(_norm(json.dumps(
            [s["text"] for s in g.get("transcript", [])]
            + [o["text"] for o in g.get("ocr", [])] + [caption_text(g)]
        )).split())
        unsupported = 0
        for f in kept_facts:
            vt = [t for t in _norm(f.get("value")).split() if len(t) > 2]
            if vt and sum(1 for t in vt if t in src_tokens) / len(vt) < 0.4:
                unsupported += 1
        unsupported_total += unsupported

        if len(kept_facts) >= exp.get("min_facts_kept", 0):
            min_kept_met += 1

        field_matches = _field_matches(exp.get("fields") or {}, kept_facts)
        field_total += len(field_matches)
        field_hits += sum(field_matches.values())

        # wildcard content-presence check (taxonomy-gap items)
        for fname, needles in (exp.get("fields") or {}).items():
            if fname != "*":
                continue
            content_total += 1
            all_vals = " ".join(_norm(f.get("value")).lower()
                                for f in kept_facts)
            if any(n in all_vals for n in needles):
                content_hits += 1

        # deadline parse recall
        for d in exp.get("deadlines", []):
            dl_total += 1
            if _deadline_ok(d["date_text"], kept_facts):
                dl_hits += 1

        rows.append({
            "id": g["id"],
            "served_by": served_by,
            "pred_categories": sorted(pred_cats),
            "schema_pred": cls.get("primary_schema"),
            "schema_expected": exp["schema"],
            "facts_raw": len(raw_facts),
            "kept": len(kept_facts),
            "dropped": len(dropped),
            "unsupported": unsupported,
            "min_facts_met": len(kept_facts) >= exp.get("min_facts_kept", 0),
            "field_hits": sum(field_matches.values()),
            "field_total": len(field_matches),
            "field_matches": field_matches,
            "invalid_fields": sorted({
                str(f.get("field")) for f in raw_facts
                if exp["schema"] in SCHEMA_FIELDS
                and f.get("field") not in SCHEMA_FIELDS[exp["schema"]].model_fields
            }),
            "deadline_ok": [(_deadline_ok(d["date_text"], kept_facts))
                            for d in exp.get("deadlines", [])],
            "latency_extract_s": round(lat_ext[-1], 2),
            **({"extraction_trace": trace, "kept_facts": kept_facts,
                "dropped_facts": dropped} if capture_trace else {}),
        })

    # Label policy: `primary` = categories a precise classifier must include
    # (gates); `categories` = full acceptable wish-list (reported only).
    cat_acc, cat_hit = _cat_score(cat_sets, preds)
    macro_f1 = _macro_f1(cat_sets, preds)
    acc_macro_f1 = _macro_f1(acc_sets, preds)
    n = len(golden)
    results = {
        "metric_version": 2,  # field recall uses evidence-kept facts
        "extraction_schema_mode": "expected",  # not end-to-end routing
        "golden_count": n,
        "router_served_by_counts": {
            k: sum(1 for s in served_bys if s == k)
            for k in sorted(set(served_bys))},
        "classification": {
            "multilabel_accuracy": round(cat_acc, 3),
            "strict_or_near_hit_rate": round(cat_hit, 3),
            "macro_f1": round(macro_f1, 3),
            "macro_f1_acceptable_labels": round(acc_macro_f1, 3),
            "schema_agreement": round(schema_agree / n, 3),
        },
        "extraction": {
            "golden_field_recall": round(field_hits / max(1, field_total), 3),
            "fields_probed": field_total,
            "content_presence": round(content_hits / max(1, content_total), 3),
            "malformed_json_rate": round(malformed / n, 3),
        },
        "evidence": {
            "facts_kept": kept_total,
            "hallucinations_dropped": dropped_total,
            "unsupported_kept": unsupported_total,
            "unsupported_kept_rate": round(
                unsupported_total / max(1, kept_total), 3),
            "min_facts_kept_rate": round(min_kept_met / n, 3),
        },
        "deadlines": {
            "parse_recall": round(dl_hits / max(1, dl_total), 3),
            "expected": dl_total,
        },
        "latency_seconds": {
            "classify_mean": round(sum(lat_cls) / n, 2),
            "extract_mean": round(sum(lat_ext) / n, 2),
            "total_mean_per_reel": round((sum(lat_cls) + sum(lat_ext)) / n, 2),
        },
        "per_case": rows,
    }
    return results


def test_golden_benchmark(tmp_path):
    res = run_eval()
    from app.core.config import settings
    result_path = settings.data_dir / "eval_results.json"
    result_path.write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(f"\nEval results: {result_path}")
    print("\n=== REELVAULT AI BENCHMARK (golden set) ===")
    print(json.dumps({k: v for k, v in res.items() if k != "per_case"},
                     indent=2))
    print("\nper-case:")
    for r in res["per_case"]:
        print(f"  {r['id']:<16} kept={r['kept']} dropped={r['dropped']} "
              f"unsup={r['unsupported']} fields={r['field_hits']}/"
              f"{r['field_total']} dl={r['deadline_ok']} "
              f"schema={r['schema_pred']}/{r['schema_expected']}")
    # acceptance gates (calibrated for Qwen2.5-3B + Evidence Ledger guardrails)
    assert res["classification"]["multilabel_accuracy"] >= 0.60
    assert res["classification"]["macro_f1"] >= 0.45
    assert res["extraction"]["golden_field_recall"] >= 0.50
    assert res["evidence"]["unsupported_kept_rate"] <= 0.55
    assert res["evidence"]["min_facts_kept_rate"] >= 0.75
    assert res["deadlines"]["parse_recall"] >= 0.50
    assert res["extraction"]["malformed_json_rate"] <= 0.15


if __name__ == "__main__":
    print(json.dumps(run_eval(), indent=2))
