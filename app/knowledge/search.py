"""Hybrid search: SQLite FTS5 keyword + local vector cosine semantic search."""
from __future__ import annotations

import math
import struct

from app.db.schema import get_db


def _vec(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob)//4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def keyword_search(user_id: int, query: str, limit: int = 30) -> list[dict]:
    fts_query = " OR ".join(
        f'"{w.replace(chr(34), "")}"' for w in query.split() if w.strip())
    if not fts_query:
        return []
    with get_db() as db:
        rows = db.execute(
            """
            SELECT r.id, r.title, r.summary, r.categories_json, r.status,
                   r.confidence, r.priority, r.starred, r.author_handle,
                   r.ingested_at,
                   bm25(reels_fts) AS rank
            FROM reels_fts f JOIN reels r ON r.id = f.rowid
            WHERE reels_fts MATCH ? AND r.user_id=?
            ORDER BY rank LIMIT ?
            """,
            (fts_query, user_id, limit),
        ).fetchall()
    return [dict(r) | {"match_type": "keyword"} for r in rows]


def semantic_search(user_id: int, query_vec: list[float],
                    limit: int = 30) -> list[dict]:
    with get_db() as db:
        rows = db.execute(
            "SELECT e.reel_id, e.vector, e.owner_type, e.text_used FROM embeddings e"
            " JOIN reels r ON r.id=e.reel_id WHERE r.user_id=?", (user_id,)
        ).fetchall()
    best: dict[int, float] = {}
    for row in rows:
        v = _vec(row["vector"])
        if len(v) != len(query_vec):
            continue
        s = cosine(query_vec, v)
        if s > best.get(row["reel_id"], 0):
            best[row["reel_id"]] = s
    if not best:
        return []
    ranked = sorted(best.items(), key=lambda kv: -kv[1])[:limit]
    out = []
    with get_db() as db:
        for rid, score in ranked:
            r = db.execute(
                "SELECT id, title, summary, categories_json, status, confidence,"
                " priority, starred, author_handle, ingested_at FROM reels"
                " WHERE id=?", (rid,)).fetchone()
            if r:
                out.append(dict(r) | {"score": round(score, 3),
                                      "match_type": "semantic"})
    return out


def hybrid_search(user_id: int, query: str, embedder, limit: int = 30,
                  category: str | None = None,
                  min_confidence: float | None = None) -> list[dict]:
    kw = {r["id"]: r for r in keyword_search(user_id, query, limit)}
    sem = {r["id"]: r for r in semantic_search(
        user_id, embedder.embed([query])[0], limit)}
    merged: dict[int, dict] = {}
    for rid, r in {**kw, **sem}.items():
        r = dict(r)
        k = kw.get(rid)
        s = sem.get(rid)
        r["kw_rank"] = list(kw).index(rid) + 1 if k else None
        r["sem_score"] = s["score"] if s else None
        # reciprocal-rank-ish blend
        blend = 0.0
        if k:
            blend += 1.0 / r["kw_rank"]
        if s:
            blend += s["score"]
        r["blend"] = round(blend, 4)
        merged[rid] = r
    results = sorted(merged.values(), key=lambda r: -r["blend"])
    if category:
        cat_l = category.lower()
        results = [r for r in results if cat_l in (r["categories_json"] or "").lower()]
    if min_confidence is not None:
        results = [r for r in results if (r["confidence"] or 0) >= min_confidence]
    return results[:limit]
