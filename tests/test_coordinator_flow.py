"""Exercises CoordinatorFlow per docs/design/agent-architecture.md SS3:
greeting, intent capture, routing + the auth gate (delegating to the
already-tested AuthFlow), handoff, and the "anything else?" loop via
mark_task_completed() (there's no real domain agent yet to call that for
us).
"""

import pytest

from ivr.coordinator.flow import CoordinatorEscalationReason, CoordinatorStatus
from ivr.auth.models import EscalationReason, Tier

ASHA_MOBILE = "9876500001"  # CIF1001, mpin 4321 -- Tier 1 in the real catalog
VIKRAM_MOBILE = "9876500002"  # CIF1002, mpin 1111
MEERA_MOBILE = "9876500003"  # CIF1003, watchlist flag


def test_start_returns_greeting(coordinator):
    state = coordinator.start(ani=ASHA_MOBILE)
    assert state.status == CoordinatorStatus.NEEDS_INTENT
    assert "ABC Retail Bank" in state.prompt


def test_tier0_intent_skips_auth_entirely(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    state = coordinator.submit_utterance("what are the branch hours")
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.intent_id == "branch_hours"
    assert state.tier_achieved == Tier.TIER_0


def test_tier1_happy_path_ends_in_handoff(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    state = coordinator.submit_utterance("what's my balance")
    assert state.status == CoordinatorStatus.NEEDS_MPIN

    state = coordinator.submit_mpin("4321")
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.intent_id == "balance_enquiry"
    assert state.agent_id == "accounts_agent"
    assert state.tier_achieved == Tier.TIER_1


def test_tier2_happy_path_needs_mpin_then_otp(coordinator, otp_gateway):
    coordinator.start(ani=VIKRAM_MOBILE)
    state = coordinator.submit_utterance("hotlist my card")
    assert state.status == CoordinatorStatus.NEEDS_MPIN

    state = coordinator.submit_mpin("1111")
    assert state.status == CoordinatorStatus.NEEDS_OTP

    code = otp_gateway.last_code_sent_to(VIKRAM_MOBILE)
    state = coordinator.submit_otp(code)
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.tier_achieved == Tier.TIER_2


def test_explicit_agent_request_escalates_at_intent_capture(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    state = coordinator.submit_utterance("I want to speak to an agent")
    assert state.status == CoordinatorStatus.ESCALATED
    assert state.escalation_reason == CoordinatorEscalationReason.EXPLICIT_REQUEST


def test_repeated_unclassifiable_utterances_escalate(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    state = coordinator.submit_utterance("gibberish nonsense xyz")
    assert state.status == CoordinatorStatus.NEEDS_INTENT
    state = coordinator.submit_utterance("more gibberish qwerty")
    assert state.status == CoordinatorStatus.ESCALATED
    assert state.escalation_reason == CoordinatorEscalationReason.INTENT_NOT_UNDERSTOOD


def test_repeated_low_confidence_utterances_escalate(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    state = None
    for _ in range(3):
        state = coordinator.submit_utterance("what's my balance", confidence=0.2)
    assert state.status == CoordinatorStatus.ESCALATED
    assert state.escalation_reason == CoordinatorEscalationReason.INTENT_NOT_UNDERSTOOD


def test_repeat_command_reissues_last_prompt(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    coordinator.submit_utterance("gibberish nonsense xyz")  # get a non-greeting prompt on record
    state = coordinator.submit_utterance("repeat")
    assert state.status == CoordinatorStatus.NEEDS_INTENT
    assert "didn't quite catch" in state.prompt.lower()


def test_anything_else_loop_skips_reauth_at_same_tier(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    coordinator.submit_utterance("what's my balance")
    coordinator.submit_mpin("4321")

    state = coordinator.mark_task_completed()
    assert state.status == CoordinatorStatus.NEEDS_INTENT
    assert "anything else" in state.prompt.lower()

    # A different Tier-1 intent in the same call -- already authenticated, no MPIN prompt again.
    state = coordinator.submit_utterance("can I get a statement")
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.intent_id == "statement_request"
    assert state.agent_id == "transaction_agent"


def test_step_up_reauthenticates_mpin(coordinator, otp_gateway):
    # Known simplification (see coordinator/flow.py module docstring): this
    # re-runs full auth rather than only the delta, so MPIN gets asked again
    # even though it was already given this call. Pinning that behavior here
    # so it's a deliberate change, not a silent regression, whenever the
    # delta-only step-up gets built.
    coordinator.start(ani=VIKRAM_MOBILE)
    coordinator.submit_utterance("what's my balance")  # Tier 1
    state = coordinator.submit_mpin("1111")
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.tier_achieved == Tier.TIER_1

    coordinator.mark_task_completed()
    state = coordinator.submit_utterance("hotlist my card")  # Tier 2, higher than achieved
    assert state.status == CoordinatorStatus.NEEDS_MPIN  # asked again -- known simplification

    state = coordinator.submit_mpin("1111")
    assert state.status == CoordinatorStatus.NEEDS_OTP
    code = otp_gateway.last_code_sent_to(VIKRAM_MOBILE)
    state = coordinator.submit_otp(code)
    assert state.status == CoordinatorStatus.READY_FOR_HANDOFF
    assert state.tier_achieved == Tier.TIER_2


def test_start_over_clears_tier_achieved(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    coordinator.submit_utterance("what's my balance")
    coordinator.submit_mpin("4321")
    coordinator.mark_task_completed()

    state = coordinator.submit_utterance("start over")
    assert state.status == CoordinatorStatus.NEEDS_INTENT
    assert "ABC Retail Bank" in state.prompt

    state = coordinator.submit_utterance("what's my balance")
    assert state.status == CoordinatorStatus.NEEDS_MPIN  # had to re-auth after start-over


def test_auth_escalation_propagates_with_reason(coordinator):
    coordinator.start(ani=MEERA_MOBILE)  # watchlist-flagged account
    state = coordinator.submit_utterance("what's my balance")
    assert state.status == CoordinatorStatus.ESCALATED
    assert state.escalation_reason == CoordinatorEscalationReason.AUTH_ESCALATED
    assert state.auth_escalation_reason == EscalationReason.ACCOUNT_FLAGGED
    # anti-enumeration still holds at the coordinator level
    assert "watchlist" not in (state.prompt or "").lower()


def test_submit_mpin_without_active_auth_raises(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    with pytest.raises(RuntimeError):
        coordinator.submit_mpin("4321")


def test_saying_no_after_anything_else_ends_the_call(coordinator):
    coordinator.start(ani=ASHA_MOBILE)
    coordinator.submit_utterance("what's my balance")
    coordinator.submit_mpin("4321")
    coordinator.mark_task_completed()  # prompts "anything else?"

    state = coordinator.submit_utterance("no thanks")
    assert state.status == CoordinatorStatus.CALL_ENDED


def test_saying_no_on_the_very_first_turn_does_not_end_the_call(coordinator):
    # "no idea what to do" legitimately contains the word "no" -- must not
    # be treated as "caller is done" outside the "anything else?" context.
    coordinator.start(ani=ASHA_MOBILE)
    state = coordinator.submit_utterance("no idea what to do")
    assert state.status == CoordinatorStatus.NEEDS_INTENT
