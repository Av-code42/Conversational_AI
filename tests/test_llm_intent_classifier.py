import json

import pytest

from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator.llm_intent_classifier import LLMIntentClassifier


class FakeChatClient:
    def __init__(self, response: str):
        self.response = response
        self.last_call = None

    def complete(self, *, system: str, user: str) -> str:
        self.last_call = {"system": system, "user": user}
        return self.response


@pytest.fixture
def real_tier_map():
    return IntentTierMap.load()


def test_classifies_a_valid_response(real_tier_map):
    client = FakeChatClient(json.dumps({"intent_id": "balance_enquiry"}))
    classifier = LLMIntentClassifier(real_tier_map, client)
    assert classifier.classify("what's my balance") == "balance_enquiry"


def test_null_intent_id_returns_none(real_tier_map):
    client = FakeChatClient(json.dumps({"intent_id": None}))
    classifier = LLMIntentClassifier(real_tier_map, client)
    assert classifier.classify("tell me a joke") is None


def test_unknown_intent_id_from_model_returns_none(real_tier_map):
    # Defensive: the model could hallucinate an id not in the real catalog.
    client = FakeChatClient(json.dumps({"intent_id": "made_up_intent"}))
    classifier = LLMIntentClassifier(real_tier_map, client)
    assert classifier.classify("something") is None


def test_malformed_json_raises(real_tier_map):
    client = FakeChatClient("this is not json")
    classifier = LLMIntentClassifier(real_tier_map, client)
    with pytest.raises(json.JSONDecodeError):
        classifier.classify("what's my balance")


def test_empty_text_returns_none_without_calling_the_model(real_tier_map):
    client = FakeChatClient(json.dumps({"intent_id": "balance_enquiry"}))
    classifier = LLMIntentClassifier(real_tier_map, client)
    assert classifier.classify("") is None
    assert client.last_call is None


def test_system_prompt_lists_all_catalog_intents(real_tier_map):
    client = FakeChatClient(json.dumps({"intent_id": "balance_enquiry"}))
    classifier = LLMIntentClassifier(real_tier_map, client)
    classifier.classify("what's my balance")
    for intent_id, _agent, _tier in real_tier_map.all_intents():
        assert intent_id in client.last_call["system"]
