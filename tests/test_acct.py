"""Tests for account commands."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.config import Statement, load_profile

runner = CliRunner()


def _account(aid, name, balance):
    a = MagicMock()
    a.id = aid
    a.name = name
    a.closed = False
    a.balance = balance
    a.cleared_balance = balance
    a.uncleared_balance = 0
    return a


def _tx(tid, account_id, amount, tx_date):
    t = MagicMock()
    t.id = tid
    t.account_id = account_id
    t.account_name = account_id
    t.amount = amount
    t.var_date = date.fromisoformat(tx_date)
    t.payee_name = "Some Payee"
    t.import_id = f"imp-{tid}"
    t.matched_transaction_id = None
    t.cleared = "cleared"
    t.transfer_account_id = None
    t.deleted = False
    return t


def _profile(monkeypatch, tmp_path, data):
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(data))
    monkeypatch.setattr("ynab_tools.config.PROFILE_FILE", profile_file)
    monkeypatch.setattr("ynab_tools.config.CONFIG_DIR", tmp_path)


@patch("ynab_tools.commands.acct.client")
def test_verify_save_records_the_statement_and_keeps_other_keys(mock_client, monkeypatch, tmp_path):
    _profile(monkeypatch, tmp_path, {"card_last4": {"1234": "Some Card"}, "goal_group": "Goals"})
    mock_client.list_accounts.return_value = [_account("checking", "Checking", 100_000)]
    mock_client.list_transactions_by_account.return_value = []

    result = runner.invoke(
        app, ["acct", "verify", "Checking", "--balance", "100", "--date", "2026-10-01", "--save", "--json"]
    )

    assert result.exit_code == 0, result.output
    profile = load_profile()
    assert profile.statements == {"Checking": Statement(balance=100_000, on=date(2026, 10, 1))}
    assert profile.card_last4 == {"1234": "Some Card"}
    assert profile.goal_group == "Goals"
    assert json.loads(result.stdout)["bank"] == 100_000


@patch("ynab_tools.commands.acct.client")
def test_verify_with_date_compares_as_of_that_date(mock_client, monkeypatch, tmp_path):
    """Spending after the statement date is not a gap against that statement."""
    _profile(monkeypatch, tmp_path, {})
    mock_client.list_accounts.return_value = [_account("checking", "Checking", 70_000)]
    mock_client.list_transactions_by_account.return_value = [
        _tx("after", "checking", -30_000, "2026-10-03"),
    ]

    result = runner.invoke(
        app, ["acct", "verify", "Checking", "--balance", "100", "--date", "2026-10-01", "--json"]
    )

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["unexplained"] == -30_000
    assert out["as_of"] == {"date": "2026-10-01", "unexplained": 0}
    assert load_profile().statements == {}


def _two_freedom_cards():
    return [
        _account("card-1", "🗽 Chase Freedom", -100_000),
        _account("card-2", "♾️ Chase Freedom Unlimited", -200_000),
    ]


@patch("ynab_tools.commands.acct.client")
def test_verify_exact_name_wins_over_substring(mock_client, monkeypatch, tmp_path):
    """A name that is also a prefix of another account picks the exact one."""
    _profile(monkeypatch, tmp_path, {})
    mock_client.list_accounts.return_value = _two_freedom_cards()
    mock_client.list_transactions_by_account.return_value = []

    result = runner.invoke(app, ["acct", "verify", "chase freedom", "--balance", "-100", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["account"] == "🗽 Chase Freedom"


@patch("ynab_tools.commands.acct.client")
def test_verify_substring_matching_two_accounts_still_refuses(mock_client, monkeypatch, tmp_path):
    _profile(monkeypatch, tmp_path, {})
    mock_client.list_accounts.return_value = _two_freedom_cards()

    result = runner.invoke(app, ["acct", "verify", "Freedom", "--balance", "-100"])

    assert result.exit_code == 1
    assert "matches 2 accounts" in result.output
