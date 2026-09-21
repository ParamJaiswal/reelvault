"""v2 hybrid routing: per-kind LLM backend selection.

Pins the routing table (which content_kind is served by which provider),
the cloud-first/local-fallback wrapper, and the v0.1 guarantee that an
unset RV_LLM_TEXT_BACKEND changes nothing.
"""
import json as _json
from unittest.mock import patch

import pytest


class FakeBackend:
    def __init__(self, name, reply="ok", fail=False, base_url=None):
        self.name = name
        self.reply = reply
        self.fail = fail
        self.base_url = base_url or f"http://{name}/v1"
        self.calls = []

    def available(self):
        return not self.fail

    def chat(self, messages, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("429 rate limit")
        return self.reply


# ------------------------------------------------------------ routing table
def test_text_kind_gets_hybrid_wrapper():
    from app.ai.providers import get_llm_for_kind, TextFallbackProvider
    import app.ai.providers as mod
    local = FakeBackend("llamacpp")
    with patch.object(mod.settings, "llm_text_backend", "openai_compat"), \
            patch.object(mod, "get_llm", lambda: local), \
            patch.object(mod, "_build_backend",
                         lambda name: FakeBackend(name)):
        llm = get_llm_for_kind("paper")
    assert isinstance(llm, TextFallbackProvider)
    assert llm.primary.name == "openai_compat"
    assert llm.fallback is local


@pytest.mark.parametrize("kind", ["video", "linkedin_post", "image_post"])
def test_media_carrying_kinds_stay_on_default_backend(kind):
    """linkedin_post can carry video, so it must not burn cloud quota."""
    from app.ai.providers import get_llm_for_kind
    import app.ai.providers as mod
    local = FakeBackend("llamacpp")
    with patch.object(mod.settings, "llm_text_backend", "openai_compat"), \
            patch.object(mod, "get_llm", lambda: local):
        assert get_llm_for_kind(kind) is local


def test_unset_text_backend_keeps_single_backend():
    """Empty RV_LLM_TEXT_BACKEND == v0.1 behavior for every kind."""
    from app.ai.providers import get_llm_for_kind
    import app.ai.providers as mod
    local = FakeBackend("llamacpp")
    with patch.object(mod.settings, "llm_text_backend", ""), \
            patch.object(mod, "get_llm", lambda: local):
        assert all(get_llm_for_kind(k) is local
                   for k in ("paper", "article", "x_post", "note", "video"))


# ------------------------------------------------------- fallback behaviour
def test_hybrid_falls_back_to_local_when_cloud_fails():
    from app.ai.providers import TextFallbackProvider
    cloud, local = FakeBackend("cloud", fail=True), FakeBackend("local")
    hybrid = TextFallbackProvider(cloud, local)
    assert hybrid.chat([{"role": "user", "content": "hi"}], reel_id=7) == "ok"
    assert local.calls and local.calls[0]["reel_id"] == 7
    assert hybrid.available() is True


def test_hybrid_prefers_cloud_and_skips_fallback():
    from app.ai.providers import TextFallbackProvider
    cloud, local = FakeBackend("cloud", reply="from-cloud"), FakeBackend("local")
    assert TextFallbackProvider(cloud, local).chat([{}]) == "from-cloud"
    assert local.calls == []


# ------------------------------------------------------- endpoint separation
def test_cloud_settings_win_for_openai_compat_and_local_untouched():
    """Hybrid runs llama-server and Groq at once, so the cloud provider must
    have its own endpoint settings instead of hijacking RV_LLM_SERVER_URL."""
    import app.ai.providers as mod
    with patch.object(mod.settings, "llm_cloud_server_url",
                      "https://api.groq.com/openai/v1"), \
            patch.object(mod.settings, "llm_cloud_model_name",
                         "openai/gpt-oss-120b"), \
            patch.object(mod.settings, "llm_server_url",
                         "http://127.0.0.1:8091/v1"), \
            patch.object(mod.settings, "llm_model_name", "qwen2.5-3b-instruct"):
        cloud = mod.OpenAICompatProvider(api_key="k")
        local = mod.LlamaCppProvider()
    assert cloud.base_url == "https://api.groq.com/openai/v1"
    assert cloud.model == "openai/gpt-oss-120b"
    assert local.base_url == "http://127.0.0.1:8091/v1"
    assert local.model == "qwen2.5-3b-instruct"


def test_cloud_settings_unset_falls_back_to_shared():
    """Single-backend cloud use (RV_LLM_BACKEND=openai_compat) keeps working
    with only RV_LLM_SERVER_URL configured."""
    import app.ai.providers as mod
    with patch.object(mod.settings, "llm_cloud_server_url", ""), \
            patch.object(mod.settings, "llm_cloud_model_name", ""), \
            patch.object(mod.settings, "llm_server_url",
                         "https://api.groq.com/openai/v1"), \
            patch.object(mod.settings, "llm_model_name", "openai/gpt-oss-20b"):
        cloud = mod.OpenAICompatProvider(api_key="k")
    assert cloud.base_url == "https://api.groq.com/openai/v1"
    assert cloud.model == "openai/gpt-oss-20b"


# ------------------------------------------------------------ stage wiring
def _mk_reel(user_id, kind,
             body="Contact aidan@cs.toronto.edu for the appendix"):
    from app.db.schema import get_db
    with get_db() as db:
        rid = db.execute(
            "INSERT INTO reels(user_id, source_kind, content_kind, title,"
            " status, current_stage) VALUES(?,'url',?,?,'processing','ocr')",
            (user_id, kind, kind.title())).lastrowid
        db.execute("INSERT INTO documents(reel_id, body_text) VALUES(?,?)",
                   (rid, body))
    return rid


def _reply():
    return _json.dumps({"summary": "a paper about attention",
                        "key_takeaways": [], "action_items": [],
                        "categories": ["AI/ML"], "primary_schema": "education",
                        "facts": [], "entities": []})


@pytest.mark.parametrize("kind", ["paper", "video"])
def test_stage_asks_for_its_own_content_kind(tmp_db, sample_user, kind):
    """Regression: the stage must route by the reel's content_kind — routing
    on a hardcoded value would silently keep every text reel local."""
    from app.pipeline import stages

    seen = []
    fake = FakeBackend("fake", reply=_reply())

    def spy(k):
        seen.append(k)
        return fake

    with patch.object(stages.providers, "get_llm_for_kind", spy):
        stages.stage_classify_extract(_mk_reel(sample_user, kind), {})
    assert seen and set(seen) == {kind}


def test_stage_serves_text_reel_from_cloud_when_configured(tmp_db, sample_user):
    """End-to-end of the seam: the configured text backend answers and the
    local model is never consulted, and the event records who served."""
    from app.pipeline import stages
    import app.ai.providers as mod

    cloud = FakeBackend("cloud", reply=_reply())
    local = FakeBackend("llamacpp", reply=_reply())
    rid = _mk_reel(sample_user, "article")
    with patch.object(mod.settings, "llm_text_backend", "cloud"), \
            patch.object(mod.settings, "slm_enabled", False), \
            patch.object(mod, "get_llm", lambda: local), \
            patch.object(mod, "_build_backend", lambda name: cloud):
        stages.stage_classify_extract(rid, {})

    assert cloud.calls, "text reel was not served by the cloud backend"
    assert not local.calls, "local backend should not be consulted"
    from app.db.schema import get_db
    with get_db() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT data_json FROM processing_events WHERE reel_id=?"
            " AND stage='classify_extract'", (rid,))]
    assert any('"served_by": "hybrid(cloud->llamacpp)"' in (r["data_json"] or "")
               for r in rows), rows


