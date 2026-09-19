"""Shared types for banking tools (the boxes under each domain agent in
docs/design/agent-architecture.md's diagram) -- balance, transaction
history, and service-request submission. Separate from ivr.auth.models on
purpose: AuthFlow's CBSClient protocol is scoped to authentication concerns
only (resolve customer, validate MPIN); these are a different integration
surface a real deployment would likely call through a different set of CBS
APIs entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Transaction:
    date: str  # YYYY-MM-DD
    description: str
    amount: float  # positive = credit, negative = debit


@dataclass
class ServiceRequest:
    ticket_id: str
    cif: str
    kind: str  # matches an intent_id, e.g. "change_of_address"
    details: dict


class BankingClient(Protocol):
    """Banking tools contract. banking_dummy.DummyBankingClient implements
    this in-memory; a real integration would call the bank's actual
    account/transaction/service-request APIs."""

    def get_balance(self, cif: str) -> float: ...

    def get_recent_transactions(self, cif: str, count: int) -> list[Transaction]: ...

    def get_statement(self, cif: str, days: int) -> list[Transaction]: ...

    def submit_service_request(self, cif: str, kind: str, details: dict) -> str:
        """Returns a ticket id."""
        ...
