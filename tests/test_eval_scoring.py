"""Deterministic checks for eval scoring and opt-in provider traces."""
import pytest

from tests.test_ai_eval import _extract_with_trace, _field_matches


@pytest.mark.parametrize("values", [["networking", "unrelated"],
                                    ["unrelated", "networking"]])
def test_duplicate_fields_preserve_any_matching_value(values):
    facts = [{"field": "topic", "value": value} for value in values]
    assert _field_matches({"topic": ["networking"]}, facts) == {"topic": True}


def test_rejected_facts_cannot_earn_recall():
    from app.knowledge.evidence import SourceSpan, find_evidence, HALLUCINATION_THRESHOLD

    facts = [{"field": "topic", "value": "networking",
              "quote": "A completely fictional corporate recruiting announcement"}]
    spans = [SourceSpan("Bake bread until golden brown", None, "caption")]
    kept = [f for f in facts if find_evidence(f["quote"], f["value"], spans).similarity
            >= HALLUCINATION_THRESHOLD]
    assert _field_matches({"topic": ["networking"]}, kept) == {"topic": False}


def test_values_are_not_combined_into_synthetic_matches():
    facts = [{"field": "topic", "value": "neural"},
             {"field": "topic", "value": "networks"}]
    assert not _field_matches({"topic": ["neural networks"]}, facts)["topic"]


def test_related_values_do_not_waive_explicit_gold_requirement():
    facts = [{"field": "technologies", "value": "AI, LLM, CSV"}]
    assert not _field_matches({"technologies": ["linkedin"]}, facts)["technologies"]


def test_field_names_must_match_and_wildcard_is_separate():
    facts = [{"field": "tools_used", "value": "LinkedIn"}]
    assert _field_matches({"technologies": ["linkedin"], "*": ["linkedin"]}, facts) == {
        "technologies": False}


@pytest.mark.parametrize("capture", [False, True])
def test_trace_is_opt_in_and_calls_provider_once(capture):
    class LLM:
        calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            return '{"facts": []}'

    llm = LLM()
    original_chat = llm.chat
    messages = [{"role": "user", "content": "source"}]

    class Router:
        def extract(self, unified, schema):
            import json
            assert (unified, schema) == ("source", "education")
            return json.loads(llm.chat(messages, temperature=0.15))

    result, trace = _extract_with_trace(Router(), llm, "source", "education", capture)
    assert result == {"facts": []}
    assert llm.calls == 1
    assert llm.chat == original_chat
    assert trace == ([{"messages": messages, "options": {"temperature": 0.15},
                       "raw_completion": '{"facts": []}'}] if capture else [])


def test_trace_restores_provider_on_failure():
    class LLM:
        def chat(self, messages, **kwargs):
            raise RuntimeError("provider unavailable")

    llm = LLM()
    original_chat = llm.chat

    class Router:
        def extract(self, unified, schema):
            return llm.chat([])

    with pytest.raises(RuntimeError, match="provider unavailable"):
        _extract_with_trace(Router(), llm, "source", "education", True)
    assert llm.chat == original_chat
