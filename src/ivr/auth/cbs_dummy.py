"""In-memory dummy standing in for the Core Banking System, so the
authentication flow can be built and tested before real CBS integration
exists. Implements the CBSClient protocol from models.py.

Nothing here is how a real CBS would work: a real integration would call
external validate-MPIN / resolve-customer APIs and would never hand back or
store plaintext MPINs in application memory. This dummy does, purely to
simulate "the CBS says yes/no" without a real backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ivr.auth.models import AccountFlag, MpinCheckResult

MPIN_LOCK_THRESHOLD = 5  # cumulative wrong MPIN attempts (across calls) before CBS locks it


@dataclass
class CustomerRecord:
    cif: str
    name: str
    registered_mobiles: list[str]
    mpin: str
    account_number: str
    card_number: str
    account_flags: set[AccountFlag] = field(default_factory=set)
    mpin_fail_count: int = 0
    mpin_locked: bool = False

    @property
    def account_last6(self) -> str:
        return self.account_number[-6:]

    @property
    def card_last4(self) -> str:
        return self.card_number[-4:]


def _default_customers() -> list[CustomerRecord]:
    return [
        CustomerRecord(
            cif="CIF1001",
            name="Asha Rao",
            registered_mobiles=["9876500001"],
            mpin="4321",
            account_number="00123456789012",
            card_number="4111111111111111",
        ),
        CustomerRecord(
            cif="CIF1002",
            name="Vikram Shah",
            registered_mobiles=["9876500002"],
            mpin="1111",
            account_number="00223456789099",
            card_number="4111111111112222",
        ),
        CustomerRecord(
            cif="CIF1003",
            name="Meera Iyer",
            registered_mobiles=["9876500003"],
            mpin="9999",
            account_number="00323456789055",
            card_number="4111111111113333",
            account_flags={AccountFlag.WATCHLIST},
        ),
        CustomerRecord(
            cif="CIF1004",
            name="Rohit Verma",
            registered_mobiles=[],  # no registered mobile -- OTP undeliverable
            mpin="2468",
            account_number="00423456789011",
            card_number="4111111111114444",
        ),
        CustomerRecord(
            cif="CIF1005",
            name="Priya Nair",
            registered_mobiles=["9876500005"],
            mpin="1357",
            account_number="00523456789066",
            card_number="4111111111115555",
            mpin_fail_count=MPIN_LOCK_THRESHOLD,
            mpin_locked=True,
        ),
    ]


class DummyCBSClient:
    def __init__(self, customers: list[CustomerRecord] | None = None):
        self._by_cif = {c.cif: c for c in (customers if customers is not None else _default_customers())}

    def resolve_cif_by_mobile(self, ani: str) -> str | None:
        for customer in self._by_cif.values():
            if ani in customer.registered_mobiles:
                return customer.cif
        return None

    def resolve_cif_by_identity(self, account_last6: str, card_last4: str) -> str | None:
        for customer in self._by_cif.values():
            if customer.account_last6 == account_last6 and customer.card_last4 == card_last4:
                return customer.cif
        return None

    def get_registered_mobile(self, cif: str) -> str | None:
        customer = self._by_cif.get(cif)
        if not customer or not customer.registered_mobiles:
            return None
        return customer.registered_mobiles[0]

    def get_account_flags(self, cif: str) -> set[AccountFlag]:
        customer = self._by_cif.get(cif)
        return set(customer.account_flags) if customer else set()

    def validate_mpin(self, cif: str, mpin: str) -> MpinCheckResult:
        customer = self._by_cif[cif]
        if customer.mpin_locked:
            return MpinCheckResult.LOCKED
        if mpin == customer.mpin:
            customer.mpin_fail_count = 0
            return MpinCheckResult.CORRECT
        customer.mpin_fail_count += 1
        if customer.mpin_fail_count >= MPIN_LOCK_THRESHOLD:
            customer.mpin_locked = True
            return MpinCheckResult.LOCKED
        return MpinCheckResult.INCORRECT

    # Test/debug helper -- not part of the CBSClient protocol.
    def get_customer(self, cif: str) -> CustomerRecord:
        return self._by_cif[cif]
