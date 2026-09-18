import pytest

from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator.intent_classifier import KeywordIntentClassifier


@pytest.fixture
def classifier():
    return KeywordIntentClassifier(IntentTierMap.load())


@pytest.mark.parametrize(
    "utterance,expected_intent",
    [
        ("what's my balance", "balance_enquiry"),
        ("I want to check my account balance", "balance_enquiry"),
        ("show me my recent transactions", "transaction_details"),
        ("can I get a statement", "statement_request"),
        ("I need to update my address", "change_of_address"),
        ("I'd like a new chequebook please", "cheque_book_request"),
        ("I need to do my KYC verification", "kyc_update"),
    ],
)
def test_classifies_real_catalog_utterances(classifier, utterance, expected_intent):
    assert classifier.classify(utterance) == expected_intent


def test_unrelated_text_returns_none(classifier):
    assert classifier.classify("what time does the branch open") is None


def test_empty_text_returns_none(classifier):
    assert classifier.classify("") is None


def test_synthetic_tier_map_intents_are_classifiable(tier_map):
    # conftest.py's TEST_INTENTS includes branch_hours (Tier 0) and
    # hotlist_card (Tier 2), neither of which are in the real catalog --
    # the classifier should work against any IntentTierMap it's given.
    classifier = KeywordIntentClassifier(tier_map)
    assert classifier.classify("branch hours") == "branch_hours"
    assert classifier.classify("hotlist my card") == "hotlist_card"
