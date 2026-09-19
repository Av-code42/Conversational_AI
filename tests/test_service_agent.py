import pytest

from ivr.agents.models import AgentStatus
from ivr.agents.service_agent import ServiceAgent
from ivr.banking.banking_dummy import DummyBankingClient


class FakeClassifier:
    """Deterministic stand-in for topic-switch detection during
    slot-filling -- returns whatever's configured regardless of input, so
    tests don't depend on real keyword/LLM classification quirks."""

    def __init__(self, result=None):
        self.result = result

    def classify(self, text):
        return self.result


def test_cheque_book_request_completes_immediately_and_issues_a_ticket():
    banking = DummyBankingClient()
    agent = ServiceAgent(banking, FakeClassifier())
    state = agent.start("cheque_book_request", "CIF1001")
    assert state.status == AgentStatus.COMPLETED
    assert banking.list_service_requests("CIF1001")[0].kind == "cheque_book_request"


def test_kyc_update_completes_immediately_and_issues_a_ticket():
    banking = DummyBankingClient()
    agent = ServiceAgent(banking, FakeClassifier())
    state = agent.start("kyc_update", "CIF1001")
    assert state.status == AgentStatus.COMPLETED
    assert banking.list_service_requests("CIF1001")[0].kind == "kyc_update"


def test_unowned_intent_raises():
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier())
    with pytest.raises(ValueError):
        agent.start("balance_enquiry", "CIF1001")


def test_change_of_address_asks_for_address_then_completes():
    banking = DummyBankingClient()
    agent = ServiceAgent(banking, FakeClassifier(result=None))  # no topic switch
    state = agent.start("change_of_address", "CIF1001")
    assert state.status == AgentStatus.NEEDS_INPUT

    final = agent.submit_input("221B Baker Street, Mumbai")
    assert final.status == AgentStatus.COMPLETED
    ticket = banking.list_service_requests("CIF1001")[0]
    assert ticket.kind == "change_of_address"
    assert ticket.details["new_address"] == "221B Baker Street, Mumbai"


def test_too_short_input_is_rejected_as_implausible_address():
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("ok")
    assert state.status == AgentStatus.NEEDS_INPUT
    assert "doesn't sound like" in state.prompt


def test_topic_switch_during_slot_filling_hands_off():
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier(result="balance_enquiry"))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("actually what's my balance")
    assert state.status == AgentStatus.NEED_HANDOFF
    assert state.new_intent_id == "balance_enquiry"


def test_classifier_returning_the_same_intent_is_not_treated_as_a_switch():
    # If the classifier (imperfectly) re-matches the current intent itself,
    # that's not a topic switch -- must still be treated as address input.
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier(result="change_of_address"))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("221B Baker Street, Mumbai")
    assert state.status == AgentStatus.COMPLETED


def test_low_confidence_reprompts_then_escalates():
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    state = None
    for _ in range(3):
        state = agent.submit_input("mumble", confidence=0.2)
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "low_confidence_exceeded"


def test_submit_input_with_nothing_pending_raises():
    agent = ServiceAgent(DummyBankingClient(), FakeClassifier())
    agent.start("kyc_update", "CIF1001")  # zero-slot, nothing pending afterward
    with pytest.raises(RuntimeError):
        agent.submit_input("anything")
