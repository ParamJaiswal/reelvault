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
                "SELECT * FROM facts WHERE reel_id=? ORDER BY field, value",
                (rid,))]
        # Phase 7 deterministic skill recovery adds one 'technologies' fact
        # for the verbatim "linkedin" mention - that is a source-anchored
        # regex fact, not a model claim. The fabrication guard must still
        # hold: the company=Zylker claim never persists.
        assert sorted((f["field"], f["value"]) for f in facts) == [
            ("technologies", "linkedin"), ("topic", "LinkedIn networking")]
        assert all(f["field"] != "company" for f in facts)
        topic = next(f for f in facts if f["field"] == "topic")
        assert topic["evidence_quote"] == quote
        assert topic["evidence_t_s"] == 0.0

    def test_education_deadline_replay_uses_source_fallback(
            self, tmp_db, monkeypatch, sample_user):
        from datetime import datetime
        from pathlib import Path

        from app.knowledge import deadlines
        from app.pipeline import stages
        from test_ai_eval import _unified_and_spans
        from app.knowledge.evidence import find_evidence

        case = json.loads((Path(__file__).parent / "golden" / "edu-03.json")
                          .read_text(encoding="utf-8"))
        _, spans = _unified_and_spans(case)
        # Deadline entry isolated from the saved field-keyed trial output.
        entry = {"value": "October 5th", "quote": "[00:15] OCR: CLOSES OCT 5"}
        assert find_evidence(entry["quote"], entry["value"], spans).similarity == 0
        today = datetime(2026, 9, 6)
        expected = deadlines.parse_deadline("5th of October", today=today).date
        assert deadlines.parse_deadline(entry["value"], today=today).date == expected
        original = deadlines.best_deadline
        monkeypatch.setattr(deadlines, "best_deadline",
                            lambda facts, text: original(facts, text, today=today))
        replies = iter([
            json.dumps({"categories": ["Educational"], "primary_schema": "education"}),
            json.dumps({"deadline": [entry]}),
        ])
        llm = FakeLLM("")
        monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: next(replies))
        monkeypatch.setattr(stages.providers, "get_llm", lambda: llm)
        rid = mk_reel(sample_user, shortcode="EDUdeadline")
        with get_db() as db:
            db.execute("UPDATE reels SET caption=? WHERE id=?", (case["caption"], rid))
            for segment in case["transcript"]:
                db.execute("INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                           " VALUES (?,?,?,?)",
                           (rid, segment["t"], segment["t"] + 1, segment["text"]))
            for overlay in case["ocr"]:
                db.execute("INSERT INTO ocr_results(reel_id,t_s,text,conf) VALUES (?,?,?,?)",
                           (rid, overlay["t"], overlay["text"], 1.0))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            row = db.execute("SELECT deadline_iso, deadline_raw FROM reels WHERE id=?",
                             (rid,)).fetchone()
            persisted_facts = [dict(r) for r in db.execute(
                "SELECT field, value FROM facts WHERE reel_id=?", (rid,))]
        # The zero-supported deadline entry must not persist. Only the
        # deterministic skill regex may add facts ("sql" appears verbatim
        # in edu-03's source) - Phase 7 platform recovery, by design.
        assert all(f["field"] == "technologies" for f in persisted_facts)
        assert "deadline" not in {f["value"].lower() for f in persisted_facts}
        assert row["deadline_iso"] is not None
        persisted = datetime.fromisoformat(row["deadline_iso"]).date()
        print(f"edu-03 replay: evidence_score=0; parsed_date={expected.date()}; "
              f"persisted_deadline={persisted}; golden_date={expected.date()}; "
              f"source_fallback={row['deadline_raw']!r}")
        assert persisted == expected.date()

    def test_entity_hallucination_is_not_persisted(
            self, tmp_db, monkeypatch, sample_user):
        """Entities are model output too: a name no source span supports must
        not enter the entity graph, while supported entities keep evidence."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "s", "categories": ["Tutorial"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "facts": [], "entities": [
                {"name": "Zylker", "kind": "company"},
                {"name": "LinkedIn", "kind": "tool",
                 "quote": "mine your LinkedIn connections"},
            ],
        })
        monkeypatch.setattr(stages.providers, "get_llm",
                            lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="ENTtest001")
        with get_db() as db:
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,0,5,'I mine my LinkedIn connections daily')", (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            ents = [dict(r) for r in db.execute(
                "SELECT e.norm_name, re.evidence_t_s FROM reel_entities re"
                " JOIN entities e ON e.id=re.entity_id WHERE re.reel_id=?",
                (rid,))]
            events = [json.loads(r["data_json"] or "{}") for r in db.execute(
                "SELECT data_json FROM processing_events WHERE reel_id=?"
                " AND stage='classify_extract'", (rid,))]
        assert ents == [("linkedin", 0.0)] or [
            (e["norm_name"], e["evidence_t_s"]) for e in ents
        ] == [("linkedin", 0.0)]
        assert any(d.get("value") == "Zylker" for e in events
                   for d in e.get("dropped", []))

    def test_skill_matcher_rejects_substring_false_positives(
            self, tmp_db, monkeypatch, sample_user):
        """Word-boundary regex: 'pythonic' must NOT yield technologies=python,
        'digital' must NOT yield technologies=git. Only verbatim whole-word
        mentions (e.g. 'Python' as a token) become facts."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "s", "categories": ["Tutorial"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "entities": [], "facts": [],
        })
        monkeypatch.setattr(stages.providers, "get_llm", lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="SKILLsubstr01")
        with get_db() as db:
            # Every "skill" is a substring inside a larger token; none are
            # whole-word matches. Verbatim: "tableau" appears as a word.
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,0,5,"
                "'Learn pythonic ways; great digital art; we ship fast.')",
                (rid,))
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,10,12,'She painted a tableau of dishes.')",
                (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            values = {r["value"] for r in db.execute(
                "SELECT value FROM facts WHERE reel_id=? AND field='technologies'",
                (rid,))}
        assert "python" not in values, f"pythonic matched python; got {values}"
        assert "git" not in values, f"digital matched git; got {values}"
        assert "tableau" in values, f"verbatim 'tableau' must survive; got {values}"

    def test_skill_matcher_treats_punctuation_as_a_word_boundary(self, tmp_db,
                                                                  monkeypatch,
                                                                  sample_user):
        """Documents the deliberate boundary rule: only letters and digits
        bind a token to its neighbour. So 'python-based' and 'git-bashing'
        ARE mentions (hyphen compounds name the tool), while 'pythonic' and
        'digital' are not. Picking this rule keeps the matcher aligned with
        evidence.py's token-boundary philosophy."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "s", "categories": ["Tutorial"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "entities": [], "facts": [],
        })
        monkeypatch.setattr(stages.providers, "get_llm", lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="SKILLhyph01")
        with get_db() as db:
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,7,9,'A python-based stack; daily git-bashing.')"
                , (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT value, evidence_t_s FROM facts WHERE reel_id=?"
                " AND field='technologies'", (rid,))]
        assert {r["value"] for r in rows} == {"python", "git"}
        assert all(r["evidence_t_s"] == 7.0 for r in rows)

    def test_skill_matcher_attributes_source_and_timestamp(
            self, tmp_db, monkeypatch, sample_user):
        """Skills matched in OCR must claim evidence_source='ocr' with the
        OCR row's t_s, not the hardcoded ('transcript', None) the old code
        wrote. Skills from caption claim evidence_source='caption'."""
        from app.pipeline import stages

        reply = json.dumps({
            "summary": "s", "categories": ["Tutorial"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "entities": [], "facts": [],
        })
        monkeypatch.setattr(stages.providers, "get_llm", lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="SKILLattr01")
        with get_db() as db:
            db.execute("UPDATE reels SET caption=? WHERE id=?",
                       ("Python is in the description here.", rid))
            db.execute(
                "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                " VALUES (?,0,5,'Hello everyone.')", (rid,))
            db.execute(
                "INSERT INTO ocr_results(reel_id,t_s,text,conf)"
                " VALUES (?,17.5,'Our tutorial uses AWS and docker',1.0)",
                (rid,))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT value, evidence_source, evidence_t_s, evidence_quote"
                " FROM facts"
                " WHERE reel_id=? AND field='technologies'"
                " ORDER BY value", (rid,))]
        by_val = {r["value"]: r for r in rows}
        assert "python" in by_val, f"caption skill missing; got {rows}"
        assert by_val["python"]["evidence_source"] == "caption"
        assert by_val["python"]["evidence_t_s"] is None
        for name in ("aws", "docker"):
            assert name in by_val, f"OCR skill {name} missing; got {rows}"
            assert by_val[name]["evidence_source"] == "ocr"
            assert by_val[name]["evidence_t_s"] == 17.5
            assert "AWS" in by_val[name]["evidence_quote"] or "docker" in (
                by_val[name]["evidence_quote"].lower())

    def test_edu05_platform_recovered_without_model_help(
            self, tmp_db, monkeypatch, sample_user):
        """The accepted Phase 7 limitation was that Qwen2.5-3B never emits
        LinkedIn for edu-05, and the golden harness cannot see the fix because
        it replicates only the fact loop. This drives the real stage on the
        real fixture with an extraction that returns NO facts, so the only
        thing that can persist is the deterministic matcher — proving the
        platform is searchable end-to-end regardless of model output."""
        from pathlib import Path

        from app.pipeline import stages

        case = json.loads((Path(__file__).parent / "golden" / "edu-05.json")
                          .read_text(encoding="utf-8"))
        reply = json.dumps({
            "summary": "networking", "categories": ["Educational"],
            "primary_schema": "education", "key_takeaways": [],
            "action_items": [], "entities": [], "facts": [],
        })
        monkeypatch.setattr(stages.providers, "get_llm", lambda: FakeLLM(reply))
        rid = mk_reel(sample_user, shortcode="EDU05stage1")
        with get_db() as db:
            db.execute("UPDATE reels SET caption=? WHERE id=?",
                       (case["caption"], rid))
            for segment in case["transcript"]:
                db.execute(
                    "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                    " VALUES (?,?,?,?)",
                    (rid, segment["t"], segment["t"] + 1, segment["text"]))
            for overlay in case["ocr"]:
                db.execute("INSERT INTO ocr_results(reel_id,t_s,text,conf)"
                           " VALUES (?,?,?,?)",
                           (rid, overlay["t"], overlay["text"], 1.0))
        stages.stage_classify_extract(rid, {})
        with get_db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT field, value, evidence_source, evidence_quote,"
                " evidence_t_s, confidence FROM facts WHERE reel_id=?"
                " ORDER BY value", (rid,))]
        linkedin = [r for r in rows if r["value"] == "linkedin"]
        assert len(linkedin) == 1, (
            f"expected exactly one recovered linkedin fact, got {rows}")
        fact = linkedin[0]
        assert fact["field"] == "technologies"
        assert fact["evidence_source"] == "transcript"
        # the quote must be the real span that mentions it, and timed so the
        # UI can seek there — not the bare token with a null timestamp
        assert "linkedin" in fact["evidence_quote"].lower()
        assert len(fact["evidence_quote"]) > len("linkedin")
        assert fact["evidence_t_s"] is not None
        assert fact["confidence"] == 0.95

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
