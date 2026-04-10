"""Tests for CLI commands (smoke tests with mocked API)."""

from unittest.mock import MagicMock, patch
from uuid import UUID

from typer.testing import CliRunner

from ynab_tools.cli import app

runner = CliRunner()


def _mock_budget(name="Test Budget", bid="fc975e18-93e4-4955-898d-8bb201bf883a"):
    b = MagicMock()
    b.id = UUID(bid)
    b.name = name
    b.to_dict.return_value = {"id": bid, "name": name}
    return b


def _mock_account(name="Checking", balance=100000, cleared=80000, uncleared=20000):
    a = MagicMock()
    a.name = name
    a.type = "checking"
    a.closed = False
    a.balance = balance
    a.cleared_balance = cleared
    a.uncleared_balance = uncleared
    a.to_dict.return_value = {"name": name, "balance": balance}
    return a


def _mock_transaction(
    payee="Amazon",
    amount=-45990,
    approved=False,
    category="Shopping",
    account="Checking",
    tid="b3cc2552-e3a3-4c1c-a7c0-127fbe8509d0",
):
    t = MagicMock()
    t.id = UUID(tid)
    t.var_date = "2026-03-20"
    t.amount = amount
    t.payee_name = payee
    t.category_name = category
    t.account_name = account
    t.approved = approved
    t.deleted = False
    t.memo = None
    t.flag_color = None
    t.import_payee_name = None
    t.transfer_account_id = None
    t.cleared = MagicMock(value="cleared")
    t.to_dict.return_value = {
        "id": tid,
        "date": "2026-03-20",
        "amount": amount,
        "payee_name": payee,
        "category_name": category,
        "approved": approved,
    }
    return t


@patch("ynab_tools.commands.budget.client")
@patch("ynab_tools.commands.budget.load_config")
def test_budget_list(mock_config, mock_client):
    mock_config.return_value = MagicMock(default_budget_id="fc975e18-93e4-4955-898d-8bb201bf883a")
    mock_client.list_budgets.return_value = [_mock_budget()]

    result = runner.invoke(app, ["budget", "list"])
    assert result.exit_code == 0
    assert "Test Budget" in result.output


@patch("ynab_tools.commands.budget.client")
@patch("ynab_tools.commands.budget.load_config")
def test_budget_list_json(mock_config, mock_client):
    mock_config.return_value = MagicMock(default_budget_id="")
    mock_client.list_budgets.return_value = [_mock_budget()]

    result = runner.invoke(app, ["budget", "list", "--json"])
    assert result.exit_code == 0
    assert "fc975e18" in result.output


@patch("ynab_tools.commands.acct.client")
def test_acct_list(mock_client):
    mock_client.list_accounts.return_value = [_mock_account()]

    result = runner.invoke(app, ["acct", "list"])
    assert result.exit_code == 0
    assert "Checking" in result.output


@patch("ynab_tools.commands.acct.client")
def test_acct_list_json(mock_client):
    mock_client.list_accounts.return_value = [_mock_account()]

    result = runner.invoke(app, ["acct", "list", "--json"])
    assert result.exit_code == 0
    assert "Checking" in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_list(mock_client):
    mock_client.list_transactions.return_value = [_mock_transaction()]

    result = runner.invoke(app, ["tx", "list", "--days", "7"])
    assert result.exit_code == 0
    assert "Amazon" in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_list_json(mock_client):
    mock_client.list_transactions.return_value = [_mock_transaction()]

    result = runner.invoke(app, ["tx", "list", "--days", "7", "--json"])
    assert result.exit_code == 0
    assert "Amazon" in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_list_unapproved(mock_client):
    mock_client.list_transactions.return_value = [_mock_transaction(approved=False)]

    result = runner.invoke(app, ["tx", "list", "-u"])
    assert result.exit_code == 0
    assert "○" in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_list_payee_filter(mock_client):
    mock_client.list_transactions.return_value = [
        _mock_transaction(payee="Amazon"),
        _mock_transaction(payee="Starbucks", tid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
    ]

    result = runner.invoke(app, ["tx", "list", "--payee", "star"])
    assert result.exit_code == 0
    assert "Starbucks" in result.output
    assert "Amazon" not in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_show(mock_client):
    mock_client.get_transaction.return_value = _mock_transaction()

    result = runner.invoke(app, ["tx", "show", "b3cc2552-e3a3-4c1c-a7c0-127fbe8509d0"])
    assert result.exit_code == 0
    assert "Amazon" in result.output
    assert "-$45.99" in result.output


@patch("ynab_tools.commands.tx.client")
def test_tx_approve(mock_client):
    result = runner.invoke(app, ["tx", "approve", "b3cc2552-e3a3-4c1c-a7c0-127fbe8509d0"])
    assert result.exit_code == 0
    assert "✓" in result.output
    mock_client.approve_transaction.assert_called_once()


def test_status_no_key(monkeypatch):
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    monkeypatch.setattr("ynab_tools.config.ENV_FILE", MagicMock(exists=MagicMock(return_value=False)))
    monkeypatch.setattr("ynab_tools.config.CONFIG_FILE", MagicMock(exists=MagicMock(return_value=False)))

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "YNAB_API_KEY" in result.output
