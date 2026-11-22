"""Tests for scheduled transaction commands."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app

runner = CliRunner()


def _schedule(payee, sid, amount=-100_000):
    s = MagicMock()
    s.id = sid
    s.payee_name = payee
    s.amount = amount
    s.deleted = False
    s.date_next = "2026-11-01"
    s.account_name = "Checking"
    return s


@patch("ynab.ScheduledTransactionsApi")
@patch("ynab_tools.commands.scheduled.client")
def test_delete_removes_the_one_matching_schedule(mock_client, api):
    mock_client.list_scheduled_transactions.return_value = [
        _schedule("Verizon", "aaaa1111"),
        _schedule("Trupanion", "bbbb2222"),
    ]

    result = runner.invoke(app, ["scheduled", "delete", "verizon"])

    assert result.exit_code == 0
    api.return_value.delete_scheduled_transaction.assert_called_once()
    assert api.return_value.delete_scheduled_transaction.call_args.args[1] == "aaaa1111"


@patch("ynab.ScheduledTransactionsApi")
@patch("ynab_tools.commands.scheduled.client")
def test_delete_refuses_an_ambiguous_payee(mock_client, api):
    """Two loans share a payee name; guessing would delete the wrong one."""
    mock_client.list_scheduled_transactions.return_value = [
        _schedule("Online Payment to Auto Loan", "aaaa1111", -439_670),
        _schedule("Online Payment to Auto Loan", "bbbb2222", -646_140),
    ]

    result = runner.invoke(app, ["scheduled", "delete", "auto loan"])

    assert result.exit_code == 1
    assert "aaaa1111" in result.output and "bbbb2222" in result.output
    api.return_value.delete_scheduled_transaction.assert_not_called()


@patch("ynab.ScheduledTransactionsApi")
@patch("ynab_tools.commands.scheduled.client")
def test_delete_accepts_an_id_prefix(mock_client, api):
    mock_client.list_scheduled_transactions.return_value = [
        _schedule("Online Payment to Auto Loan", "aaaa1111", -439_670),
        _schedule("Online Payment to Auto Loan", "bbbb2222", -646_140),
    ]

    result = runner.invoke(app, ["scheduled", "delete", "bbbb"])

    assert result.exit_code == 0
    assert api.return_value.delete_scheduled_transaction.call_args.args[1] == "bbbb2222"


@patch("ynab_tools.commands.scheduled.client")
def test_delete_reports_no_match(mock_client):
    mock_client.list_scheduled_transactions.return_value = [_schedule("Verizon", "aaaa1111")]

    result = runner.invoke(app, ["scheduled", "delete", "netflix"])

    assert result.exit_code == 1
    assert "No scheduled transaction" in result.output
