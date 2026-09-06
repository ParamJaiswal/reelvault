"""Pipeline stage handlers. Each maps to a queue stage and is idempotent.

Stages: ingest -> media -> transcribe -> ocr -> classify_extract -> embed -> finalize
Every handler updates reel.current_stage/progress so the UI shows live state.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from app.ai import providers
from app.core.config import settings
from app.db.queue import Queue
from app.db.schema import get_db
from app.knowledge.evidence import (HALLUCINATION_THRESHOLD, SourceSpan,
                                    confidence_score, find_evidence)
from app.knowledge.schemas import SCHEMA_FIELDS
from app.pipeline.fetch import FetchError, download_reel
from app.pipeline.media import (MediaError, extract_audio, ffprobe, make_thumb,
                                sample_frames, validate_video)

log = logging.getLogger("rv.pipeline")

EMAIL_RE = regex = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
URL_RE = re.compile(r"https?://[^\s\"'>)]+")
PHONE_RE = re.compile(r"(?:\+91[- ]?)?[6-9]\d{9}\b")
SKILL_HINTS = ["python", "sql", "excel", "power bi", "tableau", "machine learning",
               "deep learning", "nlp", "pandas", "numpy", "react", "java",
               "aws", "docker", "git", "communication"]


def ev(db, reel_id: int, stage: str, message: str, level: str = "info", **data):
    db.execute(
        "INSERT INTO processing_events(reel_id, stage, level, message, data_json)"
        " VALUES (?,?,?,?,?)",
        (reel_id, stage, level, message[:500], json.dumps(data) if data else None),
    )


def set_reel(db, reel_id: int, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE reels SET {sets} WHERE id=?",
               (*fields.values(), reel_id))


def get_reel(db, reel_id: int) -> dict:
    return dict(db.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone())


# ------------------------------------------------------------------ stages
def stage_ingest(reel_id: int, payload: dict) -> None:
    with get_db() as db:
        reel = get_reel(db, reel_id)
        set_reel(db, reel_id, status="processing", current_stage="ingest", progress=0.05)

    if reel["media_path"] and Path(reel["media_path"]).exists():
        with get_db() as db:
            ev(db, reel_id, "ingest", "local file present; skipping download")
        return

    if not reel["source_url"]:
        raise MediaError("Nothing to fetch: no URL and no file.")
    try:
        meta = download_reel(reel["source_url"], reel["shortcode"] or f"reel{reel_id}")
    except FetchError as e:
        with get_db() as db:
            ev(db, reel_id, "ingest", str(e), level="warn", code="FETCH_UNAVAILABLE")
            # metadata-only mode: keep the reel record, no media pipeline
            set_reel(db, reel_id, summary=str(e), key_takeaways_json=json.dumps([
                "Media couldn't be fetched automatically.",
                "Attach the video file from Settings > this reel to complete processing."]),
                action_items_json=json.dumps(["Attach video file manually"]),
                categories_json=json.dumps(["Other"]), confidence=0.2,
                status="completed", current_stage="done", progress=1.0,
                completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            # cancel the remaining queued stages — nothing more to do
            db.execute("DELETE FROM jobs WHERE reel_id=? AND status='queued'",
                       (reel_id,))
            db.execute(
                "INSERT INTO notifications(user_id, reel_id, kind, title, body)"
                " SELECT user_id, ?, 'fetch_failed', 'Reel saved — media unavailable',"
                " COALESCE(?, '') FROM reels WHERE id=?",
                (reel_id, str(e)[:200], reel_id))
        return  # job completes successfully; reel is in a defined terminal state

    with get_db() as db:
        caption = meta.get("caption") or reel["caption"]
        author = meta.get("author_handle") or reel["author_handle"]
        set_reel(db, reel_id, media_path=meta["path"], caption=caption,
                 author_handle=author,
                 title=reel["title"] or (meta.get("title") or "")[:120])
        ev(db, reel_id, "ingest", f"downloaded {Path(meta['path']).name}")


def stage_media(reel_id: int, payload: dict) -> None:
    with get_db() as db:
        reel = get_reel(db, reel_id)
        set_reel(db, reel_id, current_stage="media", progress=0.15)
        mp = reel["media_path"]

    if not mp or not Path(mp).exists():
        raise MediaError("No media on disk for media stage")

    info = validate_video(mp)
    wav = settings.media_dir / "audio" / f"r{reel_id}.wav"
    thumb = settings.media_dir / "frames" / f"thumb_r{reel_id}.jpg"
    extract_audio(mp, str(wav))
    make_thumb(mp, str(thumb))
    frames = sample_frames(mp, reel_id, info["duration_s"])

    content_hash = None
    try:
        content_hash = sha_of_file(mp)[:16] + f":{int(info['duration_s'])}"
    except Exception:
        pass

    with get_db() as db:
        db.execute("DELETE FROM frames WHERE reel_id=?", (reel_id,))
        for fr in frames:
            db.execute(
                "INSERT INTO frames(reel_id, t_s, path) VALUES (?,?,?)",
                (reel_id, fr["t_s"], fr["path"]),
            )
        set_reel(db, reel_id, duration_s=info["duration_s"], language=None,
                 thumb_path=str(thumb),
                 content_hash=content_hash)
        ev(db, reel_id, "media",
           f"validated {info['width']}x{info['height']}, {info['duration_s']:.1f}s,"
           f" audio={info['has_audio']}, frames={len(frames)}")


def phash_of_thumb(path: str) -> str:
    from app.pipeline.media import phash
    return phash(path)


def sha_of_file(path: str, chunk: int = 1 << 20) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def stage_transcribe(reel_id: int, payload: dict) -> None:
    with get_db() as db:
        reel = get_reel(db, reel_id)
        set_reel(db, reel_id, current_stage="transcribe", progress=0.35)
        wav = settings.media_dir / "audio" / f"r{reel_id}.wav"
    if not wav.exists():
        ev_local = None
        raise MediaError("No extracted audio — cannot transcribe")

    result = providers.get_transcriber().transcribe(str(wav), reel_id=reel_id)
    segs = result.get("segments", [])
    with get_db() as db:
        db.execute("DELETE FROM transcript_segments WHERE reel_id=?", (reel_id,))
        for s in segs:
            db.execute(
                "INSERT INTO transcript_segments(reel_id, start_s, end_s, text,"
                " avg_logprob, no_speech_prob) VALUES (?,?,?,?,?,?)",
                (reel_id, s["start"], s["end"], s["text"],
                 s.get("avg_logprob"), s.get("no_speech_prob")),
            )
        avg_conf = (
            sum(1 - min(1, max(0, -s["avg_logprob"] / 4)) for s in segs) / len(segs)
            if segs else 0.0
        )
        set_reel(db, reel_id, language=result.get("language"),
                 progress=0.5)
        ev(db, reel_id, "transcribe",
           f"{len(segs)} segments, lang={result.get('language')},"
           f" conf={avg_conf:.2f}")
        if avg_conf and avg_conf < 0.3:
            ev(db, reel_id, "transcribe", "low transcription confidence",
               level="warn")


def stage_ocr(reel_id: int, payload: dict) -> None:
    ocr = providers.get_ocr()
    if ocr is None:
        with get_db() as db:
            ev(db, reel_id, "ocr", "OCR disabled by config")
        return
    with get_db() as db:
        set_reel(db, reel_id, current_stage="ocr", progress=0.55)
        frames = [dict(r) for r in db.execute(
            "SELECT * FROM frames WHERE reel_id=? ORDER BY t_s", (reel_id,))]
    n_lines = 0
    seen_norm = {}
    rows_to_insert = []
    for fr in frames:
        items = ocr.read_image(fr["path"], reel_id=reel_id)
        joined = []
        for it in items:
            t = it["text"].strip()
            if len(t) < 2:
                continue
            joined.append((t, it["conf"]))
        frame_text = " ".join(t for t, _ in joined)
        if not frame_text:
            continue
        key = "".join(c for c in frame_text.lower() if c.isalnum())[:80]
        # dedupe near-identical consecutive overlays, keep earliest timestamp
        if key in seen_norm:
            continue
        seen_norm[key] = True
        rows_to_insert.append((reel_id, fr["id"], fr["t_s"], frame_text[:2000],
                               round(sum(c for _, c in joined) / len(joined), 3)))
        n_lines += 1
    with get_db() as db:
        db.execute("DELETE FROM ocr_results WHERE reel_id=?", (reel_id,))
        for row in rows_to_insert:
            db.execute(
                "INSERT INTO ocr_results(reel_id, frame_id, t_s, text, conf)"
                " VALUES (?,?,?,?,?)", row)
        ev(db, reel_id, "ocr", f"{n_lines} unique text overlays found")


def stage_classify_extract(reel_id: int, payload: dict) -> None:
    with get_db() as db:
        reel = get_reel(db, reel_id)
        set_reel(db, reel_id, current_stage="classify_extract", progress=0.65)

    # ------- gather unified representation -------
    with get_db() as db:
        segs = [dict(r) for r in db.execute(
            "SELECT start_s, end_s, text FROM transcript_segments"
            " WHERE reel_id=? ORDER BY start_s", (reel_id,))]
        ocrs = [dict(r) for r in db.execute(
            "SELECT t_s, text FROM ocr_results WHERE reel_id=? ORDER BY t_s",
            (reel_id,))]
    caption = reel["caption"] or ""
    transcript = "\n".join(f"[{fmt_ts(s['start_s'])}] {s['text']}" for s in segs)
    overlay = "\n".join(f"[{fmt_ts(o['t_s'])}] OCR: {o['text']}" for o in ocrs)

    unified = f"CAPTION: {caption}\n\nTRANSCRIPT:\n{transcript or '(no speech detected)'}\n\nON-SCREEN TEXT:\n{overlay or '(none)'}"

    # ------- regex pre-pass (cheap deterministic extraction) -------
    pre = {
        "emails": sorted(set(EMAIL_RE.findall(unified)))[:4],
        "urls": sorted(set(URL_RE.findall(unified)))[:4],
        "phones": sorted(set(PHONE_RE.findall(unified)))[:3],
        "skills": [sk for sk in SKILL_HINTS if sk in unified.lower()],
    }

    # ------- SLM classification + schema selection + extraction -------
    from app.ai.router import get_router

    llm = providers.get_llm()
    if not llm.available():
        raise RuntimeError("LLM server unreachable — is llama-server running?")
    router = get_router()
    cls, served_by = router.classify(unified, reel_id=reel_id)
    cats = [c for c in cls.get("categories", [])][:4]
    schema_type = str(cls.get("primary_schema") or "generic").strip().lower()
    if schema_type not in SCHEMA_FIELDS:
        schema_type = "generic"  # never trust unvalidated model output

    extraction = router.extract(unified, schema_type, reel_id=reel_id)
    if not extraction:
        raise ValueError("SLM returned unparseable JSON twice")

    # ------- Evidence Ledger verification -------
    spans = build_spans(segs, ocrs, caption)
    verified_facts = []
    dropped = []
    for f in extraction.get("facts", []):
        val, quote = (f.get("value") or "").strip(), (f.get("quote") or "").strip()
        if not val:
            continue
        m = find_evidence(quote, val, spans)
        if m.similarity < HALLUCINATION_THRESHOLD:
            dropped.append({"field": f.get("field"), "value": val})
            continue
        conf = confidence_score(
            m.similarity, m.n_sources_agreeing, None,
            has_timestamp=bool(m.span and m.span.t_s is not None))
        verified_facts.append({
            "schema_type": schema_type if schema_type != "generic" else "note",
            "field": (f.get("field") or "note")[:60],
            "value": val[:300],
            "evidence_source": (m.span.source if m.span else "transcript"),
            "evidence_quote": (m.span.text[:400] if m.span else quote),
            "evidence_t_s": m.span.t_s if m.span else None,
            "confidence": conf,
        })
    # regex-anchored facts always pass (deterministic evidence)
    for url in pre["urls"]:
        verified_facts.append({
            "schema_type": schema_type if schema_type != "generic" else "note",
            "field": "link", "value": url, "evidence_source": "caption"
            if url in caption else "transcript",
            "evidence_quote": url, "evidence_t_s": None,
            "confidence": 0.95,
        })
    for em in pre["emails"]:
        verified_facts.append({
            "schema_type": schema_type if schema_type != "generic" else "note",
            "field": "email", "value": em,
            "evidence_source": "caption" if em in caption else "transcript",
            "evidence_quote": em, "evidence_t_s": None, "confidence": 0.95,
        })

    overall_conf = (
        round(sum(f["confidence"] for f in verified_facts) / len(verified_facts), 2)
        if verified_facts else 0.35
    )

    # priority score for the Opportunity Radar
    priority = radar_priority(schema_type, verified_facts, extraction)

    # deadline intelligence (deterministic — models never do date math)
    from app.knowledge.deadlines import best_deadline

    dl = best_deadline(verified_facts, unified)
    dl_cols = {"deadline_iso": dl.date.isoformat() if dl and dl.date else None,
               "reminder_iso": dl.reminder_date.isoformat() if dl and dl.reminder_date else None,
               "deadline_raw": dl.raw[:120] if dl else None}

    with get_db() as db:
        set_reel(db, reel_id,
                 summary=(extraction.get("summary") or "")[:1500],
                 key_takeaways_json=json.dumps(extraction.get("key_takeaways", [])[:8]),
                 action_items_json=json.dumps(extraction.get("action_items", [])[:8]),
                 categories_json=json.dumps(cats or ["Other"]),
                 confidence=overall_conf,
                 priority=priority, **dl_cols)
        # idempotent: replace previous extraction rather than duplicate it
        db.execute("DELETE FROM facts WHERE reel_id=?", (reel_id,))
        for f in verified_facts:
            db.execute(
                "INSERT INTO facts(reel_id, schema_type, field, value,"
                " evidence_source, evidence_quote, evidence_t_s, confidence)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (reel_id, f["schema_type"], f["field"], f["value"],
                 f["evidence_source"], f["evidence_quote"], f["evidence_t_s"],
                 f["confidence"]))
        for ent in (extraction.get("entities") or [])[:12]:
            name = (ent.get("name") or "").strip()[:80]
            kind = (ent.get("kind") or "other").strip().lower()[:30]
            if not name:
                continue
            row = db.execute(
                "SELECT id FROM entities WHERE user_id=? AND norm_name=? AND kind=?",
                (reel["user_id"], name.lower(), kind)).fetchone()
            if row:
                eid = row["id"]
            else:
                cur = db.execute(
                    "INSERT INTO entities(user_id, norm_name, kind, display_name)"
                    " VALUES (?,?,?,?)",
                    (reel["user_id"], name.lower(), kind, name))
                eid = cur.lastrowid
            db.execute(
                "INSERT OR IGNORE INTO reel_entities(reel_id, entity_id,"
                " evidence_quote, evidence_t_s) VALUES (?,?,?,?)",
                (reel_id, eid, (ent.get("quote") or "")[:300], None))

    with get_db() as db:
        msg = (f"classified {cats}, schema={schema_type}, facts kept="
               f"{len(verified_facts)}, dropped_as_hallucination={len(dropped)}")
        ev(db, reel_id, "classify_extract", msg,
           dropped=[d["value"][:40] for d in dropped][:10],
           served_by=served_by)


def radar_priority(schema_type: str, facts: list[dict], extraction: dict) -> int:
    """Opportunity Radar urgency: deadlines soon & actionable > evergreen."""
    score = 0
    txt = json.dumps(facts).lower() + json.dumps(
        extraction.get("action_items", [])).lower()
    if schema_type == "job":
        score += 50
    if schema_type == "event":
        score += 40
    if any(k in txt for k in ["deadline", "last date", "apply before", "closes"]):
        score += 30
    if any(k in txt for k in ["freshers", "fresher", "0-1 years", "student"]):
        score += 15
    if any(k in txt for k in ["intern", "internship"]):
        score += 10
    return min(score, 99)


def stage_embed(reel_id: int, payload: dict) -> None:
    emb = providers.get_embedder()
    with get_db() as db:
        set_reel(db, reel_id, current_stage="embed", progress=0.85)
        reel = get_reel(db, reel_id)
        existing = db.execute(
            "SELECT COUNT(*) c FROM embeddings WHERE reel_id=?", (reel_id,)).fetchone()["c"]
        if existing:
            ev(db, reel_id, "embed", "already embedded (idempotent skip)")
            return
        segs = [dict(r) for r in db.execute(
            "SELECT id, text FROM transcript_segments WHERE reel_id=? ORDER BY start_s",
            (reel_id,))]
        ocrs = [dict(r) for r in db.execute(
            "SELECT id, t_s, text FROM ocr_results WHERE reel_id=? ORDER BY t_s",
            (reel_id,))]
        facts = [dict(r) for r in db.execute(
            "SELECT id, field, value FROM facts WHERE reel_id=?", (reel_id,))]

    texts, metas = [], []
    body = f"{reel['title']}. {reel['summary']} " + " ".join(
        json.loads(reel['key_takeaways_json']))
    texts.append(body); metas.append(("reel", reel_id))
    for s in segs:
        texts.append(s["text"]); metas.append(("segment", s["id"]))
    for o in ocrs:
        texts.append(o["text"]); metas.append(("ocr", o["id"]))
    for f in facts:
        texts.append(f"{f['field']}: {f['value']}"); metas.append(("fact", f["id"]))
    if not any(t.strip() for t in texts):
        with get_db() as db:
            ev(db, reel_id, "embed", "nothing to embed")
        return
    vecs = emb.embed(texts, reel_id=reel_id)
    with get_db() as db:
        import struct
        for (owner_type, owner_id), vec, txt in zip(metas, vecs, texts):
            blob = struct.pack(f"<{len(vec)}f", *vec)
            db.execute(
                "INSERT INTO embeddings(owner_type, owner_id, reel_id, text_used,"
                " dim, vector, model) VALUES (?,?,?,?,?,?,?)",
                (owner_type, owner_id, reel_id, txt[:1000], len(vec), blob,
                 emb.name))
    with get_db() as db:
        ev(db, reel_id, "embed", f"embedded {len(texts)} chunks @ {emb.dim}d")


def stage_finalize(reel_id: int, payload: dict) -> None:
    # duplicate check runs at finalize: cheapest similarity first (hash/url),
    # then embedding cosine against other reels' summary vectors.
    with get_db() as db:
        reel = get_reel(db, reel_id)
        dup_of = None
        if reel["shortcode"]:
            r = db.execute(
                "SELECT id FROM reels WHERE shortcode=? AND id!=? AND status='completed'"
                " AND duplicate_of IS NULL LIMIT 1",
                (reel["shortcode"], reel_id)).fetchone()
            dup_of = r["id"] if r else None
        if dup_of is None and reel["content_hash"]:
            r = db.execute(
                "SELECT id FROM reels WHERE content_hash=? AND id!=?"
                " AND status='completed' AND duplicate_of IS NULL LIMIT 1",
                (reel["content_hash"], reel_id)).fetchone()
            dup_of = r["id"] if r else None

    if dup_of is None:
        dup_of = semantic_duplicate_check(reel_id)

    with get_db() as db:
        if dup_of:
            set_reel(db, reel_id, status="duplicate", duplicate_of=dup_of,
                     current_stage="done", progress=1.0,
                     completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            db.execute("DELETE FROM jobs WHERE reel_id=? AND status!='done'", (reel_id,))
            ev(db, reel_id, "finalize", f"duplicate of reel #{dup_of}")
            db.execute(
                "INSERT INTO notifications(user_id, reel_id, kind, title, body)"
                " SELECT user_id, ?, 'duplicate', 'Similar Reel already saved',"
                " 'Merged into reel #'||? FROM reels WHERE id=?",
                (reel_id, dup_of, reel_id))
        else:
            set_reel(db, reel_id, status="completed", current_stage="done",
                     progress=1.0,
                     completed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
            db.execute(
                "INSERT INTO notifications(user_id, reel_id, kind, title, body)"
                " SELECT user_id, ?, 'processed', 'Reel processed: '||COALESCE(NULLIF(title,''),'Reel'),"
                " COALESCE(summary,'') FROM reels WHERE id=?", (reel_id, reel_id))
            ev(db, reel_id, "finalize", "processing complete")


def semantic_duplicate_check(reel_id: int) -> int | None:
    import math
    import struct
    with get_db() as db:
        mine = db.execute(
            "SELECT vector FROM embeddings WHERE owner_type='reel' AND reel_id=?",
            (reel_id,)).fetchone()
        if not mine:
            return None
        others = db.execute(
            "SELECT e.reel_id, e.vector FROM embeddings e JOIN reels r ON r.id=e.reel_id"
            " WHERE e.owner_type='reel' AND e.reel_id!=? AND r.status='completed'"
            " AND r.duplicate_of IS NULL", (reel_id,)).fetchall()
    def cos(a, b):
        n = len(a)
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0
    mv = list(struct.unpack(f"<{len(mine['vector'])//4}f", mine["vector"]))
    best_id, best = None, 0.93  # threshold
    for o in others:
        ov = list(struct.unpack(f"<{len(o['vector'])//4}f", o["vector"]))
        if len(ov) != len(mv):
            continue
        s = cos(mv, ov)
        if s > best:
            best, best_id = s, o["reel_id"]
    return best_id


# ------------------------------------------------------------------ utils
def fmt_ts(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def safe_json(raw: str, fallback: dict) -> dict:
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return fallback


def build_spans(segs, ocrs, caption) -> list[SourceSpan]:
    spans = [SourceSpan(text=s["text"], t_s=s["start_s"], source="transcript")
             for s in segs]
    spans += [SourceSpan(text=o["text"], t_s=o["t_s"], source="ocr") for o in ocrs]
    if caption:
        spans.append(SourceSpan(text=caption, t_s=None, source="caption"))
    return spans


def register_all(q: Queue) -> None:
    q.register("ingest", stage_ingest)
    q.register("media", stage_media)
    q.register("transcribe", stage_transcribe)
    q.register("ocr", stage_ocr)
    q.register("classify_extract", stage_classify_extract)
    q.register("embed", stage_embed)
    q.register("finalize", stage_finalize)
