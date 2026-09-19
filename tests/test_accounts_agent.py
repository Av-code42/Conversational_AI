import pytest

from ivr.agents.accounts_agent import AccountsAgent
from ivr.agents.models import AgentStatus
from ivr.banking.banking_dummy import DummyBankingClient


def test_balance_enquiry_completes_immediately():
    agent = AccountsAgent(DummyBankingClient())
    state = agent.start("balance_enquiry", "CIF1001")
    assert state.status == AgentStatus.COMPLETED
    assert "45,231.50" in state.prompt


def test_unowned_intent_raises():
    agent = AccountsAgent(DummyBankingClient())
    with pytest.raises(ValueError):
        agent.start("cheque_book_request", "CIF1001")


def test_submit_input_always_raises():
    agent = AccountsAgent(DummyBankingClient())
    agent.start("balance_enquiry", "CIF1001")
    with pytest.raises(RuntimeError):
        agent.submit_input("anything")
