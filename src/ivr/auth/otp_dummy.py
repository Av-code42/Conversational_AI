"""In-memory dummy standing in for the OTP gateway. Implements the
OTPGateway protocol from models.py.

Simulates "SMS delivery" by just recording the generated code -- no real
message goes anywhere. Tests read the code back via `last_code_for` (a
test-only helper, not part of the OTPGateway protocol) to simulate the
caller speaking the code they received.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

from ivr.auth.models import OTPCheckResult

DEFAULT_TTL_SECONDS = 180  # authentication-flow.md SS7: 3-minute validity window
DEFAULT_CODE_LENGTH = 6


@dataclass
class _IssuedOTP:
    mobile: str
    code: str
    issued_at: float
    seq: int
    consumed: bool = False


class DummyOTPGateway:
    def __init__(self, *, ttl_seconds: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._issued: dict[str, _IssuedOTP] = {}
        self._next_seq = 0  # explicit ordering -- issued_at alone ties when the
        # clock (esp. a test FakeClock) doesn't advance between two sends.

    def send_otp(self, mobile: str) -> str:
        reference = str(uuid.uuid4())
        code = f"{secrets.randbelow(10 ** DEFAULT_CODE_LENGTH):0{DEFAULT_CODE_LENGTH}d}"
        self._issued[reference] = _IssuedOTP(
            mobile=mobile, code=code, issued_at=self._clock(), seq=self._next_seq
        )
        self._next_seq += 1
        return reference

    def validate_otp(self, reference: str, entered_code: str) -> OTPCheckResult:
        issued = self._issued.get(reference)
        if issued is None or issued.consumed:
            return OTPCheckResult.NOT_FOUND
        if self._clock() - issued.issued_at > self._ttl_seconds:
            return OTPCheckResult.EXPIRED
        if entered_code != issued.code:
            return OTPCheckResult.INCORRECT
        issued.consumed = True
        return OTPCheckResult.CORRECT

    # Test/debug helpers -- not part of the OTPGateway protocol. A real
    # gateway would never expose the plaintext code to the caller of this
    # interface; these only exist so tests can simulate "the caller reads
    # back the code they received by SMS" without reaching into AuthFlow's
    # private state to find out which reference is currently active.
    def last_code_for(self, reference: str) -> str:
        return self._issued[reference].code

    def last_code_sent_to(self, mobile: str) -> str:
        candidates = [i for i in self._issued.values() if i.mobile == mobile]
        if not candidates:
            raise LookupError(f"no OTP has been sent to {mobile!r}")
        return max(candidates, key=lambda i: i.seq).code
