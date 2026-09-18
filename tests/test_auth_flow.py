"""Exercises every path in docs/design/authentication-flow.md SS6, the
retry/lockout policy in SS7, and the escalation triggers in SS8, against the
AuthFlow state machine backed by the dummy CBS/OTP implementations.
"""

import pytest

from ivr.auth.models import AuthStatus, EscalationReason, Tier

ASHA_MOBILE = "9876500001"  # CIF1001, mpin 4321, account last6 789012, card last4 1111
VIKRAM_MOBILE = "9876500002"  # CIF1002, mpin 1111, account last6 789099, card last4 2222
MEERA_MOBILE = "9876500003"  # CIF1003, watchlist flag
ROHIT_NO_MOBILE_ACCOUNT_LAST6 = "789011"  # CIF1004, no registered mobile
ROHIT_CARD_LAST4 = "4444"
PRIYA_MOBILE = "9876500005"  # CIF1005, pre-locked


# -- Tier 0 -----------------------------------------------------------


def test_tier0_requires_no_auth(flow):
    state = flow.start(ani=ASHA_MOBILE, intent_id="branch_hours")
    assert state.status == AuthStatus.NO_AUTH_REQUIRED
    assert state.tier_achieved == Tier.TIER_0


# -- Tier 1, CLI matched ------------------------------------------------


def test_cli_matched_tier1_correct_mpin_first_try(flow):
    state = flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    assert state.status == AuthStatus.NEEDS_MPIN

    state = flow.submit_mpin("4321")
    assert state.status == AuthStatus.AUTHENTICATED
    assert state.tier_achieved == Tier.TIER_1
    assert state.cif == "CIF1001"


def test_cli_matched_tier1_mpin_fallback_to_otp_success(flow, otp_gateway):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    for _ in range(3):
        state = flow.submit_mpin("0000")
    assert state.status == AuthStatus.NEEDS_OTP  # offered fallback, not escalated yet

    code = otp_gateway.last_code_sent_to(ASHA_MOBILE)
    state = flow.submit_otp(code)
    assert state.status == AuthStatus.AUTHENTICATED
    assert state.tier_achieved == Tier.TIER_1


def test_cli_matched_tier1_mpin_and_otp_fallback_both_exhausted_escalates(flow):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    for _ in range(3):
        flow.submit_mpin("0000")
    state = None
    for _ in range(3):
        state = flow.submit_otp("000000")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.OTP_FAILED


# -- Tier 2, CLI matched ------------------------------------------------


def test_cli_matched_tier2_mpin_then_otp_success(flow, otp_gateway):
    state = flow.start(ani=VIKRAM_MOBILE, intent_id="hotlist_card")
    assert state.status == AuthStatus.NEEDS_MPIN

    state = flow.submit_mpin("1111")
    assert state.status == AuthStatus.NEEDS_OTP  # tier 2 always needs both

    code = otp_gateway.last_code_sent_to(VIKRAM_MOBILE)
    state = flow.submit_otp(code)
    assert state.status == AuthStatus.AUTHENTICATED
    assert state.tier_achieved == Tier.TIER_2


def test_cli_matched_tier2_otp_exhausted_escalates(flow):
    flow.start(ani=VIKRAM_MOBILE, intent_id="hotlist_card")
    flow.submit_mpin("1111")
    state = None
    for _ in range(3):
        state = flow.submit_otp("000000")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.OTP_FAILED


def test_cli_matched_tier2_mpin_exhausted_has_no_fallback(flow):
    flow.start(ani=VIKRAM_MOBILE, intent_id="hotlist_card")
    state = None
    for _ in range(3):
        state = flow.submit_mpin("0000")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.MPIN_FAILED


# -- Tier 1, CLI mismatched / unavailable --------------------------------


@pytest.mark.parametrize("ani", [None, "0000000000"])
def test_cli_mismatch_tier1_identify_then_otp_success(flow, otp_gateway, ani):
    state = flow.start(ani=ani, intent_id="balance_enquiry")
    assert state.status == AuthStatus.NEEDS_IDENTIFICATION

    state = flow.submit_identification(account_last6="789012", card_last4="1111")
    assert state.status == AuthStatus.NEEDS_OTP

    code = otp_gateway.last_code_sent_to(ASHA_MOBILE)
    state = flow.submit_otp(code)
    assert state.status == AuthStatus.AUTHENTICATED
    assert state.tier_achieved == Tier.TIER_1
    assert state.cif == "CIF1001"


def test_cli_mismatch_identification_exhausted_escalates(flow):
    flow.start(ani=None, intent_id="balance_enquiry")
    flow.submit_identification(account_last6="000000", card_last4="0000")
    state = flow.submit_identification(account_last6="111111", card_last4="1111")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.CANNOT_IDENTIFY_CALLER


def test_cli_mismatch_no_registered_mobile_escalates(flow):
    flow.start(ani=None, intent_id="balance_enquiry")
    state = flow.submit_identification(
        account_last6=ROHIT_NO_MOBILE_ACCOUNT_LAST6, card_last4=ROHIT_CARD_LAST4
    )
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.NO_REGISTERED_MOBILE


# -- Tier 2, CLI mismatched -----------------------------------------------


