import pytest

from ivr.agents.models import AgentStatus
from ivr.agents.transaction_agent import TransactionAgent
from ivr.banking.banking_dummy import DummyBankingClient


def test_transaction_details_completes_immediately():
    agent = TransactionAgent(DummyBankingClient())
    state = agent.start("transaction_details", "CIF1001")
    assert state.status == AgentStatus.COMPLETED
    assert "last 5 transactions" in state.prompt


def test_statement_request_completes_immediately():
    agent = TransactionAgent(DummyBankingClient())
    state = agent.start("statement_request", "CIF1001")
    assert state.status == AgentStatus.COMPLETED
    assert "last 30 days" in state.prompt


def test_no_transactions_gives_a_clear_response_not_an_empty_one():
    agent = TransactionAgent(DummyBankingClient(transactions={}))
    state = agent.start("transaction_details", "CIF1001")
    assert "don't see any" in state.prompt


def test_disclosed_count_matches_actual_count_not_the_requested_cap():
    # A customer with fewer than 5 transactions must never hear "your last
    # 5 transactions" when there are only, say, 2.
    from ivr.banking.models import Transaction

    banking = DummyBankingClient(transactions={"CIF1001": [Transaction("2026-09-01", "UPI/Test", -100.0)]})
    agent = TransactionAgent(banking)
    state = agent.start("transaction_details", "CIF1001")
    assert "last 1 transaction." in state.prompt
    assert "last 5" not in state.prompt


def test_unowned_intent_raises():
    agent = TransactionAgent(DummyBankingClient())
    with pytest.raises(ValueError):
        agent.start("balance_enquiry", "CIF1001")


def test_submit_input_always_raises():
    agent = TransactionAgent(DummyBankingClient())
    agent.start("transaction_details", "CIF1001")
    with pytest.raises(RuntimeError):
        agent.submit_input("anything")
