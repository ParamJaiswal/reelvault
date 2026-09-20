"""Phase 8A.2: the reel summary was the one model claim that reached the DB
with no evidence check, and migration 2 indexes it into reels_fts — so an
ungrounded narrative was both the top UI field and a search surface.

`grounding_ratio` measures what share of a text's claim terms occur anywhere
in the source spans. It is a lexical grounding signal, not a truth proof.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.main import app, require_auth
from app.db.schema import get_db
from app.knowledge.evidence import (SUMMARY_GROUNDING_MIN, SourceSpan,
                                    grounding_ratio)

SRC = [
    SourceSpan(text="Zylker Analytics is hiring data analyst interns in"
                    " Bangalore", t_s=0.0, source="transcript"),
    SourceSpan(text="APPLY BEFORE SEPTEMBER 15", t_s=3.0, source="ocr"),
    SourceSpan(text="Freshers encouraged to apply", t_s=None, source="caption"),
]

GROUNDED = ("Zylker Analytics hiring data analyst interns Bangalore"
            " before September 15")
INVENTED = ("Zylker Analytics pays rs 12 lakh per month with remote"
            " flexibility for senior consultants")


class TestGroundingRatio:
    def test_fully_grounded_summary_scores_one(self):
        assert grounding_ratio(GROUNDED, SRC) == 1.0

    def test_invented_content_lowers_the_ratio(self):
        ratio = grounding_ratio(INVENTED, SRC)
        assert 0.0 < ratio < SUMMARY_GROUNDING_MIN, (
            f"invented salary/remote/consultant terms should push this below"
            f" the gate; got {ratio}")

    def test_unrelated_summary_scores_zero(self):
        assert grounding_ratio("A complete fabrication about stock markets",
                               SRC) == 0.0

    def test_text_without_claim_terms_is_zero_not_crashing(self):
        assert grounding_ratio("the and of to", SRC) == 0.0
        assert grounding_ratio("", SRC) == 0.0

    def test_number_surface_forms_count_as_grounded(self):
        # "fifteenth" canonicalizes to 15 the same way the claim guard does
        assert grounding_ratio("september fifteenth", SRC) == 1.0

    def test_no_spans_means_nothing_is_grounded(self):
        assert grounding_ratio("Zylker Analytics hiring", []) == 0.0


@pytest.fixture()
def client(tmp_db, sample_user):
    app.dependency_overrides[require_auth] = lambda: sample_user
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _reel_with_source(sample_user):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO reels(user_id, source_kind, source_url, shortcode,"
            " caption) VALUES (?,?,?,?,?)",
            (sample_user, "file", "https://www.instagram.com/reel/SGTst001/",
             "SGTst001", "Freshers encouraged to apply"))
        rid = cur.lastrowid
        db.execute(
            "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
            " VALUES (?,0,4,'Zylker Analytics is hiring data analyst interns"
            " in Bangalore')", (rid,))
        db.execute("INSERT INTO ocr_results(reel_id,t_s,text,conf)"
                   " VALUES (?,3,'APPLY BEFORE SEPTEMBER 15',1.0)", (rid,))
        return rid


def _run_extract(rid, summary, monkeypatch):
    from app.pipeline import stages

    reply = json.dumps({
        "summary": summary, "categories": ["Job"], "primary_schema": "job",
        "key_takeaways": [], "action_items": [], "entities": [], "facts": [],
    })

    class Fake:
        name = "fake"

        def available(self):
            return True

        def chat(self, messages, **kw):
            return reply

    monkeypatch.setattr(stages.providers, "get_llm", lambda: Fake())
    stages.stage_classify_extract(rid, {})


def _grounding(rid):
    with get_db() as db:
        return db.execute("SELECT summary_grounding FROM reels WHERE id=?",
                          (rid,)).fetchone()["summary_grounding"]


class TestExtractionPersistsGrounding:
    def test_grounded_summary_stores_full_ratio(self, tmp_db, sample_user,
                                                monkeypatch):
        rid = _reel_with_source(sample_user)
        _run_extract(rid, GROUNDED, monkeypatch)
        assert _grounding(rid) == 1.0

    def test_invented_summary_stores_low_ratio(self, tmp_db, sample_user,
                                               monkeypatch):
        rid = _reel_with_source(sample_user)
        _run_extract(rid, INVENTED, monkeypatch)
        assert _grounding(rid) < SUMMARY_GROUNDING_MIN

    def test_empty_summary_stores_null_not_zero(self, tmp_db, sample_user,
                                                monkeypatch):
        """NULL means "never measured"; 0.0 would mean "measured, ungrounded"."""
        rid = _reel_with_source(sample_user)
        _run_extract(rid, "", monkeypatch)
        assert _grounding(rid) is None


class TestApiExposesVerdict:
    def test_detail_reports_verified_for_grounded(self, client, tmp_db,
                                                  sample_user, monkeypatch):
        rid = _reel_with_source(sample_user)
        _run_extract(rid, GROUNDED, monkeypatch)
        body = client.get(f"/api/reels/{rid}").json()
        assert body["summary_grounding"] == 1.0
        assert body["summary_verified"] is True

    def test_detail_reports_unverified_for_invented(self, client, tmp_db,
                                                    sample_user, monkeypatch):
        rid = _reel_with_source(sample_user)
        _run_extract(rid, INVENTED, monkeypatch)
        body = client.get(f"/api/reels/{rid}").json()
        assert body["summary_verified"] is False

    def test_never_measured_row_is_reported_unverified(self, client, tmp_db,
                                                       sample_user):
        """Pre-v5 rows carry NULL. Reporting them as verified would be a lie
        about work that was never done."""
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO reels(user_id, source_kind, summary) VALUES"
                " (?,?,?)", (sample_user, "file", "an old unmeasured summary"))
            rid = cur.lastrowid
        body = client.get(f"/api/reels/{rid}").json()
        assert body["summary_grounding"] is None
        assert body["summary_verified"] is False

    def test_manual_correction_is_remeasured_against_source(self, client, tmp_db,
                                                            sample_user):
        rid = _reel_with_source(sample_user)
        with get_db() as db:
            db.execute("UPDATE reels SET summary=?, summary_grounding=0.1"
                       " WHERE id=?", ("stale wrong summary", rid))
        res = client.patch(f"/api/reels/{rid}", json={
            "summary": "Zylker Analytics is hiring data analyst interns"})
        assert res.status_code == 200
        assert _grounding(rid) == 1.0, (
            "a user edit must be re-measured, not trusted blindly")

    def test_correction_to_ungrounded_text_keeps_the_flag(self, client, tmp_db,
                                                          sample_user):
        rid = _reel_with_source(sample_user)
        res = client.patch(f"/api/reels/{rid}", json={
            "summary": "this reel is about cryptocurrency trading signals"})
        assert res.status_code == 200
        assert client.get(f"/api/reels/{rid}").json()["summary_verified"] is False

    def test_clearing_summary_resets_grounding_to_null(self, client, tmp_db,
                                                       sample_user):
        rid = _reel_with_source(sample_user)
        client.patch(f"/api/reels/{rid}", json={"summary": "   "})
        assert _grounding(rid) is None

    def test_list_endpoint_carries_the_verdict_too(self, client, tmp_db,
                                                   sample_user, monkeypatch):
        rid = _reel_with_source(sample_user)
        _run_extract(rid, GROUNDED, monkeypatch)
        cards = client.get("/api/reels").json()["reels"]
        assert next(c for c in cards if c["id"] == rid)["summary_verified"] is True

    def test_foreign_reel_correction_is_rejected(self, client, tmp_db):
        """The grounding path adds a pre-UPDATE read; it must stay scoped to
        the caller's own rows."""
        with get_db() as db:
            db.execute("INSERT INTO users(id, username, api_key_hash)"
                       " VALUES (99,'other','')")
            cur = db.execute(
                "INSERT INTO reels(user_id, source_kind, summary)"
                " VALUES (?,?,?)", (99, "file", "someone else's reel"))
            rid = cur.lastrowid
        assert client.patch(f"/api/reels/{rid}",
                            json={"summary": "hijacked"}).status_code == 404
        with get_db() as db:
            assert db.execute("SELECT summary FROM reels WHERE id=?",
                              (rid,)).fetchone()["summary"] == "someone else's reel"


