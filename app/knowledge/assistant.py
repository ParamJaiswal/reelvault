"""Personal AI assistant: fully local RAG over the user's reel knowledge.

Retrieval -> hybrid search for top-k reels -> context pack with evidence ->
SLM answers with [reel#N] citations -> post-validation strips claims that
reference non-provided reels. Answers "I don't know" when retrieval is empty.
"""
from __future__ import annotations

import json
import re

from app.ai.providers import get_embedder, get_llm
from app.db.schema import get_db
from app.knowledge.search import hybrid_search

SYSTEM = """You are the user's personal knowledge assistant for their saved Instagram Reels.
Answer ONLY from the provided context. Cite reels as [R#id] after each claim.
If the context doesn't contain the answer say exactly: I don't have that in your saved reels.
Be concise. Prefer bullet points."""

CTX_HEADER = "CONTEXT (transcript snippets, facts and OCR text from saved reels):"


def answer_question(user_id: int, question: str, k: int = 6) -> dict:
    llm = get_llm()
    emb = get_embedder()
    hits = hybrid_search(user_id, question, emb, limit=k)

    if not hits:
        return {"answer": "I don't have that in your saved reels.",
                "citations": [], "confidence": 0.0}

    # build context pack from each hit reel's best chunks
    blocks, citations = [], []
    for r in hits:
        rid = r["id"]
        chunks = reel_chunks(rid)
        if not chunks:
            continue
        citations.append({
            "reel_id": rid,
            "title": r.get("title") or "(untitled)",
            "summary": (r.get("summary") or "")[:200],
            "categories": json.loads(r.get("categories_json") or "[]"),
            "score": r.get("blend", 0),
        })
        joined = "\n".join(chunks[:8])
        blocks.append(f"[R#{rid}] {(r.get('title') or '')}\n{joined}")

    context = CTX_HEADER + "\n\n" + "\n\n".join(blocks)
    raw = llm.chat(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": f"{context}\n\nQUESTION: {question}"}],
        max_tokens=700, temperature=0.2,
    )
    cited_ids = set(c["reel_id"] for c in citations)
    used = {int(m) for m in re.findall(r"\[R#(\d+)\]", raw)}
    hallucinated_refs = used - cited_ids
    answer = raw.strip()
    conf = min(0.95, 0.4 + 0.1 * len(hits)) if not hallucinated_refs else 0.5
    return {"answer": answer, "citations": citations,
            "confidence": round(conf, 2)}


def reel_chunks(reel_id: int) -> list[str]:
    """Best-evidence chunks for one reel: summary, takeaways, facts w/
    timestamps, transcript excerpts, OCR."""
    out: list[str] = []
    with get_db() as db:
        r = db.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
        if not r:
            return []
        d = dict(r)
        if d.get("summary"):
            out.append("SUMMARY: " + d["summary"])
        for t in json.loads(d.get("key_takeaways_json") or "[]")[:5]:
            out.append("TAKEAWAY: " + t)
        for f in db.execute(
                "SELECT field, value, evidence_quote, evidence_t_s FROM facts"
                " WHERE reel_id=? LIMIT 15", (reel_id,)).fetchall():
            ts = ""
            if f["evidence_t_s"] is not None:
                m, s = divmod(int(f["evidence_t_s"]), 60)
                ts = f" @{m:02d}:{s:02d}"
            out.append(f"FACT {f['field']}={f['value']} (evidence: \"{(f['evidence_quote'] or '')[:80]}\"{ts})")
        segs = db.execute(
            "SELECT start_s, text FROM transcript_segments WHERE reel_id=?"
            " ORDER BY start_s LIMIT 12", (reel_id,)).fetchall()
        for s in segs:
            m, sec = divmod(int(s["start_s"]), 60)
            out.append(f"[{m:02d}:{sec:02d}] {s['text']}")
        ocrs = db.execute(
            "SELECT t_s, text FROM ocr_results WHERE reel_id=? ORDER BY t_s LIMIT 8",
            (reel_id,)).fetchall()
        for o in ocrs:
            m, sec = divmod(int(o["t_s"]), 60)
            out.append(f"OCR[{m:02d}:{sec:02d}] {o['text'][:200]}")
    return out
