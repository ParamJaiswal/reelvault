"""V2-2: RV_LLM_BACKEND provider swap seam tests.

Verifies get_llm() honors llm_backend config and OpenAICompatProvider
contract without hitting a real API.
"""
import pytest
from unittest.mock import patch, MagicMock


def test_get_llm_default_is_llamacpp():
    """Default backend must stay llamacpp for v0.1 compat."""
    from app.ai.providers import get_llm, LlamaCppProvider, _llm
    import app.ai.providers as mod
    mod._llm = None  # reset singleton
    with patch.object(mod.settings, "llm_backend", "llamacpp"):
        llm = get_llm()
        assert isinstance(llm, LlamaCppProvider)
    mod._llm = None


def test_get_llm_openai_compat():
    """openai_compat backend returns OpenAICompatProvider."""
    from app.ai.providers import get_llm, OpenAICompatProvider
    import app.ai.providers as mod
    mod._llm = None
    with patch.object(mod.settings, "llm_backend", "openai_compat"):
        llm = get_llm()
        assert isinstance(llm, OpenAICompatProvider)
    mod._llm = None


def test_get_llm_none_raises():
    """none backend must fail loudly, not silently return nothing."""
    from app.ai.providers import get_llm
    import app.ai.providers as mod
    mod._llm = None
    with patch.object(mod.settings, "llm_backend", "none"):
        with pytest.raises(RuntimeError, match="no LLM available"):
            get_llm()
    mod._llm = None


def test_openai_compat_requires_api_key():
    """OpenAICompatProvider.chat must refuse without RV_LLM_API_KEY."""
    from app.ai.providers import OpenAICompatProvider
    p = OpenAICompatProvider(api_key="")
    with pytest.raises(RuntimeError, match="RV_LLM_API_KEY not set"):
        p.chat([{"role": "user", "content": "hi"}])


def test_openai_compat_available_without_key():
    """available() returns False when no key, doesn't crash."""
    from app.ai.providers import OpenAICompatProvider
    p = OpenAICompatProvider(api_key="")
    assert p.available() is False


def test_openai_compat_chat_records_telemetry(tmp_db):
    """Successful chat call records to ai_runs."""
    from app.ai.providers import OpenAICompatProvider
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    mock_resp.raise_for_status = MagicMock()

    p = OpenAICompatProvider(
        base_url="http://fake:9999/v1", model="test-model", api_key="sk-test")
    with patch("app.ai.providers.httpx.post", return_value=mock_resp):
        result = p.chat([{"role": "user", "content": "test"}])
    assert result == '{"ok": true}'

    import sqlite3
    db = sqlite3.connect(str(tmp_db))
    row = db.execute("SELECT task, backend, model, ok, tokens_in FROM ai_runs").fetchone()
    db.close()
    assert row is not None
    assert row[0] == "llm.chat"
    assert row[1] == "openai_compat"
    assert row[2] == "test-model"
    assert row[3] == 1
    assert row[4] == 10
