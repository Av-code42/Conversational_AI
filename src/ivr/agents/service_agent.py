"""Service Agent -- owns change_of_address, cheque_book_request, kyc_update.

cheque_book_request and kyc_update are zero-slot (same v1 simplification as
transaction_agent.py: sensible defaults, no slot-filling). change_of_address
is the one tool in this whole build that genuinely can't be defaulted --
it needs the actual new address -- so this is the one real multi-turn
slot-filling agent, including topic-switch detection: if the caller says
something else entirely while being asked for their address (e.g. "actually
what's my balance"), this hands back to the Coordinator via NEED_HANDOFF
instead of trying to save "what's my balance" as an address.
"""

from __future__ import annotations

from ivr.agents.models import AgentState, AgentStatus
from ivr.banking.models import BankingClient
from ivr.coordinator.intent_classifier import IntentClassifier

ASR_CONFIDENCE_THRESHOLD = 0.6
SOFT_REPROMPT_MAX = 2
MIN_PLAUSIBLE_ADDRESS_LENGTH = 8  # heuristic: shorter than this can't be a real address


class ServiceAgent:
    def __init__(self, banking: BankingClient, intent_classifier: IntentClassifier):
        self._banking = banking
        self._intent_classifier = intent_classifier

        self._cif: str | None = None
        self._pending_intent_id: str | None = None
        self._awaiting_address = False
        self._soft_reprompts = 0

    def start(self, intent_id: str, cif: str) -> AgentState:
        self._cif = cif
        self._pending_intent_id = intent_id

        if intent_id == "change_of_address":
            self._awaiting_address = True
            return AgentState(status=AgentStatus.NEEDS_INPUT, prompt="Sure -- what's your new address?")

        if intent_id == "cheque_book_request":
            ticket = self._banking.submit_service_request(
                cif, "cheque_book_request", {"leaves": 20, "delivery": "registered address"}
            )
            return AgentState(
                status=AgentStatus.COMPLETED,
                prompt=f"Your cheque book request is confirmed, reference {ticket}. "
                f"It'll be delivered to your registered address.",
            )

        if intent_id == "kyc_update":
            ticket = self._banking.submit_service_request(cif, "kyc_update", {})
            return AgentState(
                status=AgentStatus.COMPLETED,
                prompt=f"I've registered your KYC re-verification request, reference {ticket}. "
                f"Our team will follow up with you.",
            )

        raise ValueError(f"ServiceAgent doesn't own intent {intent_id!r}")

    def submit_input(self, text: str, *, confidence: float = 1.0) -> AgentState:
        if not self._awaiting_address:
            raise RuntimeError("ServiceAgent.submit_input called with nothing pending")

        if confidence < ASR_CONFIDENCE_THRESHOLD:
            self._soft_reprompts += 1
            if self._soft_reprompts > SOFT_REPROMPT_MAX:
                return AgentState(status=AgentStatus.ESCALATE, escalation_reason="low_confidence_exceeded")
            return AgentState(status=AgentStatus.NEEDS_INPUT, prompt="Sorry, I didn't catch that. What's your new address?")

        # Topic-switch detection: reuse the same intent classifier the
        # Coordinator uses for its own intent capture. Not foolproof -- an
        # address that happens to contain a word like "cheque" (e.g. a road
        # name) could misfire -- but far better than blindly saving
        # whatever's said next as the new address.
        maybe_new_intent = self._intent_classifier.classify(text)
        if maybe_new_intent and maybe_new_intent != self._pending_intent_id:
            self._awaiting_address = False
            return AgentState(status=AgentStatus.NEED_HANDOFF, new_intent_id=maybe_new_intent)

        if len(text.strip()) < MIN_PLAUSIBLE_ADDRESS_LENGTH:
            return AgentState(
                status=AgentStatus.NEEDS_INPUT,
                prompt="That doesn't sound like a complete address -- could you say the full address again?",
            )

        ticket = self._banking.submit_service_request(self._cif, "change_of_address", {"new_address": text.strip()})
        self._awaiting_address = False
        return AgentState(status=AgentStatus.COMPLETED, prompt=f"Got it, I've updated your address on file. Reference {ticket}.")
