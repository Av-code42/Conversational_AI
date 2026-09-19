"""Service Agent -- owns change_of_address, cheque_book_request, kyc_update.

All three tools mutate a record (they create a service-request ticket), so
all three now confirm before actually submitting -- a guardrail against a
misheard address or an accidental request silently going through. Saying
"no" to an address confirmation lets the caller re-state it; saying "no" to
a cheque-book/KYC confirmation just cancels (nothing to re-state there).

change_of_address is still the one tool with real slot-filling (it can't be
defaulted the way the other two can), including topic-switch detection: if
the caller says something else entirely while being asked for their
address, this hands back to the Coordinator via NEED_HANDOFF instead of
treating it as address input. That reclassification also happens to catch
most "off-topic text mistaken for an address" cases before they'd ever
reach confirmation -- but it isn't foolproof (an intent classifier can only
recognize the intents it knows about), which is exactly why confirmation
exists as a second line of defense: even if an off-topic sentence slips
past the length/topic-switch checks, the caller sees it read back and can
reject it before anything is actually saved.

A per-call ServiceRequestLimiter (rate_limit.py) caps how many service
requests a single call can submit -- guardrail against a call (accidental
looping or deliberate abuse) racking up an unreasonable number of tickets.
Checked immediately before every actual submission, after confirmation.
"""

from __future__ import annotations

from enum import Enum, auto

from ivr.agents.models import AgentState, AgentStatus
from ivr.agents.rate_limit import ServiceRequestLimiter
from ivr.banking.models import BankingClient
from ivr.coordinator.intent_classifier import IntentClassifier

ASR_CONFIDENCE_THRESHOLD = 0.6
SOFT_REPROMPT_MAX = 2
MIN_PLAUSIBLE_ADDRESS_LENGTH = 8  # heuristic: shorter than this can't be a real address

_YES_WORDS = {"yes", "yeah", "yep", "yup", "correct", "confirm", "confirmed", "sure"}
_NO_WORDS = {"no", "nope", "cancel", "wrong", "incorrect", "don't", "dont", "stop"}


def _parse_yes_no(text: str) -> bool | None:
    words = set(text.strip().lower().split())
    is_yes = bool(words & _YES_WORDS)
    is_no = bool(words & _NO_WORDS)
    if is_yes and not is_no:
        return True
    if is_no and not is_yes:
        return False
    return None  # ambiguous or neither -- e.g. empty, unrelated text, or both matched


class _Stage(Enum):
    AWAITING_ADDRESS = auto()
    AWAITING_ADDRESS_CONFIRMATION = auto()
    AWAITING_TOOL_CONFIRMATION = auto()  # cheque_book_request / kyc_update
    DONE = auto()


