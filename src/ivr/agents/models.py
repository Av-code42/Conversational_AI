"""The domain agent contract from docs/design/agent-architecture.md SS4:
receives an intent + session context, owns its own tool execution and any
slot-filling, and returns one of COMPLETED / NEED_HANDOFF / ESCALATE to the
Coordinator. All three concrete agents (accounts_agent.py, transaction_agent.py,
service_agent.py) implement this same protocol so CoordinatorFlow/the
telephony and CLI layers never need agent-specific logic.

A fresh agent instance is created per intent occurrence (see
agents/registry.py) -- never reused across intents or calls, so an agent
never needs to reset its own state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"  # mid slot-filling turn
    NEED_HANDOFF = "need_handoff"  # caller asked for something else mid-flow
    ESCALATE = "escalate"


@dataclass
class AgentState:
    status: AgentStatus
    prompt: str | None = None  # spoken response (COMPLETED) or slot question (NEEDS_INPUT)
    new_intent_id: str | None = None  # set when NEED_HANDOFF
    escalation_reason: str | None = None  # set when ESCALATE


class DomainAgent(Protocol):
    def start(self, intent_id: str, cif: str) -> AgentState:
        """Begin handling `intent_id` for the authenticated caller `cif`.
        For a zero-slot tool this executes immediately and returns
        COMPLETED; for one that needs more information it returns
        NEEDS_INPUT with the first slot question."""
        ...

    def submit_input(self, text: str, *, confidence: float = 1.0) -> AgentState:
        """Continues a NEEDS_INPUT turn. Only valid to call after start()
        (or a prior submit_input()) returned NEEDS_INPUT."""
        ...
