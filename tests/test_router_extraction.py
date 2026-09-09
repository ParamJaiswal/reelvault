"""Router/extraction regression tests (agent1-router).

LLM-free: the extraction prompt is captured through a fake provider so
prompt rules and the few-shot example can be asserted without a GPU."""
import json

import pytest


# ------------------------------------------------------- B3: schema aliases
def test_schema_alias_maps_recipe_and_fitness_to_generic():
    from app.ai.router import SCHEMA_ALIAS

    assert SCHEMA_ALIAS.get("recipe") == "generic"
    assert SCHEMA_ALIAS.get("fitness") == "generic"
    # pre-existing entry, must not have been duplicated/removed
    assert SCHEMA_ALIAS.get("finance") == "generic"


def test_classification_with_recipe_schema_is_valid_now():
    """Before B3 a 'recipe' primary_schema failed _valid_classification,
    forcing a retry and then a heuristic re-classification that dropped
    the model's categories."""
    from app.ai.router import _valid_classification

    assert _valid_classification({"categories": ["Other"],
                                  "primary_schema": "recipe"})
    assert _valid_classification({"categories": ["Job"],
                                  "primary_schema": "Fitness"})


# ------------------------------------------ B1/B4: extraction prompt capture
class _FakeLLM:
    """Records messages/kwargs extract() sends, returns a fixed JSON body."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def available(self):
        return True

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.reply


@pytest.fixture()
def captured_llm(monkeypatch):
    import app.ai.router as router_mod

    fake = _FakeLLM('{"summary": "s", "facts": []}')
    monkeypatch.setattr(router_mod.providers, "get_llm", lambda: fake)
    return fake


def _extraction_system_prompt(captured_llm, schema_type="job"):
    from app.ai.router import get_router

    get_router().extract("transcript text", schema_type)
    return captured_llm.calls[0]["messages"][0]["content"]


# --------------------------------------------- B1: compact few-shot example
def test_job_extraction_prompt_has_one_compact_example(captured_llm):
    prompt = _extraction_system_prompt(captured_llm, "job")
    assert prompt.count("ANSWER: {") == 1
    assert "TRANSCRIPT: [00:00] Zylker is hiring data analysts" in prompt
    for field in ("company", "location", "experience_required", "deadline"):
        assert f'"field": "{field}"' in prompt
    # measured 1808 chars when the example landed; bound keeps it compact
    assert len(prompt) < 2500


def test_extraction_example_matches_schema_validators(captured_llm):
    """The few-shot ANSWER must pass the same validators real extraction
    output goes through, and its quotes must be verbatim source text so the
    example itself models the evidence rules."""
    from app.knowledge.schemas import BaseExtraction, JobFacts

    prompt = _extraction_system_prompt(captured_llm, "job")
    obj = json.loads(prompt.split("ANSWER: ", 1)[1])
    parsed = BaseExtraction.model_validate(obj)
    assert len(parsed.facts) == 4
    assert {f.field for f in parsed.facts} <= set(JobFacts.model_fields)
    transcript = prompt.split("TRANSCRIPT: ", 1)[1].split("\n", 1)[0]
    for fact in obj["facts"]:
        assert fact["quote"] in transcript, fact["field"]


# ----------------------------------------- B4: verbatim date words in prompt
def test_job_extraction_prompt_requires_verbatim_dates(captured_llm):
    prompt = _extraction_system_prompt(captured_llm, "job")
    assert "verbatim date words" in prompt
    assert "never a normalized date" in prompt
    obj = json.loads(prompt.split("ANSWER: ", 1)[1])
    deadline = next(f for f in obj["facts"] if f["field"] == "deadline")
    assert deadline["value"] == "September 15"  # verbatim, not 2026-09-15


def test_generic_extraction_prompt_requires_verbatim_dates(captured_llm):
    prompt = _extraction_system_prompt(captured_llm, "generic")
    assert "verbatim date words" in prompt
    assert "never a normalized date" in prompt
    assert len(prompt) < 1200
