"""The authentication state machine, implementing
docs/design/authentication-flow.md end to end: SS6 (flow), SS7 (retry/resend/
lockout policy), SS8 (escalation triggers).

Usage sketch (see tests/test_auth_flow.py for the full set of paths):

    flow = AuthFlow(cbs=DummyCBSClient(), otp_gateway=DummyOTPGateway(),
                     tier_map=IntentTierMap.load())
    state = flow.start(ani="9876500001", intent_id="balance_enquiry")
    # state.status == AuthStatus.NEEDS_MPIN (CLI matched -> ask for MPIN)
    state = flow.submit_mpin("4321")
    # state.status == AuthStatus.AUTHENTICATED, state.tier_achieved == Tier.TIER_1

Every submit_* method takes a `confidence` kwarg (0.0-1.0) representing the
ASR's confidence in what it heard. Below ASR_CONFIDENCE_THRESHOLD, the input
is not submitted to CBS/the OTP gateway at all and does not count against
the attempt ceilings in SS7 -- see _guard_confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

from ivr.auth.models import (
    AuthStatus,
    CBSClient,
    CLIMatchResult,
    EscalationReason,
    MpinCheckResult,
    OTPCheckResult,
    OTPGateway,
    Tier,
)
from ivr.auth.tier_config import IntentTierMap

MPIN_MAX_ATTEMPTS = 3
OTP_MAX_VERIFY_ATTEMPTS = 3
OTP_MAX_RESENDS = 3
IDENTIFICATION_MAX_ATTEMPTS = 2
SOFT_REPROMPT_MAX = 2
ASR_CONFIDENCE_THRESHOLD = 0.6

_GENERIC_ESCALATION_PROMPT = "Let me connect you with someone who can help you with that."


class _Stage(Enum):
    AWAITING_IDENTIFICATION = auto()  # CLI mismatch: need account+card to resolve a CIF
    AWAITING_MPIN_PRIMARY = auto()  # CLI matched: tier-1 primary factor, or tier-2 primary factor
    AWAITING_OTP_FALLBACK = auto()  # tier-1 only, after MPIN exhausted
    AWAITING_OTP_STEPUP = auto()  # CLI mismatch: OTP replacing the CLI possession leg
    AWAITING_OTP_SECOND_FACTOR = auto()  # tier-2, CLI matched: after MPIN success
    AWAITING_MPIN_AFTER_OTP = auto()  # tier-2, CLI mismatch: after OTP step-up success
    DONE = auto()


@dataclass
class AuthState:
    status: AuthStatus
    tier_required: Tier
    tier_achieved: Tier | None = None
    prompt: str | None = None
    escalation_reason: EscalationReason | None = None
    cif: str | None = None


@dataclass
class _RetryCounters:
    identification_attempts: int = 0
    mpin_attempts: int = 0
    otp_verify_attempts: int = 0
    otp_resends: int = 0
    soft_reprompts: dict[_Stage, int] = field(default_factory=dict)


class AuthFlow:
    def __init__(self, *, cbs: CBSClient, otp_gateway: OTPGateway, tier_map: IntentTierMap):
        self._cbs = cbs
        self._otp_gateway = otp_gateway
        self._tier_map = tier_map

        self._stage = _Stage.DONE
        self._tier_required: Tier = Tier.TIER_0
        self._tier_achieved: Tier | None = None
        self._cif: str | None = None
        self._otp_reference: str | None = None
        self._retries = _RetryCounters()

    # -- entry point ---------------------------------------------------

    def start(self, ani: str | None, intent_id: str) -> AuthState:
        self._tier_required = self._tier_map.tier_for(intent_id)

        if self._tier_required == Tier.TIER_0:
            self._stage = _Stage.DONE
            return self._state(AuthStatus.NO_AUTH_REQUIRED, tier_achieved=Tier.TIER_0)

        if ani is None:
            cli_result = CLIMatchResult.UNAVAILABLE
        else:
            cif = self._cbs.resolve_cif_by_mobile(ani)
            cli_result = CLIMatchResult.MATCHED if cif else CLIMatchResult.NOT_MATCHED
            if cif:
                self._cif = cif

        if cli_result == CLIMatchResult.MATCHED:
            flagged = self._check_account_flags()
            if flagged:
                return flagged
            self._stage = _Stage.AWAITING_MPIN_PRIMARY
            return self._state(AuthStatus.NEEDS_MPIN, prompt="Please say your MPIN.")

        self._stage = _Stage.AWAITING_IDENTIFICATION
        return self._state(
            AuthStatus.NEEDS_IDENTIFICATION,
            # One thing at a time -- the telephony layer captures account and
            # card digits as two separate turns (app.py's identification_step),
            # so this must only ask for the first of them. Asking for both in
            # one sentence invites the caller to say both back at once, which
            # gets misread as the account number alone (see
            # digit_normalizer.py's module docstring for the concatenation
            # problem that causes).
            prompt="I couldn't verify your number automatically. Please say the last 6 digits of your account number.",
        )

    # -- submissions -----------------------------------------------------

    def submit_identification(self, account_last6: str, card_last4: str, *, confidence: float = 1.0) -> AuthState:
        self._require_stage(_Stage.AWAITING_IDENTIFICATION)
        soft = self._guard_confidence(confidence)
        if soft is not None:
            return soft

        self._retries.identification_attempts += 1
        cif = self._cbs.resolve_cif_by_identity(account_last6, card_last4)

        if cif is None:
            if self._retries.identification_attempts >= IDENTIFICATION_MAX_ATTEMPTS:
                return self._escalate(EscalationReason.CANNOT_IDENTIFY_CALLER)
            return self._state(
                AuthStatus.NEEDS_IDENTIFICATION,
                prompt="I couldn't find an account matching that. Let's try again -- please say the last 6 digits of your account number.",
            )

        self._cif = cif
        flagged = self._check_account_flags()
        if flagged:
            return flagged
        return self._dispatch_otp_after_identification()

    def submit_mpin(self, mpin: str, *, confidence: float = 1.0) -> AuthState:
        self._require_stage(_Stage.AWAITING_MPIN_PRIMARY, _Stage.AWAITING_MPIN_AFTER_OTP)
        soft = self._guard_confidence(confidence)
        if soft is not None:
            return soft

        self._retries.mpin_attempts += 1
        result = self._cbs.validate_mpin(self._cif, mpin)

        if result == MpinCheckResult.LOCKED:
            return self._escalate(EscalationReason.MPIN_LOCKED)

        if result == MpinCheckResult.CORRECT:
            if self._stage == _Stage.AWAITING_MPIN_AFTER_OTP:
                return self._authenticated(Tier.TIER_2)
            # AWAITING_MPIN_PRIMARY
            if self._tier_required == Tier.TIER_1:
                return self._authenticated(Tier.TIER_1)
            return self._dispatch_otp_for_tier2_second_factor()

        # INCORRECT
        if self._retries.mpin_attempts >= MPIN_MAX_ATTEMPTS:
            if self._stage == _Stage.AWAITING_MPIN_PRIMARY and self._tier_required == Tier.TIER_1:
                return self._dispatch_otp_fallback()
            return self._escalate(EscalationReason.MPIN_FAILED)
        return self._state(AuthStatus.NEEDS_MPIN, prompt="That MPIN doesn't match. Please say it again.")

    def submit_otp(self, otp: str, *, confidence: float = 1.0) -> AuthState:
        self._require_stage(
            _Stage.AWAITING_OTP_FALLBACK, _Stage.AWAITING_OTP_STEPUP, _Stage.AWAITING_OTP_SECOND_FACTOR
        )
        soft = self._guard_confidence(confidence)
        if soft is not None:
            return soft

        result = self._otp_gateway.validate_otp(self._otp_reference, otp)

        if result == OTPCheckResult.CORRECT:
            return self._on_otp_correct()

        # INCORRECT, EXPIRED, or NOT_FOUND (e.g. re-submitting an already-consumed
        # code) all count against the verify ceiling -- otherwise a caller who
        # keeps re-submitting a stale code without ever asking to resend would
        # loop here forever with no escalation.
        self._retries.otp_verify_attempts += 1
        if self._retries.otp_verify_attempts >= OTP_MAX_VERIFY_ATTEMPTS:
            return self._escalate(EscalationReason.OTP_FAILED)
        if result == OTPCheckResult.EXPIRED:
            return self._state(
                AuthStatus.NEEDS_OTP,
                prompt="That code has expired. Say 'resend' and I'll send you a new one.",
            )
        return self._state(AuthStatus.NEEDS_OTP, prompt="That code doesn't match. Please say it again.")

    def request_otp_resend(self) -> AuthState:
        self._require_stage(
            _Stage.AWAITING_OTP_FALLBACK, _Stage.AWAITING_OTP_STEPUP, _Stage.AWAITING_OTP_SECOND_FACTOR
        )
        if self._retries.otp_resends >= OTP_MAX_RESENDS:
            return self._escalate(EscalationReason.OTP_FAILED)

        self._retries.otp_resends += 1
        self._retries.otp_verify_attempts = 0
        mobile = self._cbs.get_registered_mobile(self._cif)
        self._otp_reference = self._otp_gateway.send_otp(mobile)
        return self._state(AuthStatus.NEEDS_OTP, prompt="I've sent a new code. Please say it when you're ready.")

    # -- internal transitions ---------------------------------------------

    def _dispatch_otp_after_identification(self) -> AuthState:
        """CLI-mismatch path: identification just succeeded, OTP is the
        possession-leg replacement for the failed CLI check."""
        mobile = self._cbs.get_registered_mobile(self._cif)
        if mobile is None:
            return self._escalate(EscalationReason.NO_REGISTERED_MOBILE)
        self._otp_reference = self._otp_gateway.send_otp(mobile)
        self._stage = _Stage.AWAITING_OTP_STEPUP
        return self._state(
            AuthStatus.NEEDS_OTP,
            prompt="I've sent a one-time code to your registered mobile number. Please say the code.",
        )

    def _dispatch_otp_fallback(self) -> AuthState:
        """CLI-matched, tier-1, MPIN exhausted: offer OTP as the fallback
        single factor rather than escalating outright."""
        mobile = self._cbs.get_registered_mobile(self._cif)
        if mobile is None:
            return self._escalate(EscalationReason.NO_REGISTERED_MOBILE)
        self._otp_reference = self._otp_gateway.send_otp(mobile)
        self._stage = _Stage.AWAITING_OTP_FALLBACK
        return self._state(
            AuthStatus.NEEDS_OTP,
            prompt=(
                "Let's try a different way. I've sent a one-time code to your "
                "registered mobile number. Please say the code."
            ),
        )

    def _dispatch_otp_for_tier2_second_factor(self) -> AuthState:
        """CLI-matched, tier-2, MPIN just succeeded: OTP is the second
        required factor."""
        mobile = self._cbs.get_registered_mobile(self._cif)
        if mobile is None:
            return self._escalate(EscalationReason.NO_REGISTERED_MOBILE)
        self._otp_reference = self._otp_gateway.send_otp(mobile)
        self._stage = _Stage.AWAITING_OTP_SECOND_FACTOR
        return self._state(
            AuthStatus.NEEDS_OTP,
            prompt="Thanks. I've also sent a one-time code to your registered mobile number. Please say it.",
        )

    def _on_otp_correct(self) -> AuthState:
        if self._stage == _Stage.AWAITING_OTP_FALLBACK:
            return self._authenticated(Tier.TIER_1)
        if self._stage == _Stage.AWAITING_OTP_SECOND_FACTOR:
            return self._authenticated(Tier.TIER_2)
        # AWAITING_OTP_STEPUP
        if self._tier_required == Tier.TIER_1:
            return self._authenticated(Tier.TIER_1)
        self._stage = _Stage.AWAITING_MPIN_AFTER_OTP
        self._retries.mpin_attempts = 0
        return self._state(AuthStatus.NEEDS_MPIN, prompt="Thanks, that's verified. Now please say your MPIN.")

    # -- helpers ------------------------------------------------------

    def _check_account_flags(self) -> AuthState | None:
        if self._cbs.get_account_flags(self._cif):
            # Escalate silently -- authentication-flow.md SS8: don't reveal
            # the reason to the caller.
            return self._escalate(EscalationReason.ACCOUNT_FLAGGED)
        return None

    def _guard_confidence(self, confidence: float) -> AuthState | None:
        if confidence >= ASR_CONFIDENCE_THRESHOLD:
            return None
        count = self._retries.soft_reprompts.get(self._stage, 0) + 1
        self._retries.soft_reprompts[self._stage] = count
        if count > SOFT_REPROMPT_MAX:
            return self._escalate(EscalationReason.LOW_CONFIDENCE_EXCEEDED)
        return self._state(AuthStatus.NEEDS_REPEAT, prompt="Sorry, I didn't catch that clearly. Could you say it again?")

    def _authenticated(self, tier: Tier) -> AuthState:
        self._stage = _Stage.DONE
        self._tier_achieved = tier
        return self._state(AuthStatus.AUTHENTICATED, tier_achieved=tier)

    def _escalate(self, reason: EscalationReason) -> AuthState:
        self._stage = _Stage.DONE
        return self._state(AuthStatus.ESCALATED, escalation_reason=reason, prompt=_GENERIC_ESCALATION_PROMPT)

    def _state(self, status: AuthStatus, *, tier_achieved: Tier | None = None, prompt: str | None = None,
               escalation_reason: EscalationReason | None = None) -> AuthState:
        return AuthState(
            status=status,
            tier_required=self._tier_required,
            tier_achieved=tier_achieved if tier_achieved is not None else self._tier_achieved,
            prompt=prompt,
            escalation_reason=escalation_reason,
            cif=self._cif,
        )

    def _require_stage(self, *valid: _Stage) -> None:
        if self._stage not in valid:
            raise RuntimeError(
                f"Unexpected call for current stage {self._stage.name}; expected one of {[s.name for s in valid]}"
            )
