"""Transaction Agent -- owns transaction_details and statement_request.

V1 simplification, flagged rather than silent: neither tool asks for a
date range or count -- transaction_details always returns the last 5
transactions, statement_request always returns the last 30 days. A real
product would let a caller ask for a specific period; that's slot-filling
this version doesn't build, since neither tool strictly requires it to be
useful.
"""

from __future__ import annotations

from ivr.agents.models import AgentState, AgentStatus
from ivr.banking.models import BankingClient, Transaction


class TransactionAgent:
    def __init__(self, banking: BankingClient):
        self._banking = banking

    def start(self, intent_id: str, cif: str) -> AgentState:
        if intent_id == "transaction_details":
            txns = self._banking.get_recent_transactions(cif, count=5)
            return AgentState(status=AgentStatus.COMPLETED, prompt=_format_recent(txns))
        if intent_id == "statement_request":
            txns = self._banking.get_statement(cif, days=30)
            return AgentState(status=AgentStatus.COMPLETED, prompt=_format_statement(txns))
        raise ValueError(f"TransactionAgent doesn't own intent {intent_id!r}")

    def submit_input(self, text: str, *, confidence: float = 1.0) -> AgentState:
        raise RuntimeError("TransactionAgent never asks for input -- start() always completes immediately")


def _format_recent(transactions: list[Transaction]) -> str:
    if not transactions:
        return "I don't see any recent transactions on your account."
    # The disclosed count reflects what's actually being read out, not the
    # requested cap -- a customer with only 2 transactions should never hear
    # "your last 5 transactions" when there are only 2.
    noun = "transaction" if len(transactions) == 1 else "transactions"
    intro = f"I'll go through your last {len(transactions)} {noun}."
    return intro + " " + _list_transactions(transactions)


def _format_statement(transactions: list[Transaction]) -> str:
    if not transactions:
        return "I don't see any transactions in the last 30 days."
    intro = "I'll go through your statement for the last 30 days."
    return intro + " " + _list_transactions(transactions)


def _list_transactions(transactions: list[Transaction]) -> str:
    parts = []
    for t in transactions:
        verb = "credited" if t.amount >= 0 else "debited"
        parts.append(f"On {t.date}, {abs(t.amount):,.2f} rupees {verb} for {t.description}.")
    return " ".join(parts)
