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
