"""Maps agent_id (from config/intent_tier_map.yaml, surfaced as
CoordinatorState.agent_id) to the domain agent that should handle it.

create() returns a FRESH instance every call -- an agent's internal state
(e.g. ServiceAgent's slot-filling progress) must never leak across
different intents or different calls, same principle as AuthFlow/
CoordinatorFlow being instantiated fresh per call.
"""

from __future__ import annotations

from ivr.agents.accounts_agent import AccountsAgent
from ivr.agents.models import DomainAgent
from ivr.agents.service_agent import ServiceAgent
from ivr.agents.transaction_agent import TransactionAgent
from ivr.banking.models import BankingClient
from ivr.coordinator.intent_classifier import IntentClassifier


class AgentRegistry:
    def __init__(self, banking: BankingClient, intent_classifier: IntentClassifier):
        self._banking = banking
        self._intent_classifier = intent_classifier

    def create(self, agent_id: str) -> DomainAgent:
        if agent_id == "accounts_agent":
            return AccountsAgent(self._banking)
        if agent_id == "transaction_agent":
            return TransactionAgent(self._banking)
        if agent_id == "service_agent":
            return ServiceAgent(self._banking, self._intent_classifier)
        raise ValueError(f"Unknown agent_id: {agent_id!r}")
