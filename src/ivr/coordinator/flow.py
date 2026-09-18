"""The Coordinator Agent, implementing docs/design/agent-architecture.md
SS3: greeting + intent capture, resolving the owning agent and required tier
from config/intent_tier_map.yaml (via IntentTierMap, tier_config.py), running
AuthFlow as a gate before handoff, and owning global commands and escalation
centrally for its own turns.

No domain agent (Accounts/Transaction/Service) exists in code yet -- this
stops at "authenticated and ready to hand off to <agent_id> for <intent_id>"
(CoordinatorStatus.READY_FOR_HANDOFF). Call mark_task_completed() to
simulate a domain agent finishing its work and returning control, so the
"anything else?" loop (agent-architecture.md SS3 step 6) is exercisable
before a real domain agent exists.

Known simplification, flagged rather than silently done differently: mid-
call tier step-up (agent-architecture.md SS5) is supposed to re-run
authentication for only the delta (e.g. already have MPIN, just add OTP).
This implementation re-runs AuthFlow from scratch for the new tier instead
-- simpler, and currently unreachable with the real intent catalog anyway
(config/intent_tier_map.yaml has no Tier 2 intents yet), but worth fixing
before Tier 2 intents actually ship, since it means a caller who already
gave their MPIN this call would be asked for it again on step-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ivr.auth.flow import AuthFlow, AuthState
from ivr.auth.models import AuthStatus, CBSClient, EscalationReason, OTPGateway, Tier
from ivr.auth.tier_config import IntentTierMap
from ivr.coordinator.intent_classifier import IntentClassifier
from ivr.shared.global_commands import GlobalCommand, detect_global_command

GREETING = "Thank you for calling ABC Retail Bank. How can I help you today?"
ANYTHING_ELSE_PROMPT = "Is there anything else I can help you with?"
INTENT_NO_MATCH_PROMPT = "Sorry, I didn't quite catch that. Could you tell me what you'd like to do?"
INTENT_LOW_CONFIDENCE_PROMPT = "Sorry, I didn't catch that clearly. Could you say that again?"
GENERIC_ESCALATION_PROMPT = "Let me connect you with someone who can help you with that."

INTENT_NO_MATCH_MAX = 2  # authentication-flow.md SS8's 2-reprompt pattern, applied to intent capture
INTENT_SOFT_REPROMPT_MAX = 2
ASR_CONFIDENCE_THRESHOLD = 0.6

_TIER_RANK: dict[Tier, int] = {Tier.TIER_0: 0, Tier.TIER_1: 1, Tier.TIER_2: 2}


class CoordinatorStatus(str, Enum):
    NEEDS_INTENT = "needs_intent"
    # Mirror AuthStatus's values exactly for the proxied auth statuses so a
    # single AuthStatus -> CoordinatorStatus cast works in _from_auth_state.
    NEEDS_IDENTIFICATION = "needs_identification"
    NEEDS_MPIN = "needs_mpin"
    NEEDS_OTP = "needs_otp"
    NEEDS_REPEAT = "needs_repeat"
    READY_FOR_HANDOFF = "ready_for_handoff"
    ESCALATED = "escalated"


class CoordinatorEscalationReason(str, Enum):
    EXPLICIT_REQUEST = "explicit_request"  # caller asked for a human, at intent capture
    INTENT_NOT_UNDERSTOOD = "intent_not_understood"
    AUTH_ESCALATED = "auth_escalated"  # AuthFlow escalated; see CoordinatorState.auth_escalation_reason


@dataclass
class CoordinatorState:
    status: CoordinatorStatus
    prompt: str | None = None
    intent_id: str | None = None
    agent_id: str | None = None
    tier_required: Tier | None = None
    tier_achieved: Tier | None = None
    cif: str | None = None
    escalation_reason: CoordinatorEscalationReason | None = None
    auth_escalation_reason: EscalationReason | None = None


@dataclass
class _HandoffRecord:
    agent_id: str
    intent_id: str


class CoordinatorFlow:
    def __init__(self, *, cbs: CBSClient, otp_gateway: OTPGateway, tier_map: IntentTierMap,
                 intent_classifier: IntentClassifier):
        self._cbs = cbs
        self._otp_gateway = otp_gateway
        self._tier_map = tier_map
        self._intent_classifier = intent_classifier

        self._ani: str | None = None
        self._cif: str | None = None
        self._tier_achieved: Tier | None = None
        self._handoff_history: list[_HandoffRecord] = []

        self._auth: AuthFlow | None = None
        self._pending_intent_id: str | None = None
        self._pending_agent_id: str | None = None
        self._intent_no_match_count = 0
        self._intent_soft_reprompts = 0
        self._last_intent_prompt = GREETING

    # -- entry point -----------------------------------------------------

    def start(self, ani: str | None) -> CoordinatorState:
        self._ani = ani
        return self._to_intent_capture(GREETING)

    # -- intent capture turn ----------------------------------------------

    def submit_utterance(self, text: str, *, confidence: float = 1.0) -> CoordinatorState:
        command = detect_global_command(text)
        if command == GlobalCommand.ESCALATE_TO_CSR:
            return self._escalate(CoordinatorEscalationReason.EXPLICIT_REQUEST)
        if command == GlobalCommand.START_OVER:
            self._tier_achieved = None  # re-authenticate from here; caller/cif/ani are unaffected
            self._handoff_history.clear()
            return self._to_intent_capture(GREETING)
        if command == GlobalCommand.REPEAT:
            return self._to_intent_capture(self._last_intent_prompt, reset_counts=False)

        if confidence < ASR_CONFIDENCE_THRESHOLD:
            self._intent_soft_reprompts += 1
            if self._intent_soft_reprompts > INTENT_SOFT_REPROMPT_MAX:
                return self._escalate(CoordinatorEscalationReason.INTENT_NOT_UNDERSTOOD)
            return self._to_intent_capture(INTENT_LOW_CONFIDENCE_PROMPT, reset_counts=False)

        intent_id = self._intent_classifier.classify(text)
        if intent_id is None or intent_id not in self._tier_map:
            self._intent_no_match_count += 1
            if self._intent_no_match_count >= INTENT_NO_MATCH_MAX:
                return self._escalate(CoordinatorEscalationReason.INTENT_NOT_UNDERSTOOD)
            return self._to_intent_capture(INTENT_NO_MATCH_PROMPT, reset_counts=False)

        self._intent_no_match_count = 0
        self._intent_soft_reprompts = 0
        return self._route(intent_id)

    # -- routing / auth gate ------------------------------------------------

    def _route(self, intent_id: str) -> CoordinatorState:
        agent_id = self._tier_map.agent_for(intent_id)
        tier_required = self._tier_map.tier_for(intent_id)
        self._pending_intent_id = intent_id
        self._pending_agent_id = agent_id

        if self._tier_achieved is not None and _TIER_RANK[self._tier_achieved] >= _TIER_RANK[tier_required]:
            return self._handoff(intent_id, agent_id, tier_required, self._tier_achieved)

        self._auth = AuthFlow(cbs=self._cbs, otp_gateway=self._otp_gateway, tier_map=self._tier_map)
        return self._from_auth_state(self._auth.start(ani=self._ani, intent_id=intent_id))

    # -- auth factor passthrough --------------------------------------------

    def submit_identification(self, account_last6: str, card_last4: str, *, confidence: float = 1.0) -> CoordinatorState:
        self._require_active_auth()
        return self._from_auth_state(self._auth.submit_identification(account_last6, card_last4, confidence=confidence))

    def submit_mpin(self, mpin: str, *, confidence: float = 1.0) -> CoordinatorState:
        self._require_active_auth()
        return self._from_auth_state(self._auth.submit_mpin(mpin, confidence=confidence))

    def submit_otp(self, otp: str, *, confidence: float = 1.0) -> CoordinatorState:
        self._require_active_auth()
        return self._from_auth_state(self._auth.submit_otp(otp, confidence=confidence))

    def request_otp_resend(self) -> CoordinatorState:
        self._require_active_auth()
        return self._from_auth_state(self._auth.request_otp_resend())

    # -- regaining control after a (simulated) domain agent -------------------

    def mark_task_completed(self) -> CoordinatorState:
        """Simulates a domain agent returning COMPLETED (agent-architecture.md
        SS4) -- there's no real domain agent yet to call this for us."""
        self._auth = None
        return self._to_intent_capture(ANYTHING_ELSE_PROMPT)

    # -- internal -------------------------------------------------------

    def _from_auth_state(self, auth_state: AuthState) -> CoordinatorState:
        if auth_state.status in (AuthStatus.AUTHENTICATED, AuthStatus.NO_AUTH_REQUIRED):
            self._tier_achieved = auth_state.tier_achieved
            self._cif = auth_state.cif
            return self._handoff(self._pending_intent_id, self._pending_agent_id, auth_state.tier_required, auth_state.tier_achieved)

        if auth_state.status == AuthStatus.ESCALATED:
            return self._escalate(CoordinatorEscalationReason.AUTH_ESCALATED, auth_escalation_reason=auth_state.escalation_reason)

        # NEEDS_IDENTIFICATION / NEEDS_MPIN / NEEDS_OTP / NEEDS_REPEAT proxy straight through --
        # CoordinatorStatus and AuthStatus share these string values by design.
        return CoordinatorState(
            status=CoordinatorStatus(auth_state.status.value),
            prompt=auth_state.prompt,
            intent_id=self._pending_intent_id,
            agent_id=self._pending_agent_id,
            tier_required=auth_state.tier_required,
            tier_achieved=self._tier_achieved,
            cif=auth_state.cif,
        )

    def _handoff(self, intent_id: str, agent_id: str, tier_required: Tier, tier_achieved: Tier | None) -> CoordinatorState:
        self._handoff_history.append(_HandoffRecord(agent_id=agent_id, intent_id=intent_id))
        self._auth = None
        return CoordinatorState(
            status=CoordinatorStatus.READY_FOR_HANDOFF,
            intent_id=intent_id,
            agent_id=agent_id,
            tier_required=tier_required,
            tier_achieved=tier_achieved,
            cif=self._cif,
        )

    def _escalate(self, reason: CoordinatorEscalationReason, *, auth_escalation_reason: EscalationReason | None = None) -> CoordinatorState:
        self._auth = None
        return CoordinatorState(
            status=CoordinatorStatus.ESCALATED,
            prompt=GENERIC_ESCALATION_PROMPT,
            escalation_reason=reason,
            auth_escalation_reason=auth_escalation_reason,
            tier_achieved=self._tier_achieved,
            cif=self._cif,
        )

    def _to_intent_capture(self, prompt: str, *, reset_counts: bool = True) -> CoordinatorState:
        self._last_intent_prompt = prompt
        if reset_counts:
            self._intent_no_match_count = 0
            self._intent_soft_reprompts = 0
        return CoordinatorState(
            status=CoordinatorStatus.NEEDS_INTENT,
            prompt=prompt,
            tier_achieved=self._tier_achieved,
            cif=self._cif,
        )

    def _require_active_auth(self) -> None:
        if self._auth is None:
            raise RuntimeError("No authentication in progress -- call submit_utterance() to route an intent first")