def test_stage_survives_cloud_failure_by_serving_from_local(tmp_db, sample_user):
    """The point of the wrapper: a 429 must degrade the note to local, not
    fail the job."""
    from app.pipeline import stages
    import app.ai.providers as mod

    cloud = FakeBackend("cloud", fail=True, reply=_reply())
    local = FakeBackend("llamacpp", reply=_reply())
    rid = _mk_reel(sample_user, "paper")
    with patch.object(mod.settings, "llm_text_backend", "cloud"), \
            patch.object(mod.settings, "slm_enabled", False), \
            patch.object(mod, "get_llm", lambda: local), \
            patch.object(mod, "_build_backend", lambda name: cloud):
        stages.stage_classify_extract(rid, {})

    assert cloud.calls and local.calls, "expected cloud attempt then local serve"
    from app.db.schema import get_db
    with get_db() as db:
        status = db.execute("SELECT status FROM reels WHERE id=?",
                            (rid,)).fetchone()[0]
    assert status == "processing"  # stage completed; worker decides next stage


def test_fallback_warning_never_carries_the_key(caplog):
    """The fallback path logs str(exception) — httpx embeds the Authorization
    header in it, so the log line must be redacted."""
    import logging
    from app.ai.providers import TextFallbackProvider

    class KeyLeakingBackend:
        name = "leaky"
        base_url = "https://api.groq.com/openai/v1"

        def available(self):
            return True

        def chat(self, messages, **kw):
            raise RuntimeError("Illegal header value b'Bearer SECRETKEY123'")

    local = FakeBackend("local")
    hybrid = TextFallbackProvider(KeyLeakingBackend(), local)
    with caplog.at_level(logging.WARNING, logger="rv.ai"):
        hybrid.chat([{"role": "user", "content": "hi"}])
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRETKEY123" not in logged
    assert "REDACTED" in logged


def test_text_backend_without_key_fails_loudly(monkeypatch):
    """Hybrid configured but no credentials = a config error, not a silent
    per-call downgrade to the local model."""
    import app.ai.providers as mod
    monkeypatch.setattr(mod.settings, "llm_api_key", "")
    monkeypatch.delenv("RV_LLM_API_KEY", raising=False)
    with patch.object(mod.settings, "llm_text_backend", "openai_compat"), \
            patch.object(mod.settings, "llm_backend", "llamacpp"):
        with pytest.raises(RuntimeError, match="needs RV_LLM_API_KEY"):
            mod.get_llm_for_kind("paper")


def test_changing_text_backend_rebuilds_the_wrapper():
    """The cached wrapper is keyed on the backend name, so a config change
    cannot leave a stale provider serving every text reel."""
    from app.ai.providers import get_llm_for_kind
    import app.ai.providers as mod

    a, b = FakeBackend("a"), FakeBackend("b")
    local = FakeBackend("llamacpp")
    builders = {"a": a, "b": b}
    with patch.object(mod, "get_llm", lambda: local), \
            patch.object(mod, "_build_backend", lambda name: builders[name]):
        with patch.object(mod.settings, "llm_text_backend", "a"):
            assert get_llm_for_kind("paper").primary is a
        with patch.object(mod.settings, "llm_text_backend", "b"):
            assert get_llm_for_kind("paper").primary is b
