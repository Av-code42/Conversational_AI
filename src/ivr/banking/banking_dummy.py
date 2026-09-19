"""In-memory dummy standing in for the banking tools -- balance, transaction
history, service-request submission. Implements the BankingClient protocol
from models.py. Keyed by the same CIFs as auth/cbs_dummy.py's customers, so
a call authenticated as e.g. Asha Rao (CIF1001) sees her balance/history.
"""

from __future__ import annotations

from ivr.banking.models import ServiceRequest, Transaction


def _default_transactions() -> dict[str, list[Transaction]]:
    return {
        "CIF1001": [
            Transaction("2026-09-14", "UPI/Swiggy", -450.00),
            Transaction("2026-09-12", "Salary Credit", 85000.00),
            Transaction("2026-09-10", "ATM Withdrawal", -5000.00),
            Transaction("2026-09-05", "Electricity Bill", -1200.00),
            Transaction("2026-08-30", "UPI/Amazon", -2340.00),
        ],
        "CIF1002": [
            Transaction("2026-09-15", "UPI/Zomato", -680.00),
            Transaction("2026-09-11", "Salary Credit", 145000.00),
            Transaction("2026-09-08", "Mutual Fund SIP", -20000.00),
            Transaction("2026-09-02", "Credit Card Payment", -18500.00),
        ],
        "CIF1003": [
            Transaction("2026-09-13", "ATM Withdrawal", -2000.00),
            Transaction("2026-09-01", "Pension Credit", 32000.00),
        ],
        "CIF1004": [
            Transaction("2026-09-09", "UPI/Uber", -320.00),
            Transaction("2026-09-01", "Salary Credit", 62000.00),
        ],
        "CIF1005": [
            Transaction("2026-09-10", "UPI/Flipkart", -1500.00),
            Transaction("2026-09-01", "Salary Credit", 58000.00),
        ],
    }


def _default_balances() -> dict[str, float]:
    return {
        "CIF1001": 45231.50,
        "CIF1002": 128900.00,
        "CIF1003": 3200.75,
        "CIF1004": 89000.00,
        "CIF1005": 15750.25,
    }


class DummyBankingClient:
    def __init__(
        self,
        balances: dict[str, float] | None = None,
        transactions: dict[str, list[Transaction]] | None = None,
    ):
        self._balances = balances if balances is not None else _default_balances()
        self._transactions = transactions if transactions is not None else _default_transactions()
        self._service_requests: list[ServiceRequest] = []

    def get_balance(self, cif: str) -> float:
        return self._balances.get(cif, 0.0)

    def get_recent_transactions(self, cif: str, count: int = 5) -> list[Transaction]:
        return self._transactions.get(cif, [])[:count]

    def get_statement(self, cif: str, days: int = 30) -> list[Transaction]:
        # Dummy data has no real dates to filter by `days` against -- all
        # seeded transactions are treated as within the requested window.
        return list(self._transactions.get(cif, []))

    def submit_service_request(self, cif: str, kind: str, details: dict) -> str:
        ticket_id = f"SR{len(self._service_requests) + 1:05d}"
        self._service_requests.append(ServiceRequest(ticket_id, cif, kind, details))
        return ticket_id

    # Test/debug helper -- not part of the BankingClient protocol.
    def list_service_requests(self, cif: str) -> list[ServiceRequest]:
        return [r for r in self._service_requests if r.cif == cif]
