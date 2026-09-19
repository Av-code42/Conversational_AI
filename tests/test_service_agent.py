import pytest

from ivr.agents.models import AgentStatus
from ivr.agents.rate_limit import ServiceRequestLimiter
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


def make_agent(banking=None, classifier=None, limiter=None):
    return ServiceAgent(
        banking or DummyBankingClient(),
        classifier or FakeClassifier(),
        limiter or ServiceRequestLimiter(),
    )


# -- cheque_book_request / kyc_update: confirm-before-commit -------------


def test_cheque_book_request_asks_for_confirmation_first():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking)
    state = agent.start("cheque_book_request", "CIF1001")
    assert state.status == AgentStatus.NEEDS_INPUT
    assert banking.list_service_requests("CIF1001") == []  # not submitted yet


def test_cheque_book_request_confirmed_submits_a_ticket():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking)
    agent.start("cheque_book_request", "CIF1001")
    state = agent.submit_input("yes")
    assert state.status == AgentStatus.COMPLETED
    assert banking.list_service_requests("CIF1001")[0].kind == "cheque_book_request"


def test_cheque_book_request_declined_submits_nothing():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking)
    agent.start("cheque_book_request", "CIF1001")
    state = agent.submit_input("no")
    assert state.status == AgentStatus.COMPLETED
    assert "haven't submitted" in state.prompt.lower()
    assert banking.list_service_requests("CIF1001") == []


def test_kyc_update_confirmed_submits_a_ticket():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking)
    agent.start("kyc_update", "CIF1001")
    state = agent.submit_input("yes")
    assert state.status == AgentStatus.COMPLETED
    assert banking.list_service_requests("CIF1001")[0].kind == "kyc_update"


def test_ambiguous_confirmation_reprompts_then_escalates():
    agent = make_agent()
    agent.start("cheque_book_request", "CIF1001")
    state = None
    for _ in range(3):
        state = agent.submit_input("maybe possibly")
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "confirmation_not_understood"


def test_unowned_intent_raises():
    agent = make_agent()
    with pytest.raises(ValueError):
        agent.start("balance_enquiry", "CIF1001")


# -- change_of_address: collect, then confirm -----------------------------


def test_change_of_address_asks_for_address_then_confirms_then_completes():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking, classifier=FakeClassifier(result=None))
    state = agent.start("change_of_address", "CIF1001")
    assert state.status == AgentStatus.NEEDS_INPUT

    state = agent.submit_input("221B Baker Street, Mumbai")
    assert state.status == AgentStatus.NEEDS_INPUT
    assert "221B Baker Street, Mumbai" in state.prompt
    assert banking.list_service_requests("CIF1001") == []  # not committed yet

    final = agent.submit_input("yes")
    assert final.status == AgentStatus.COMPLETED
    ticket = banking.list_service_requests("CIF1001")[0]
    assert ticket.kind == "change_of_address"
    assert ticket.details["new_address"] == "221B Baker Street, Mumbai"


def test_declining_address_confirmation_lets_caller_restate():
    banking = DummyBankingClient()
    agent = make_agent(banking=banking, classifier=FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    agent.submit_input("221B Baker Street, Mumbai")

    state = agent.submit_input("no")
    assert state.status == AgentStatus.NEEDS_INPUT
    assert "correct address" in state.prompt.lower()

    final = agent.submit_input("42 Park Avenue, Delhi")
    assert final.status == AgentStatus.NEEDS_INPUT  # asks to confirm the NEW address
    confirmed = agent.submit_input("yes")
    assert confirmed.status == AgentStatus.COMPLETED
    ticket = banking.list_service_requests("CIF1001")[0]
    assert ticket.details["new_address"] == "42 Park Avenue, Delhi"


def test_too_short_input_is_rejected_as_implausible_address():
    agent = make_agent(classifier=FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("ok")
    assert state.status == AgentStatus.NEEDS_INPUT
    assert "doesn't sound like" in state.prompt


def test_repeated_implausible_address_escalates_instead_of_looping_forever():
    # Regression: this used to have no cap at all.
    agent = make_agent(classifier=FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    state = None
    for _ in range(3):
        state = agent.submit_input("ok")
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "address_format_not_understood"


def test_topic_switch_during_slot_filling_hands_off():
    agent = make_agent(classifier=FakeClassifier(result="balance_enquiry"))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("actually what's my balance")
    assert state.status == AgentStatus.NEED_HANDOFF
    assert state.new_intent_id == "balance_enquiry"


def test_classifier_returning_the_same_intent_is_not_treated_as_a_switch():
    # If the classifier (imperfectly) re-matches the current intent itself,
    # that's not a topic switch -- must still be treated as address input.
    agent = make_agent(classifier=FakeClassifier(result="change_of_address"))
    agent.start("change_of_address", "CIF1001")
    state = agent.submit_input("221B Baker Street, Mumbai")
    assert state.status == AgentStatus.NEEDS_INPUT  # now asks for confirmation, doesn't commit yet


def test_low_confidence_reprompts_then_escalates():
    agent = make_agent(classifier=FakeClassifier(result=None))
    agent.start("change_of_address", "CIF1001")
    state = None
    for _ in range(3):
        state = agent.submit_input("mumble", confidence=0.2)
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "low_confidence_exceeded"


def test_submit_input_with_nothing_pending_raises():
    agent = make_agent()
    agent.start("kyc_update", "CIF1001")
    agent.submit_input("yes")  # completes it -- nothing pending afterward
    with pytest.raises(RuntimeError):
        agent.submit_input("anything")


# -- rate limiting ----------------------------------------------------


def test_rate_limit_blocks_submission_and_escalates():
    banking = DummyBankingClient()
    limiter = ServiceRequestLimiter(max_per_call=1)
    limiter.record_submission()  # simulate one already used up this call

    agent = make_agent(banking=banking, limiter=limiter)
    agent.start("kyc_update", "CIF1001")
    state = agent.submit_input("yes")
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "service_request_limit_exceeded"
    assert banking.list_service_requests("CIF1001") == []


def test_rate_limit_shared_across_agent_instances_via_the_same_limiter():
    # Mirrors how AgentRegistry hands the same limiter to every ServiceAgent
    # it creates for a given call -- the cap applies across the whole call,
    # not per intent/per agent instance.
    banking = DummyBankingClient()
    limiter = ServiceRequestLimiter(max_per_call=1)

    first_agent = make_agent(banking=banking, limiter=limiter)
    first_agent.start("kyc_update", "CIF1001")
    first_agent.submit_input("yes")

    second_agent = make_agent(banking=banking, limiter=limiter)
    second_agent.start("cheque_book_request", "CIF1001")
    state = second_agent.submit_input("yes")
    assert state.status == AgentStatus.ESCALATE
    assert state.escalation_reason == "service_request_limit_exceeded"
