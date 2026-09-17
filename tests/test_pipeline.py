"""Integration tests: DB pipeline mechanics + evidence flow through stages.
AI-model stages are exercised in the live E2E suite; these verify plumbing
with deterministic fakes so they run anywhere, fast."""
import json

from app.db.queue import Queue
from app.db.schema import get_db


class FakeLLM:
    name = "fake"
    def __init__(self, reply): self.reply = reply; self.calls = []
    def available(self): return True
    def chat(self, messages, **kw):
        self.calls.append(messages)
        return self.reply


def mk_reel(user_id, media_path=None, shortcode="TSTaaa111"):
    with get_db() as db:
        db.execute("INSERT OR IGNORE INTO users(id, username, api_key_hash)"
                   " VALUES (1,'t','')")
        uid = user_id or 1
        if not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
            cur = db.execute("INSERT INTO users(username, api_key_hash)"
                             " VALUES ('t2','')")
            uid = cur.lastrowid
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " media_path) VALUES (?,?,?,?,?)",
            (uid, "file", f"https://www.instagram.com/reel/{shortcode}/",
             shortcode, media_path))
        return cur.lastrowid


class TestQueue:
    def test_enqueue_claim_complete(self, tmp_db):
        q = Queue()
        rid = mk_reel(1)
        q.enqueue(rid, ["media"])
        job = q.claim("w")
        assert job and job["stage"] == "media" and job["reel_id"] == rid
        q.complete(job["id"])
        assert q.claim("w") is None
        q.close()

    def test_retry_backoff_then_dead(self, tmp_db, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "retry_backoff_s", 0)
        q = Queue()
        rid = mk_reel(1)
        q.enqueue(rid, ["ocr"])
        jid = q.claim("w")["id"]
        assert q.fail(jid, "boom") == "queued"      # retry 1
        jid = q.claim("w")["id"]
        assert q.fail(jid, "boom") == "queued"      # retry 2
        jid = q.claim("w")["id"]
        assert q.fail(jid, "boom") == "dead"        # exhausted -> dead letter
        stats = q.stats()
        assert stats.get("dead") == 1
        # admin retry resurrects
        assert q.retry_stage(rid, "ocr") is True
        assert q.claim("w") is not None
        q.close()

    def test_stale_running_job_reclaimed(self, tmp_db, monkeypatch):
        import time

        from app.core.config import settings
        settings.stale_job_timeout_s = 0  # everything running counts as stale
        q = Queue()
        rid = mk_reel(1)
        q.enqueue(rid, ["embed"])
        first = q.claim("worker-a")
        time.sleep(0.02)               # ensure heartbeat < stale cutoff
        second = q.claim("worker-b")   # stale heartbeat -> reclaimable
        assert second["id"] == first["id"]
        settings.stale_job_timeout_s = 1800
        q.close()


