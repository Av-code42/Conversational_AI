import pytest

from ivr.agents.accounts_agent import AccountsAgent
from ivr.agents.registry import AgentRegistry
from ivr.agents.service_agent import ServiceAgent
from ivr.agents.transaction_agent import TransactionAgent
from ivr.banking.banking_dummy import DummyBankingClient


class FakeClassifier:
    def classify(self, text):
        return None


@pytest.fixture
def registry():
    return AgentRegistry(DummyBankingClient(), FakeClassifier())


def test_creates_accounts_agent(registry):
    assert isinstance(registry.create("accounts_agent"), AccountsAgent)


def test_creates_transaction_agent(registry):
    assert isinstance(registry.create("transaction_agent"), TransactionAgent)


def test_creates_service_agent(registry):
    assert isinstance(registry.create("service_agent"), ServiceAgent)


def test_unknown_agent_id_raises(registry):
    with pytest.raises(ValueError):
        registry.create("not_a_real_agent")


def test_each_call_returns_a_fresh_instance(registry):
    # State must never leak across intents/calls -- see registry.py's docstring.
    first = registry.create("service_agent")
    second = registry.create("service_agent")
    assert first is not second

    first.start("change_of_address", "CIF1001")  # puts `first` mid slot-filling
    # `second` must be unaffected by `first`'s in-progress state
    with pytest.raises(RuntimeError):
        second.submit_input("should have nothing pending")