class TestBackfill:
    """scripts/backfill_summary_grounding.py exists because v5 leaves every
    pre-existing row NULL, which would flag all 21 real reels as
    "not source-checked" for the wrong reason (never measured, not
    ungrounded). Measuring needs no model call."""

    def _backfill(self):
        from scripts.backfill_summary_grounding import backfill
        return backfill

    def _mk(self, user, summary, *, with_source=True, grounding=None):
        """Seeds the same three spans as SRC, because GROUNDED/INVENTED are
        defined against that pool — "before September 15" only exists in the
        OCR line, so a transcript-only source scores 0.7, not 1.0."""
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO reels(user_id, source_kind, caption, summary,"
                " summary_grounding) VALUES (?,?,?,?,?)",
                (user, "file", "Freshers encouraged to apply", summary,
                 grounding))
            rid = cur.lastrowid
            if with_source:
                db.execute(
                    "INSERT INTO transcript_segments(reel_id,start_s,end_s,text)"
                    " VALUES (?,0,4,'Zylker Analytics is hiring data analyst"
                    " interns in Bangalore')", (rid,))
                db.execute(
                    "INSERT INTO ocr_results(reel_id,t_s,text,conf)"
                    " VALUES (?,3,'APPLY BEFORE SEPTEMBER 15',1.0)", (rid,))
        return rid

    def test_dry_run_measures_without_writing(self, tmp_db, sample_user):
        rid = self._mk(sample_user, GROUNDED)
        report = self._backfill()(apply=False)
        assert [r["reel_id"] for r in report] == [rid]
        assert report[0]["grounding"] == 1.0 and report[0]["verified"] is True
        assert _grounding(rid) is None, "dry run must not persist"

    def test_apply_persists_the_measured_ratio(self, tmp_db, sample_user):
        good = self._mk(sample_user, GROUNDED)
        bad = self._mk(sample_user, INVENTED)
        report = {r["reel_id"]: r for r in self._backfill()(apply=True)}
        assert _grounding(good) == 1.0
        assert report[good]["verified"] is True
        assert report[bad]["verified"] is False
        assert 0.0 < _grounding(bad) < SUMMARY_GROUNDING_MIN

    def test_existing_measurement_is_never_overwritten(self, tmp_db,
                                                       sample_user):
        """A re-extracted or user-corrected row already has a real value;
        the backfill must not silently re-score and change its verdict."""
        rid = self._mk(sample_user, GROUNDED, grounding=0.42)
        assert self._backfill()(apply=True) == []
        assert _grounding(rid) == 0.42

    def test_second_run_is_a_no_op(self, tmp_db, sample_user):
        self._mk(sample_user, GROUNDED)
        assert len(self._backfill()(apply=True)) == 1
        assert self._backfill()(apply=True) == []

    def test_blank_summary_stays_null(self, tmp_db, sample_user):
        """Nothing to measure, so nothing to claim — NULL is the honest
        value and the UI reports it as unverified."""
        rid = self._mk(sample_user, "   ")
        assert self._backfill()(apply=True) == []
        assert _grounding(rid) is None


