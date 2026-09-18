import pytest

from ivr.auth.models import Tier
from ivr.auth.tier_config import IntentTierMap, UnknownIntentError


def test_loads_real_config_file():
    tier_map = IntentTierMap.load()
    assert tier_map.tier_for("balance_enquiry") == Tier.TIER_1
    assert tier_map.agent_for("balance_enquiry") == "accounts_agent"
    assert tier_map.tier_for("cheque_book_request") == Tier.TIER_1
    assert tier_map.agent_for("kyc_update") == "service_agent"


def test_contains():
    tier_map = IntentTierMap.load()
    assert "balance_enquiry" in tier_map
    assert "not_a_real_intent" not in tier_map


def test_unknown_intent_raises():
    tier_map = IntentTierMap.load()
    with pytest.raises(UnknownIntentError):
        tier_map.tier_for("not_a_real_intent")
    with pytest.raises(UnknownIntentError):
        tier_map.agent_for("not_a_real_intent")
