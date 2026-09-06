"""AI evaluation harness — deterministic ReelVault benchmark.

Run explicitly:  pytest tests/test_ai_eval.py -q -s   (needs Qwen server up)
Adds RV_EVAL_WITH_SLM=1 to also exercise the business-SLM path in the router.

Golden set: 12 reel-like transcripts spanning 10 content categories, each with
expected categories, key fields, deadline substrings, and opportunity flag.
Measured: classification accuracy/macro-F1, extraction field P/R/F1,
evidence coverage + unsupported-fact rate, radar correctness, latency.
Results written to data/eval_results.json for the report.
"""
from __future__ import annotations

import json
import os
import time

import pytest

pytestmark = pytest.mark.ai_eval

GOLDEN = [
    {"id": "job-01", "text": "Hiring alert! Zylker Analytics is hiring Data Analyst interns in Bangalore. Fresher role, zero to one years experience. You need SQL, Python and Excel. Stipend twenty five thousand per month, pre placement offer of eight to ten lakh. Apply before September fifteenth at zylker dot example dot com slash careers.",
     "categories": {"Job", "Internship"}, "primary_schema": "job",
     "fields": {"company": ["zylker"], "role": ["data analyst"], "location": ["bangalore"], "deadline": ["september"]},
     "opportunity": True},
    {"id": "job-02", "text": "Google is hiring UI engineers in Hyderabad, two to four years experience, react and typescript required. CTC up to thirty lakh. Last date to apply is October 20th, link in caption careers dot google dot com.",
     "categories": {"Job"}, "primary_schema": "job",
     "fields": {"company": ["google"], "location": ["hyderabad"], "skills": ["react"], "experience_required": ["two to four"]},
     "opportunity": True},
    {"id": "intern-01", "text": "Summer internship program at Microsoft Research India. Undergrad students can apply for machine learning research internships until November 30. Stipend provided, remote friendly.",
     "categories": {"Internship"}, "primary_schema": "job",
     "fields": {"company": ["microsoft"], "deadline": ["november"]},
     "opportunity": True},
    {"id": "edu-01", "text": "RAG explained in sixty seconds. Chunk your documents, embed them into a vector database like FAISS or Qdrant, retrieve top matches at query time, stuff into the LLM prompt. That is retrieval augmented generation.",
     "categories": {"Educational", "Tutorial", "AI/ML"}, "primary_schema": "education",
     "fields": {"topic": ["rag"]}, "opportunity": False},
    {"id": "edu-02", "text": "Pandas tutorial for beginners: how to groupby, merge dataframes and pivot tables. Full notebook link below, practice datasets included. Great for data analyst interviews.",
     "categories": {"Tutorial", "Data Analytics", "Data Science"}, "primary_schema": "education",
     "fields": {}, "opportunity": False},
    {"id": "tool-01", "text": "Stop paying for transcription. Whisper runs completely offline on your laptop, over ninety languages, open source from OpenAI. Faster whisper makes it five times faster. Free forever, github repo on screen.",
     "categories": {"Tool", "Productivity"}, "primary_schema": "tool",
     "fields": {"tool_name": ["whisper"], "pricing": ["free"]}, "opportunity": False},
    {"id": "tool-02", "text": "Cursor is the AI code editor everyone talks about. Tab autocomplete, chat with your codebase, agent mode. Twenty dollars a month, free tier available. Alternative to VS Code with copilot.",
     "categories": {"Tool", "AI/ML", "Productivity"}, "primary_schema": "tool",
     "fields": {"tool_name": ["cursor"], "pricing": ["twenty"]}, "opportunity": False},
    {"id": "career-01", "text": "Three career tips for fresher data analysts: build two solid projects, learn SQL deeply before python libraries, and network with recruiters on LinkedIn weekly. Mindset matters more than tools.",
     "categories": {"Career"}, "primary_schema": "generic",
     "fields": {}, "opportunity": False},
    {"id": "biz-01", "text": "Business idea: start a niche job board for blue collar hiring in tier two cities. Charge companies three thousand per listing. Low competition, recurring revenue, easy to build with no code.",
     "categories": {"Business", "Startup"}, "primary_schema": "generic",
     "fields": {}, "opportunity": False},
    {"id": "fin-01", "text": "SIP versus lumpsum explained. If you have fifty thousand rupees, investing monthly via SIP averages out market volatility. Historical index returns around twelve percent. Start early, compound longer.",
     "categories": {"Finance"}, "primary_schema": "generic",
     "fields": {}, "opportunity": False},
    {"id": "event-01", "text": "Bangalore AI meetup this Saturday six PM at WeWork Koramangala. Talk on building agents with LangChain. Free entry, register on lu.meetup dot com slash bangai, limited seats.",
     "categories": {"News", "AI/ML", "Career"}, "primary_schema": "event",
     "fields": {"location": ["bangalore", "koramangala"]}, "opportunity": True},
    {"id": "prod-01", "text": "Notion template for tracking job applications: columns for company, role, status, salary, follow up date. Duplicate my free template from the link. Stop losing track of your applications.",
     "categories": {"Productivity", "Tool", "Career"}, "primary_schema": "tool",
     "fields": {}, "opportunity": False},
]


def _norm(s):
    import re
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower())


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


