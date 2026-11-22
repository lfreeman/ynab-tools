"""Tests for the daily routine."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app

runner = CliRunner()

THIS_MONTH = date.today().replace(day=1)


def _tx(tid, amount, payee, account="checking", category_id="mortgage", import_id=None,
        cleared="cleared", transfer_account_id=None, tx_date=THIS_MONTH):
    t = MagicMock()
    t.id = tid
    t.amount = amount
    t.payee_name = payee
    t.account_id = account
    t.account_name = account
    t.category_id = category_id
    t.import_id = import_id
    t.matched_transaction_id = None
    t.cleared = cleared
    t.transfer_account_id = transfer_account_id
    t.transfer_transaction_id = None
    t.var_date = tx_date
    t.approved = True
    t.deleted = False
    return t


def _category(cid, name, balance):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.balance = balance
    c.hidden = False
    c.deleted = False
    return c


def _group(name, categories):
    g = MagicMock()
    g.name = name
    g.categories = categories
    return g


def _account(aid, name, acct_type, balance):
    a = MagicMock()
    a.id = aid
    a.name = name
    a.type = acct_type
    a.balance = balance
    a.closed = False
    a.direct_import_linked = False
    return a


def _setup(mock_client, txs, categories, accounts=None):
    month = MagicMock()
    month.to_be_budgeted = 0
    month.age_of_money = 40
    mock_client.get_month.return_value = month
    mock_client.list_accounts.return_value = accounts or [_account("checking", "Checking", "checking", 1)]
    mock_client.list_categories.return_value = categories
    mock_client.list_transactions.return_value = txs


@patch("ynab_tools.commands.routine.client")
def test_routine_flags_ghosts_and_names_them_in_overspending(mock_client):
    """A ghost doubles a bill; assigning money to cover it would be wrong."""
    _setup(
        mock_client,
        txs=[
            _tx("ghost", -2_000_000, "Mortgage Co", cleared="uncleared"),
            _tx("real", -2_100_000, "Mortgage Co", import_id="imp-1"),
        ],
        categories=[_group("Housing", [_category("mortgage", "Mortgage", -2_000_000)])],
    )

    result = runner.invoke(app, ["routine"])

    assert "1 ghost" in result.output
    assert "scheduled prune" in result.output
    assert "$2,000.00 of it is ghosts" in result.output


@patch("ynab_tools.commands.routine.client")
def test_routine_describes_card_mismatch_in_plain_terms(mock_client):
    _setup(
        mock_client,
        txs=[],
        categories=[_group("Credit Card Payments", [_category("boa-cat", "Bank of America", -75_000)])],
        accounts=[_account("boa", "Bank of America", "creditCard", -25_000)],
    )

    result = runner.invoke(app, ["routine"])
    output = " ".join(result.output.split())  # Rich wraps at the test terminal's width

    assert "owe $25.00, payment category has -$75.00 — $100.00 short" in output


@patch("ynab_tools.commands.routine.client")
def test_routine_names_a_linked_account_that_stopped_importing(mock_client):
    """A dropped feed has no error flag; only the missing imports show it."""
    card = _account("card", "Store Card", "creditCard", -500_000)
    card.direct_import_linked = True
    card.direct_import_in_error = False
    card.on_budget = True
    old = _tx("old", -20_000, "Store", account="card", import_id="YNAB:-20000:old:1",
              tx_date=date.today() - timedelta(days=45))
    _setup(mock_client, [old], [], accounts=[card])

    result = runner.invoke(app, ["routine"])
    out = " ".join(result.output.split())

    assert "Store Card" in out
    assert "not imported" in out