class TestMigrationFive:
    def test_v4_database_upgrades_and_preserves_existing_rows(self, tmp_path):
        """AGENTS.md sec.7: a schema change must upgrade existing databases.
        Builds a real v4 file, inserts data, then migrates it forward."""
        from app.db.schema import MIGRATIONS, SCHEMA_VERSION, migrate

        assert SCHEMA_VERSION == max(MIGRATIONS) == 7
        db_file = tmp_path / "v4.db"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations"
                     "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL"
                     " DEFAULT (datetime('now')))")
        for v in (1, 2, 3, 4):
            conn.executescript(MIGRATIONS[v])
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)",
                         (v,))
        conn.execute("INSERT INTO users(id, username, api_key_hash)"
                     " VALUES (1,'owner','')")
        conn.execute("INSERT INTO reels(id, user_id, source_kind, summary)"
                     " VALUES (7,1,'file','a summary written before v5')")
        conn.commit()
        conn.close()

        assert migrate(db_file) == 7

        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(reels)")}
        row = conn.execute("SELECT summary, summary_grounding FROM reels"
                           " WHERE id=7").fetchone()
        applied = {r["version"] for r in
                   conn.execute("SELECT version FROM schema_migrations")}
        fts = conn.execute("SELECT COUNT(*) c FROM reels_fts"
                           " WHERE reels_fts MATCH 'summary'").fetchone()["c"]
        conn.close()
        assert "summary_grounding" in cols
        assert row["summary"] == "a summary written before v5"
        assert row["summary_grounding"] is None
        assert applied == {1, 2, 3, 4, 5, 6, 7}
        assert fts >= 1, "reels_fts must survive the upgrade"

    def test_migrate_is_idempotent_on_an_already_v5_database(self, tmp_db):
        from app.db.schema import migrate

        assert migrate() == 7
        with get_db() as db:
            versions = [r["version"] for r in
                        db.execute("SELECT version FROM schema_migrations")]
        assert sorted(versions) == [1, 2, 3, 4, 5, 6, 7]
