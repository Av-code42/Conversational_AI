from ivr.coordinator.build_intent_classifier import build_intent_classifier
from ivr.coordinator.fallback_intent_classifier import FallbackIntentClassifier
from ivr.coordinator.intent_classifier import KeywordIntentClassifier


def test_keyword_only_when_no_api_key(tier_map, monkeypatch):
    monkeypatch.setattr("ivr.coordinator.llm_config.GROQ_API_KEY", "")
    classifier = build_intent_classifier(tier_map)
    assert isinstance(classifier, KeywordIntentClassifier)


def test_fallback_chain_when_api_key_set(tier_map, monkeypatch):
    monkeypatch.setattr("ivr.coordinator.llm_config.GROQ_API_KEY", "fake-key-for-test")
    classifier = build_intent_classifier(tier_map)
    assert isinstance(classifier, FallbackIntentClassifier)
