"""Accounts Agent -- owns balance_enquiry. No slot-filling: the dummy
banking data has exactly one account per customer, so there's nothing to
disambiguate. A real deployment with multi-account customers would need to
ask "which account?" here -- flagged as a v1 simplification, not an
oversight.
"""

from __future__ import annotations

from ivr.agents.models import AgentState, AgentStatus
from ivr.banking.models import BankingClient


class AccountsAgent:
    def __init__(self, banking: BankingClient):
        self._banking = banking

    def start(self, intent_id: str, cif: str) -> AgentState:
        if intent_id != "balance_enquiry":
            raise ValueError(f"AccountsAgent doesn't own intent {intent_id!r}")
        balance = self._banking.get_balance(cif)
        return AgentState(status=AgentStatus.COMPLETED, prompt=f"Your current balance is {balance:,.2f} rupees.")

    def submit_input(self, text: str, *, confidence: float = 1.0) -> AgentState:
        raise RuntimeError("AccountsAgent never asks for input -- start() always completes immediately")
