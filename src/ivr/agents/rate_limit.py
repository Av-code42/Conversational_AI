"""Per-call rate limiting for state-changing tool calls (guardrail against
a call submitting an unreasonable number of service requests -- accidental
or abusive). Owned by AgentRegistry (registry.py), which is constructed
fresh per call precisely so this counter resets for a new call but persists
across the multiple intents one call can route through via the "anything
else?" loop.
"""

from __future__ import annotations

DEFAULT_MAX_SERVICE_REQUESTS_PER_CALL = 3


class ServiceRequestLimiter:
    def __init__(self, max_per_call: int = DEFAULT_MAX_SERVICE_REQUESTS_PER_CALL):
        self._max_per_call = max_per_call
        self._count = 0

    def can_submit(self) -> bool:
        return self._count < self._max_per_call

    def record_submission(self) -> None:
        self._count += 1