def test_cli_mismatch_tier2_identify_otp_then_mpin_success(flow, otp_gateway):
    flow.start(ani=None, intent_id="hotlist_card")
    state = flow.submit_identification(account_last6="789099", card_last4="2222")
    assert state.status == AuthStatus.NEEDS_OTP

    code = otp_gateway.last_code_sent_to(VIKRAM_MOBILE)
    state = flow.submit_otp(code)
    assert state.status == AuthStatus.NEEDS_MPIN  # OTP alone is only the possession leg for tier 2

    state = flow.submit_mpin("1111")
    assert state.status == AuthStatus.AUTHENTICATED
    assert state.tier_achieved == Tier.TIER_2


def test_cli_mismatch_tier2_mpin_after_otp_exhausted_escalates(flow, otp_gateway):
    flow.start(ani=None, intent_id="hotlist_card")
    flow.submit_identification(account_last6="789099", card_last4="2222")
    code = otp_gateway.last_code_sent_to(VIKRAM_MOBILE)
    flow.submit_otp(code)
    state = None
    for _ in range(3):
        state = flow.submit_mpin("0000")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.MPIN_FAILED


# -- Account flags, MPIN lock pass-through --------------------------------


def test_flagged_account_escalates_silently_before_any_prompt(flow):
    state = flow.start(ani=MEERA_MOBILE, intent_id="balance_enquiry")
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.ACCOUNT_FLAGGED
    # anti-enumeration: the prompt must not reveal why
    assert "watchlist" not in (state.prompt or "").lower()
    assert "flag" not in (state.prompt or "").lower()


def test_pre_locked_mpin_escalates_immediately_even_if_correct(flow):
    flow.start(ani=PRIYA_MOBILE, intent_id="balance_enquiry")
    state = flow.submit_mpin("1357")  # correct MPIN, but account is locked
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.MPIN_LOCKED


# -- ASR confidence guard -------------------------------------------------


def test_low_confidence_reprompts_without_consuming_attempt_budget(flow):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")

    # Two low-confidence submissions: reprompted, not counted.
    for _ in range(2):
        state = flow.submit_mpin("4321", confidence=0.2)
        assert state.status == AuthStatus.NEEDS_REPEAT

    # Full attempt budget (3) should still be available at full confidence.
    for _ in range(2):
        state = flow.submit_mpin("0000", confidence=1.0)
        assert state.status == AuthStatus.NEEDS_MPIN
    state = flow.submit_mpin("0000", confidence=1.0)  # 3rd real wrong attempt
    assert state.status == AuthStatus.NEEDS_OTP  # fallback offered, not escalated early


def test_low_confidence_exceeding_cap_escalates(flow):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    state = None
    for _ in range(3):
        state = flow.submit_mpin("4321", confidence=0.2)
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.LOW_CONFIDENCE_EXCEEDED


# -- OTP resend ------------------------------------------------------------


def test_otp_resend_issues_a_new_code(flow, otp_gateway):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    for _ in range(3):
        flow.submit_mpin("0000")

    state = flow.request_otp_resend()
    assert state.status == AuthStatus.NEEDS_OTP
    new_code = otp_gateway.last_code_sent_to(ASHA_MOBILE)

    state = flow.submit_otp(new_code)
    assert state.status == AuthStatus.AUTHENTICATED


def test_otp_resend_limit_escalates(flow):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    for _ in range(3):
        flow.submit_mpin("0000")

    # 3 resends per call are allowed (authentication-flow.md SS7); the 4th is denied.
    state = None
    for _ in range(3):
        state = flow.request_otp_resend()
        assert state.status == AuthStatus.NEEDS_OTP
    state = flow.request_otp_resend()
    assert state.status == AuthStatus.ESCALATED
    assert state.escalation_reason == EscalationReason.OTP_FAILED


# -- OTP expiry --------------------------------------------------------


def test_expired_otp_counts_toward_verify_attempts_and_eventually_escalates(flow, otp_gateway, clock):
    flow.start(ani=ASHA_MOBILE, intent_id="balance_enquiry")
    for _ in range(3):
        flow.submit_mpin("0000")
    code = otp_gateway.last_code_sent_to(ASHA_MOBILE)
    clock.advance(181)  # past the 3-minute TTL

    state = flow.submit_otp(code)
    assert state.status == AuthStatus.NEEDS_OTP
    assert "expired" in (state.prompt or "").lower()

    state = flow.submit_otp(code)
    assert state.status == AuthStatus.NEEDS_OTP  # 2nd try
    state = flow.submit_otp(code)
    assert state.status == AuthStatus.ESCALATED  # 3rd try exhausts the ceiling
    assert state.escalation_reason == EscalationReason.OTP_FAILED


# -- Defensive / misuse ------------------------------------------------


def test_calling_submit_mpin_out_of_order_raises(flow):
    flow.start(ani=None, intent_id="balance_enquiry")  # CLI mismatch -> expects identification next
    with pytest.raises(RuntimeError):
        flow.submit_mpin("4321")


def test_unknown_intent_raises(flow):
    from ivr.auth.tier_config import UnknownIntentError

    with pytest.raises(UnknownIntentError):
        flow.start(ani=ASHA_MOBILE, intent_id="not_a_real_intent")
