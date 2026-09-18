import pytest

from ivr.auth.cbs_dummy import DummyCBSClient
from ivr.auth.flow import AuthFlow
from ivr.auth.models import Tier
from ivr.auth.otp_dummy import DummyOTPGateway
from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator.flow import CoordinatorFlow
from ivr.coordinator.intent_classifier import KeywordIntentClassifier

# Synthetic catalog covering all three tiers -- config/intent_tier_map.yaml
# only has Tier 1 intents defined so far (see docs/design/authentication-flow.md
# SS4), so Tier 0/2 behavior is tested against this fixture map instead of
# the real file. test_tier_config.py separately verifies the real file loads
# correctly.
TEST_INTENTS = {
    "balance_enquiry": ("accounts_agent", Tier.TIER_1),
    "statement_request": ("transaction_agent", Tier.TIER_1),
    "hotlist_card": ("cards_agent", Tier.TIER_2),
    "branch_hours": ("faq_agent", Tier.TIER_0),
}


class FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def tier_map() -> IntentTierMap:
    return IntentTierMap(dict(TEST_INTENTS))


@pytest.fixture
def cbs() -> DummyCBSClient:
    return DummyCBSClient()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def otp_gateway(clock) -> DummyOTPGateway:
    return DummyOTPGateway(clock=clock)


@pytest.fixture
def flow(cbs, otp_gateway, tier_map) -> AuthFlow:
    return AuthFlow(cbs=cbs, otp_gateway=otp_gateway, tier_map=tier_map)


@pytest.fixture
def intent_classifier(tier_map) -> KeywordIntentClassifier:
    return KeywordIntentClassifier(tier_map)


@pytest.fixture
def coordinator(cbs, otp_gateway, tier_map, intent_classifier) -> CoordinatorFlow:
    return CoordinatorFlow(cbs=cbs, otp_gateway=otp_gateway, tier_map=tier_map, intent_classifier=intent_classifier)