class ServiceAgent:
    def __init__(self, banking: BankingClient, intent_classifier: IntentClassifier, limiter: ServiceRequestLimiter):
        self._banking = banking
        self._intent_classifier = intent_classifier
        self._limiter = limiter

        self._cif: str | None = None
        self._pending_intent_id: str | None = None
        self._pending_address: str | None = None
        self._stage: _Stage = _Stage.DONE
        self._soft_reprompts = 0
        self._address_format_reprompts = 0
        self._confirm_reprompts = 0

    def start(self, intent_id: str, cif: str) -> AgentState:
        self._cif = cif
        self._pending_intent_id = intent_id

        if intent_id == "change_of_address":
            self._stage = _Stage.AWAITING_ADDRESS
            return AgentState(status=AgentStatus.NEEDS_INPUT, prompt="Sure -- what's your new address?")

        if intent_id == "cheque_book_request":
            self._stage = _Stage.AWAITING_TOOL_CONFIRMATION
            return AgentState(
                status=AgentStatus.NEEDS_INPUT,
                prompt="I'll request a new 20-leaf cheque book, delivered to your registered address. "
                "Shall I go ahead? Please say yes or no.",
            )

        if intent_id == "kyc_update":
            self._stage = _Stage.AWAITING_TOOL_CONFIRMATION
            return AgentState(
                status=AgentStatus.NEEDS_INPUT,
                prompt="I'll register a KYC re-verification request for your account. "
                "Shall I go ahead? Please say yes or no.",
            )

        raise ValueError(f"ServiceAgent doesn't own intent {intent_id!r}")

    def submit_input(self, text: str, *, confidence: float = 1.0) -> AgentState:
        if self._stage == _Stage.DONE:
            raise RuntimeError("ServiceAgent.submit_input called with nothing pending")

        if confidence < ASR_CONFIDENCE_THRESHOLD:
            self._soft_reprompts += 1
            if self._soft_reprompts > SOFT_REPROMPT_MAX:
                return self._escalate("low_confidence_exceeded")
            return AgentState(status=AgentStatus.NEEDS_INPUT, prompt="Sorry, I didn't catch that. Could you say that again?")

        if self._stage == _Stage.AWAITING_ADDRESS:
            return self._handle_address_input(text)
        if self._stage == _Stage.AWAITING_ADDRESS_CONFIRMATION:
            return self._handle_address_confirmation(text)
        return self._handle_tool_confirmation(text)  # AWAITING_TOOL_CONFIRMATION

    # -- change_of_address: collect, then confirm -----------------------

    def _handle_address_input(self, text: str) -> AgentState:
        # Topic-switch detection: reuse the same intent classifier the
        # Coordinator uses for its own intent capture. Not foolproof -- see
        # the module docstring -- which is exactly why confirmation (below)
        # exists as a second line of defense.
        maybe_new_intent = self._intent_classifier.classify(text)
        if maybe_new_intent and maybe_new_intent != self._pending_intent_id:
            self._stage = _Stage.DONE
            return AgentState(status=AgentStatus.NEED_HANDOFF, new_intent_id=maybe_new_intent)

        if len(text.strip()) < MIN_PLAUSIBLE_ADDRESS_LENGTH:
            self._address_format_reprompts += 1
            if self._address_format_reprompts > SOFT_REPROMPT_MAX:
                return self._escalate("address_format_not_understood")
            return AgentState(
                status=AgentStatus.NEEDS_INPUT,
                prompt="That doesn't sound like a complete address -- could you say the full address again?",
            )

        self._pending_address = text.strip()
        self._stage = _Stage.AWAITING_ADDRESS_CONFIRMATION
        return AgentState(
            status=AgentStatus.NEEDS_INPUT,
            prompt=f"You said: {self._pending_address}. Shall I update this as your new address? Please say yes or no.",
        )

    def _handle_address_confirmation(self, text: str) -> AgentState:
        answer = _parse_yes_no(text)
        if answer is None:
            return self._reprompt_confirmation("Sorry, please say yes to confirm, or no to try a different address.")

        if answer is False:
            self._stage = _Stage.AWAITING_ADDRESS
            self._pending_address = None
            self._confirm_reprompts = 0
            return AgentState(status=AgentStatus.NEEDS_INPUT, prompt="No problem -- what's the correct address?")

        limited = self._check_rate_limit()
        if limited:
            return limited

        ticket = self._banking.submit_service_request(self._cif, "change_of_address", {"new_address": self._pending_address})
        self._limiter.record_submission()
        self._stage = _Stage.DONE
        return AgentState(status=AgentStatus.COMPLETED, prompt=f"Got it, I've updated your address on file. Reference {ticket}.")

    # -- cheque_book_request / kyc_update: confirm, then execute -----------

    def _handle_tool_confirmation(self, text: str) -> AgentState:
        answer = _parse_yes_no(text)
        if answer is None:
            return self._reprompt_confirmation("Sorry, please say yes or no.")

        if answer is False:
            self._stage = _Stage.DONE
            return AgentState(status=AgentStatus.COMPLETED, prompt="No problem, I haven't submitted that request.")

        limited = self._check_rate_limit()
        if limited:
            return limited

        self._stage = _Stage.DONE
        if self._pending_intent_id == "cheque_book_request":
            ticket = self._banking.submit_service_request(
                self._cif, "cheque_book_request", {"leaves": 20, "delivery": "registered address"}
            )
            self._limiter.record_submission()
            return AgentState(
                status=AgentStatus.COMPLETED,
                prompt=f"Your cheque book request is confirmed, reference {ticket}. "
                f"It'll be delivered to your registered address.",
            )

        ticket = self._banking.submit_service_request(self._cif, "kyc_update", {})
        self._limiter.record_submission()
        return AgentState(
            status=AgentStatus.COMPLETED,
            prompt=f"I've registered your KYC re-verification request, reference {ticket}. Our team will follow up with you.",
        )

    # -- shared helpers --------------------------------------------------

    def _reprompt_confirmation(self, prompt: str) -> AgentState:
        self._confirm_reprompts += 1
        if self._confirm_reprompts > SOFT_REPROMPT_MAX:
            return self._escalate("confirmation_not_understood")
        return AgentState(status=AgentStatus.NEEDS_INPUT, prompt=prompt)

    def _check_rate_limit(self) -> AgentState | None:
        if self._limiter.can_submit():
            return None
        return self._escalate("service_request_limit_exceeded")

    def _escalate(self, reason: str) -> AgentState:
        self._stage = _Stage.DONE
        return AgentState(status=AgentStatus.ESCALATE, escalation_reason=reason)
