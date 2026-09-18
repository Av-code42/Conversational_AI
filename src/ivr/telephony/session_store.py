"""Per-call state for the telephony webhook layer.

Twilio's /voice/gather webhook only tells us what the caller said, not what
we asked for -- so, same as scripts/call_auth_cli.py's `pending` variable,
we track which kind of input CoordinatorFlow is currently waiting for.

Also tracks the identification sub-flow: AuthFlow.submit_identification()
wants account-last6 and card-last4 together in one call
(authentication-flow.md SS6.2: "both, together"), but a phone call is one
question per turn -- so this layer asks for them as two separate prompts
and holds the first answer until the second arrives, then calls
CoordinatorFlow.submit_identification() once with both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ivr.coordinator.flow import CoordinatorFlow, CoordinatorStatus


@dataclass
class CallSession:
    call_sid: str
    coordinator: CoordinatorFlow
    pending_status: CoordinatorStatus = CoordinatorStatus.NEEDS_INTENT
    identification_step: Literal["account", "card"] | None = None
    pending_account_last6: str | None = None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CallSession] = {}

    def create(self, call_sid: str, coordinator: CoordinatorFlow) -> CallSession:
        session = CallSession(call_sid=call_sid, coordinator=coordinator)
        self._sessions[call_sid] = session
        return session

    def get(self, call_sid: str) -> CallSession | None:
        return self._sessions.get(call_sid)

    def end(self, call_sid: str) -> None:
        self._sessions.pop(call_sid, None)
