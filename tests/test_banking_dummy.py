from ivr.banking.banking_dummy import DummyBankingClient


def test_get_balance_known_customer():
    banking = DummyBankingClient()
    assert banking.get_balance("CIF1001") == 45231.50


def test_get_balance_unknown_customer_defaults_to_zero():
    banking = DummyBankingClient()
    assert banking.get_balance("NO_SUCH_CIF") == 0.0


def test_get_recent_transactions_respects_count():
    banking = DummyBankingClient()
    assert len(banking.get_recent_transactions("CIF1001", count=2)) == 2
    assert len(banking.get_recent_transactions("CIF1001", count=100)) <= 5


def test_get_recent_transactions_unknown_customer_returns_empty():
    banking = DummyBankingClient()
    assert banking.get_recent_transactions("NO_SUCH_CIF") == []


def test_get_statement_returns_transactions():
    banking = DummyBankingClient()
    statement = banking.get_statement("CIF1001", days=30)
    assert len(statement) > 0


def test_submit_service_request_issues_unique_tickets():
    banking = DummyBankingClient()
    ticket1 = banking.submit_service_request("CIF1001", "cheque_book_request", {})
    ticket2 = banking.submit_service_request("CIF1001", "kyc_update", {})
    assert ticket1 != ticket2


def test_list_service_requests_filters_by_cif():
    banking = DummyBankingClient()
    banking.submit_service_request("CIF1001", "kyc_update", {})
    banking.submit_service_request("CIF1002", "kyc_update", {})
    assert len(banking.list_service_requests("CIF1001")) == 1
    assert len(banking.list_service_requests("CIF1002")) == 1
    assert len(banking.list_service_requests("CIF1003")) == 0


def test_custom_data_overrides_defaults():
    banking = DummyBankingClient(balances={"X1": 999.0}, transactions={})
    assert banking.get_balance("X1") == 999.0
    assert banking.get_balance("CIF1001") == 0.0  # default fixture data not present