class TestStageFlow:
    def test_ingest_metadata_only_mode_on_fetch_failure(self, tmp_db, monkeypatch):
        """A dead URL must end in a defined terminal state, not a crash loop."""
        from app.pipeline import stages
        from app.pipeline.fetch import FetchError

        def fake_download(url, sc):
            raise FetchError("Couldn't download this Reel.")
        monkeypatch.setattr(stages, "download_reel", fake_download)

        rid = mk_reel(1, media_path=None, shortcode="DEADbeef00")
        with get_db() as db:
            db.execute("UPDATE reels SET media_path=NULL WHERE id=?", (rid,))
        stages.stage_ingest(rid, {})
        with get_db() as db:
            r = dict(db.execute("SELECT * FROM reels WHERE id=?", (rid,)).fetchone())
        assert r["status"] == "completed"
        assert "attach" in json.dumps(json.loads(r["action_items_json"])).lower()

    def test_classify_extract_verifies_evidence(self, tmp_db, monkeypatch, sample_user):
        """Facts without source support get dropped by the Evidence Ledger."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "Hiring data analysts",
            "key_takeaways": ["Zylker hiring"],
            "action_items": [],
            "categories": ["Job"],
            "facts": [
                {"field": "role", "value": "Data Analyst",
                 "quote": "hiring Data Analyst interns"},
                {"field": "salary", "value": "1 crore per month",
                 "quote": "they pay one crore monthly"},   # hallucination
            ],
            "entities": [{"name": "Zylker Analytics", "kind": "company"}],
        })
        fl = FakeLLM(reply)
        monkeypatch.setattr(stages.providers, "get_llm", lambda: fl)

        rid = mk_reel(sample_user, shortcode="EVIDtest01")
        with get_db() as db:
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,1,4,'Zylker Analytics is hiring Data Analyst interns')",
                (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            facts = [dict(r) for r in db.execute(
                "SELECT * FROM facts WHERE reel_id=?", (rid,))]
            kept = {f["field"]: f for f in facts}
            assert "role" in kept
            assert kept["role"]["evidence_source"] == "transcript"
            assert kept["role"]["evidence_t_s"] == 1.0
            assert "salary" not in kept          # dropped as hallucination
            ents = [dict(r) for r in db.execute(
                "SELECT e.norm_name FROM reel_entities re JOIN entities e"
                " ON e.id=re.entity_id WHERE re.reel_id=?", (rid,))]
        assert any(e["norm_name"] == "zylker analytics" for e in ents)

    def test_classify_extract_logs_drop_reason(self, tmp_db, monkeypatch,
                                               sample_user):
        """Dropped facts record WHY (quote + similarity) in processing_events,
        not just the value - without this, drop root-causing needs a model
        re-run (Phase 7 observability fix)."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "Hiring data analysts",
            "key_takeaways": [], "action_items": [], "categories": ["Job"],
            "facts": [
                {"field": "salary", "value": "1 crore per month",
                 "quote": "they pay one crore monthly"},   # hallucination
            ],
            "entities": [],
        })
        fl = FakeLLM(reply)
        monkeypatch.setattr(stages.providers, "get_llm", lambda: fl)

        rid = mk_reel(sample_user, shortcode="DROPlog001")
        with get_db() as db:
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,1,4,'Zylker Analytics is hiring Data Analyst interns')",
                (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            events = [dict(r) for r in db.execute(
                "SELECT * FROM processing_events WHERE reel_id=?"
                " AND stage='classify_extract'", (rid,))]
        dropped_payload = None
        for e in events:
            data = json.loads(e["data_json"] or "{}")
            if data.get("dropped"):
                dropped_payload = data["dropped"][0]
        assert dropped_payload is not None, "no dropped-fact event recorded"
        assert dropped_payload["value"] == "1 crore per month"
        assert "one crore" in dropped_payload["quote"]
        assert dropped_payload["sim"] < 0.45

    def test_fabricated_claim_with_real_quote_is_not_persisted(
            self, tmp_db, monkeypatch, sample_user):
        from app.pipeline import stages

        quote = "Download your LinkedIn connections for networking"
        reply = json.dumps({
            "summary": "LinkedIn networking", "categories": ["Tutorial"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "entities": [], "facts": [
                {"field": "company", "value": "Zylker", "quote": quote},
                {"field": "topic", "value": "LinkedIn networking", "quote": quote},
            ],
        })
        monkeypatch.setattr(stages.providers, "get_llm", lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="CLAIMtest01")
        with get_db() as db:
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,0,5,?)", (rid, quote))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            facts = [dict(r) for r in db.execute(
                "SELECT * FROM facts WHERE reel_id=?", (rid,))]
        assert len(facts) == 1
        assert facts[0]["value"] == "LinkedIn networking"
        assert facts[0]["evidence_quote"] == quote
        assert facts[0]["evidence_t_s"] == 0.0

    def test_finalize_merges_duplicates_by_shortcode(self, tmp_db, sample_user):
        from app.pipeline import stages
        keep = mk_reel(sample_user, shortcode="DUPabc123")
        stages.stage_finalize(keep, {})
        dup = mk_reel(sample_user, shortcode="DUPabc123")
        stages.stage_finalize(dup, {})
        with get_db() as db:
            d = dict(db.execute("SELECT status, duplicate_of FROM reels"
                                " WHERE id=?", (dup,)).fetchone())
        assert d["status"] == "duplicate"
        assert d["duplicate_of"] == keep