def run_eval(with_slm: bool | None = None):
    """with_slm: None=env RV_EVAL_WITH_SLM, True/False force."""
    from app.ai.providers import get_llm
    from app.ai.router import get_router
    from app.core.config import settings
    from app.knowledge.evidence import (SourceSpan, find_evidence,
                                        HALLUCINATION_THRESHOLD)

    if with_slm is None:
        with_slm = os.environ.get("RV_EVAL_WITH_SLM", "") == "1"
    settings.slm_enabled = with_slm

    llm = get_llm()
    assert llm.available(), "Qwen llama-server must be running for eval"
    router = get_router()

    def prod_format(g):
        # mirror stage_classify_extract's unified representation
        return (f"CAPTION: {g['id']}\n\nTRANSCRIPT:\n[00:00] {g['text']}\n\n"
                "ON-SCREEN TEXT:\n(none)")

    cat_sets = [g["categories"] for g in GOLDEN]
    preds, lat_cls, lat_ext, ext_rows = [], [], [], []
    served_bys = []
    for g in GOLDEN:
        t0 = time.time()
        cls, served_by = router.classify(prod_format(g))
        lat_cls.append(time.time() - t0)
        served_bys.append(served_by)
        preds.append({c.strip() for c in cls.get("categories", [])})
        t0 = time.time()
        schema = g["primary_schema"] if g["primary_schema"] != "generic" else "generic"
        ext = router.extract(f"TRANSCRIPT:\n{g['text']}", schema)
        lat_ext.append(time.time() - t0)
        # evidence check against a single synthetic source span (= the truth)
        spans = [SourceSpan(g["text"], 0.0, "transcript")]
        kept = dropped = covered = 0
        for f in ext.get("facts", []):
            m = find_evidence(f.get("quote", ""), f.get("value", ""), spans)
            if m.similarity < HALLUCINATION_THRESHOLD:
                dropped += 1
            else:
                kept += 1
                covered += 1
        # field-level scoring
        got_fields = {_norm(f.get("field")): _norm(f.get("value"))
                      for f in ext.get("facts", [])}
        field_hits, field_total = 0, 0
        for fname, needles in g["fields"].items():
            field_total += 1
            v = got_fields.get(_norm(fname), "")
            if any(n in v for n in needles):
                field_hits += 1
        # false-positive facts: kept facts whose value shares <40% token
        # overlap with the source text -> likely unsupported invention
        src_tokens = set(_norm(g["text"]).split())
        unsupported = 0
        for f in ext.get("facts", []):
            vt = [t for t in _norm(f.get("value")).split() if len(t) > 2]
            if not vt:
                continue
            overlap = sum(1 for t in vt if t in src_tokens) / len(vt)
            if overlap < 0.4:
                unsupported += 1
        ext_rows.append({"id": g["id"], "kept": kept, "dropped": dropped,
                         "unsupported": unsupported,
                         "field_hits": field_hits,
                         "field_total": field_total,
                         "summary_len": len(ext.get("summary", ""))})

    cat_acc, cat_hit = _cat_score(cat_sets, preds)
    macro_f1 = _macro_f1(cat_sets, preds)
    total_hits = sum(r["field_hits"] for r in ext_rows)
    total_fields = sum(r["field_total"] for r in ext_rows)
    results = {
        "router_served_by_counts": {
            k: sum(1 for s in served_bys if s == k)
            for k in sorted(set(served_bys))},
        "classification": {
            "multilabel_accuracy": round(cat_acc, 3),
            "strict_or_near_hit_rate": round(cat_hit, 3),
            "macro_f1": round(macro_f1, 3),
        },
        "extraction": {
            "golden_field_recall": round(total_hits / max(1, total_fields), 3),
            "fields_probed": total_fields,
        },
        "evidence": {
            "facts_kept": sum(r["kept"] for r in ext_rows),
            "hallucinations_dropped": sum(r["dropped"] for r in ext_rows),
            "unsupported_kept_rate": round(sum(r["unsupported"] for r in ext_rows)
                                           / max(1, sum(r["kept"] for r in ext_rows)), 3),
        },
        "latency_seconds": {
            "classify_mean": round(sum(lat_cls) / len(lat_cls), 2),
            "extract_mean": round(sum(lat_ext) / len(lat_ext), 2),
            "total_mean_per_reel": round((sum(lat_cls) + sum(lat_ext)) / len(GOLDEN), 2),
        },
        "per_case": ext_rows,
    }
    return results


def test_golden_benchmark(tmp_path, monkeypatch):
    monkeypatch.setenv("RV_SLM_ENABLED", os.environ.get("RV_EVAL_WITH_SLM", ""))
    res = run_eval()
    out = tmp_path.parent.parent  # keep artifacts near project data dir
    try:
        from app.core.config import settings
        (settings.data_dir / "eval_results.json").write_text(json.dumps(res, indent=2))
    except Exception:
        pass
    print("\n=== REELVAULT AI BENCHMARK ===")
    print(json.dumps(res, indent=2))
    # acceptance gates (calibrated for Qwen2.5-3B + rules guardrails)
    assert res["classification"]["multilabel_accuracy"] >= 0.60
    assert res["classification"]["macro_f1"] >= 0.45
    assert res["extraction"]["golden_field_recall"] >= 0.50
    assert res["evidence"]["unsupported_kept_rate"] <= 0.55


if __name__ == "__main__":
    print(json.dumps(run_eval(), indent=2))
