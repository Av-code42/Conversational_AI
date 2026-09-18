"""Shared types for the authentication flow.

Mirrors the terminology in docs/design/authentication-flow.md SS3 and the
tiers/factors defined in SS4-SS5. CBSClient and OTPGateway are Protocols so
AuthFlow (flow.py) never depends on whether it's talking to the dummy
in-memory implementations (cbs_dummy.py, otp_dummy.py) or a real Core
Banking System / OTP gateway integration later.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class Tier(str, Enum):
    TIER_0 = "TIER_0"
    TIER_1 = "TIER_1"
    TIER_2 = "TIER_2"


class CLIMatchResult(str, Enum):
    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    UNAVAILABLE = "UNAVAILABLE"


class MpinCheckResult(str, Enum):
    """CORRECT/INCORRECT/LOCKED. Lockout thresholds are CBS's responsibility,
    not the IVR's (authentication-flow.md SS7) -- AuthFlow only surfaces
    whatever status CBS returns."""

    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    LOCKED = "LOCKED"


class OTPCheckResult(str, Enum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    EXPIRED = "EXPIRED"
    NOT_FOUND = "NOT_FOUND"


class AccountFlag(str, Enum):
    DORMANT = "dormant"
    BLOCKED = "blocked"
    WATCHLIST = "watchlist"
    DECEASED = "deceased"


class AuthStatus(str, Enum):
    """Caller-facing status: what AuthFlow needs next, or the terminal
    outcome. Deliberately coarser than AuthFlow's internal stage tracking --
    several internal stages (e.g. tier-1 MPIN fallback vs tier-2 MPIN
    primary) both surface as NEEDS_MPIN externally."""

    NO_AUTH_REQUIRED = "no_auth_required"
    NEEDS_IDENTIFICATION = "needs_identification"
    NEEDS_MPIN = "needs_mpin"
    NEEDS_OTP = "needs_otp"
    NEEDS_REPEAT = "needs_repeat"  # low ASR confidence -- reprompt, not a counted attempt
    AUTHENTICATED = "authenticated"
    ESCALATED = "escalated"


class EscalationReason(str, Enum):
    """Auth-flow-owned escalation triggers from authentication-flow.md SS8.
    Coordinator-level triggers (explicit "agent" request, intent
    misclassification) belong to the agent-architecture.md Coordinator, not
    here."""

    CANNOT_IDENTIFY_CALLER = "cannot_identify_caller"
    NO_REGISTERED_MOBILE = "no_registered_mobile"
    OTP_FAILED = "otp_failed"
    MPIN_FAILED = "mpin_failed"
    MPIN_LOCKED = "mpin_locked"
    ACCOUNT_FLAGGED = "account_flagged"
    LOW_CONFIDENCE_EXCEEDED = "low_confidence_exceeded"


class CBSClient(Protocol):
    """Core Banking System integration contract. cbs_dummy.DummyCBSClient
    implements this in-memory; a real integration would call the bank's
    actual CBS APIs."""

    def resolve_cif_by_mobile(self, ani: str) -> str | None:
        """Silent CLI match: ANI -> CIF, or None if no customer has this as
        a registered mobile."""
        ...

    def resolve_cif_by_identity(self, account_last6: str, card_last4: str) -> str | None:
        """CLI-mismatch identification path: both digits together -> CIF,
        or None if no match."""
        ...

    def get_registered_mobile(self, cif: str) -> str | None:
        """The mobile OTPs should be sent to for this CIF, or None if the
        customer has no registered mobile on file."""
        ...

    def get_account_flags(self, cif: str) -> set[AccountFlag]:
        ...

    def validate_mpin(self, cif: str, mpin: str) -> MpinCheckResult:
        ...


class OTPGateway(Protocol):
    """OTP delivery/validation contract. otp_dummy.DummyOTPGateway
    implements this in-memory; a real integration would call an SMS OTP
    gateway. Deliberately returns an opaque reference from send_otp rather
    than the code itself -- the IVR layer never holds the plaintext OTP,
    only the gateway does, same as a real gateway would work."""

    def send_otp(self, mobile: str) -> str:
        """Triggers delivery, returns an opaque reference for validate_otp.
        A resend calls this again and gets a new reference; the prior one
        is invalidated (authentication-flow.md SS7)."""
        ...

    def validate_otp(self, reference: str, entered_code: str) -> OTPCheckResult:
        ...
