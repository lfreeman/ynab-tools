"""Tests for transaction analysis helpers."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.commands.tx import (
    _apply_rules,
    _find_ghosts,
    _find_misrouted_payments,
    _find_unconfirmed_payments,
    _ghost_schedules,
    _payee_tokens,
)

runner = CliRunner()


def _tx(account_id, amount, tx_date, payee=None, import_id=None, cleared="cleared",
        transfer_account_id=None, matched=None, tx_id=None):
    t = MagicMock()
    t.id = tx_id or f"{account_id}-{amount}-{tx_date}"
    t.account_id = account_id
    t.account_name = account_id
    t.amount = amount
    t.var_date = date.fromisoformat(tx_date)
    t.payee_name = payee
    t.import_id = import_id
    t.cleared = cleared
    t.transfer_account_id = transfer_account_id
    t.matched_transaction_id = matched
    t.deleted = False
    return t


def _card(account_id, name):
    a = MagicMock()
    a.id = account_id
    a.name = name
    a.type = "creditCard"
    a.closed = False
    return a


# ── Misrouted card payments ──────────────────────────────────────────────────


def test_detects_payment_linked_to_the_wrong_card():
    """A payment credited to a card whose own feed never reported it."""
    accounts = [_card("freedom", "Chase Freedom"), _card("unlimited", "Freedom Unlimited")]
    txs = [
        # Paid out of checking, YNAB guessed "Chase Freedom".
        _tx("checking", -3_173_570, "2026-05-15", import_id="imp-1",
            transfer_account_id="freedom"),
        # But Freedom Unlimited's own feed imported the money arriving.
        _tx("unlimited", 3_173_570, "2026-05-15", payee="Automatic Payment",
            import_id="imp-2"),
    ]

    issues = _find_misrouted_payments(txs, accounts)

    assert len(issues) == 1
    assert issues[0]["linked_to"] == "Chase Freedom"
    assert issues[0]["should_be"] == "unlimited"


def test_ignores_payment_linked_to_the_right_card():
    accounts = [_card("freedom", "Chase Freedom"), _card("unlimited", "Freedom Unlimited")]
    txs = [
        _tx("checking", -500_000, "2026-05-15", import_id="imp-1",
            transfer_account_id="unlimited"),
        # The receiving card's import is already matched into the transfer.
        _tx("unlimited", 500_000, "2026-05-15", import_id="imp-2",
            transfer_account_id="checking"),
    ]

    assert _find_misrouted_payments(txs, accounts) == []


def test_ignores_unrelated_amounts_on_another_card():
    accounts = [_card("freedom", "Chase Freedom"), _card("unlimited", "Freedom Unlimited")]
    txs = [
        _tx("checking", -500_000, "2026-05-15", import_id="imp-1",
            transfer_account_id="freedom"),
        _tx("unlimited", 999_000, "2026-05-15", payee="Something Else", import_id="imp-2"),
    ]

    assert _find_misrouted_payments(txs, accounts) == []


def _transfer_pair(paid_date, amount, card_cleared="uncleared", card_import=None):
    """A checking outflow (bank-imported) and the card-side inflow YNAB created."""
    out = _tx("checking", -amount, paid_date, import_id="imp-out",
              transfer_account_id="card", tx_id="out")
    inn = _tx("card", amount, paid_date, cleared=card_cleared, import_id=card_import,
              transfer_account_id="checking", tx_id="in")
    out.transfer_transaction_id, inn.transfer_transaction_id = "in", "out"
    return [out, inn]


def test_flags_payment_the_receiving_card_never_confirmed():
    issues = _find_unconfirmed_payments(_transfer_pair("2026-07-16", 782_680), today=date(2026, 10, 4))

    assert len(issues) == 1
    assert issues[0]["account_id"] == "card"
    assert issues[0]["amount"] == 782_680


def test_recent_payment_is_given_time_to_post():
    """Card feeds lag the bank by a few days; that is not a problem yet."""
    issues = _find_unconfirmed_payments(_transfer_pair("2026-10-01", 782_680), today=date(2026, 10, 4))

    assert issues == []


def test_confirmed_card_side_is_not_flagged():
    txs = _transfer_pair("2026-07-16", 782_680, card_cleared="cleared", card_import="imp-in")

    assert _find_unconfirmed_payments(txs, today=date(2026, 10, 4)) == []


# ── Ghost transactions ───────────────────────────────────────────────────────


def test_detects_ghost_beside_a_real_charge_of_a_different_amount():
    """The amounts differ — which is exactly why YNAB failed to auto-match."""
    txs = [
        _tx("checking", -600_000, "2026-10-02", payee="O&R Utilities", cleared="uncleared"),
        _tx("checking", -351_180, "2026-10-01", payee="O&R Utilities", import_id="imp-1"),
    ]

    ghosts, unconfirmed = _find_ghosts(txs)

    assert len(ghosts) == 1
    assert unconfirmed == []
    assert ghosts[0][0].amount == -600_000


def test_entry_with_no_real_charge_is_unconfirmed_not_a_ghost():
    """A payment that genuinely stopped must never be auto-deleted."""
    txs = [
        _tx("checking", -646_140, "2026-10-01", payee="Online Payment to Auto Loan",
            cleared="uncleared"),
    ]

    ghosts, unconfirmed = _find_ghosts(txs)

    assert ghosts == []
    assert len(unconfirmed) == 1


def test_a_matched_entry_is_not_a_ghost():
    """Matching clears import_id, so matched entries must be excluded."""
    txs = [
        _tx("checking", -600_000, "2026-10-02", payee="O&R Utilities",
            cleared="uncleared", matched="some-other-id"),
        _tx("checking", -351_180, "2026-10-01", payee="O&R Utilities", import_id="imp-1"),
    ]

    ghosts, unconfirmed = _find_ghosts(txs)

    assert ghosts == []
    assert unconfirmed == []


def test_generic_words_do_not_pair_unrelated_bills():
    """Two different bills can both read "Online Payment to ..."."""
    txs = [
        _tx("checking", -646_140, "2026-07-16", payee="Online Payment to Auto Loan",
            cleared="uncleared"),
        _tx("checking", -655_870, "2026-07-16", payee="Online Payment to Some Condo",
            import_id="imp-1"),
    ]

    ghosts, unconfirmed = _find_ghosts(txs)

    assert ghosts == []
    assert len(unconfirmed) == 1


def test_detects_ghost_whose_real_charge_landed_on_another_card():
    """The subscription moved cards; the schedule still posts to the old one."""
    txs = [
        _tx("unlimited", -22_990, "2026-09-20", payee="YouTube Premium", cleared="uncleared"),
        _tx("sapphire", -26_990, "2026-09-20", payee="YouTube Premium", import_id="imp-1"),
    ]

    ghosts, unconfirmed = _find_ghosts(txs)

    assert len(ghosts) == 1
    assert ghosts[0][1].account_id == "sapphire"
    assert unconfirmed == []


def test_same_account_twin_is_preferred_over_another_card():
    txs = [
        _tx("unlimited", -22_990, "2026-09-20", payee="YouTube Premium", cleared="uncleared"),
        _tx("sapphire", -26_990, "2026-09-20", payee="YouTube Premium", import_id="imp-1"),
        _tx("unlimited", -26_990, "2026-09-21", payee="YouTube Premium", import_id="imp-2"),
    ]

    ghosts, _ = _find_ghosts(txs)

    assert ghosts[0][1].account_id == "unlimited"


def _schedule(payee, sid=None):
    s = MagicMock()
    s.id = sid or payee
    s.payee_name = payee
    s.deleted = False
    return s


def test_ghost_schedules_names_the_schedule_behind_each_ghost():
    txs = [
        _tx("checking", -600_000, "2026-10-02", payee="O&R Utilities", cleared="uncleared"),
        _tx("checking", -351_180, "2026-10-01", payee="O&R Utilities", import_id="imp-1"),
    ]
    ghosts, _ = _find_ghosts(txs)
    scheduled = [_schedule("O&R Utilities"), _schedule("Verizon")]

    offenders = _ghost_schedules(scheduled, ghosts)

    assert [s.payee_name for s, _ in offenders] == ["O&R Utilities"]
    assert len(offenders[0][1]) == 1


def test_payee_tokens_drop_generic_words():
    assert _payee_tokens("Online Payment to Auto Loan") == {"loan"}
    assert "water" in _payee_tokens("Veolia Water")


# ── Categorization rules ─────────────────────────────────────────────────────


def _rule(match, category, exclude=""):
    from ynab_tools.config import Rule

    return Rule(match=match, category=category, exclude=exclude)


def _uncategorized(payee):
    t = _tx("checking", -1000, "2026-06-01", payee=payee)
    t.category_id = None
    return t


def test_first_matching_rule_wins():
    rules = [_rule("cafe", "Eating Out"), _rule("blue", "Entertainment")]

    matched = _apply_rules([_uncategorized("Blue Bottle Cafe")], rules)

    assert matched[0][1].category == "Eating Out"


def test_specific_rule_above_a_broad_one_acts_as_an_override():
    """A dance studio named Astaire must not be caught by an airline rule."""
    rules = [
        _rule("fred astaire", "Dancing"),
        _rule("air", "Travel"),
    ]

    matched = _apply_rules([_uncategorized("Fred Astaire")], rules)

    assert matched[0][1].category == "Dancing"


def test_without_the_override_the_broad_rule_miscategorizes():
    """Pins why rule order matters: the same payee lands in Travel."""
    rules = [_rule("air", "Travel")]

    matched = _apply_rules([_uncategorized("Fred Astaire")], rules)

    assert matched[0][1].category == "Travel"


def test_exclude_prevents_a_match():
    rules = [_rule("hudson", "Travel", exclude="beach glass")]

    assert _apply_rules([_uncategorized("Hudson Beach Glass")], rules) == []
    assert len(_apply_rules([_uncategorized("Hudson News JFK")], rules)) == 1


def test_unmatched_payee_is_left_alone():
    rules = [_rule("cafe", "Eating Out")]

    assert _apply_rules([_uncategorized("Some Hardware Store")], rules) == []


# ── ghosts --delete guard ────────────────────────────────────────────────────


def _ghost_and_twin():
    return [
        _tx("checking", -600_000, "2026-10-02", payee="O&R Utilities", cleared="uncleared"),
        _tx("checking", -351_180, "2026-10-01", payee="O&R Utilities", import_id="imp-1"),
    ]


@patch("ynab_tools.commands.tx.client")
def test_ghost_delete_refuses_while_a_schedule_still_produces_them(mock_client):
    """Deleting first erases the evidence `scheduled prune` needs."""
    mock_client.list_transactions.return_value = _ghost_and_twin()
    mock_client.list_scheduled_transactions.return_value = [_schedule("O&R Utilities")]

    result = runner.invoke(app, ["tx", "ghosts", "--delete"])

    assert result.exit_code == 1
    assert "scheduled prune --delete" in result.output
    mock_client._get_client.assert_not_called()


@patch("ynab_tools.commands.tx.client")
def test_ghost_delete_proceeds_with_keep_schedules(mock_client):
    mock_client.list_transactions.return_value = _ghost_and_twin()
    mock_client.list_scheduled_transactions.return_value = [_schedule("O&R Utilities")]

    with patch("ynab.TransactionsApi") as api:
        result = runner.invoke(app, ["tx", "ghosts", "--delete", "--keep-schedules"])

    assert result.exit_code == 0
    api.return_value.delete_transaction.assert_called_once()


@patch("ynab_tools.config.load_rules")
@patch("ynab_tools.commands.tx.client")
def test_categorize_rules_skips_tracking_accounts(mock_client, mock_rules):
    """A balance update on an investment account has no category to take."""
    from ynab_tools.config import Rule

    mock_rules.return_value = [Rule(match="market", category="Groceries")]
    ira = MagicMock(id="ira", on_budget=False)
    checking = MagicMock(id="checking", on_budget=True)
    mock_client.list_accounts.return_value = [ira, checking]
    update = _tx("ira", 1_000_000, "2026-10-17", payee="Broker Market Value")
    shop = _tx("checking", -20_000, "2026-10-18", payee="Farmers Market")
    for t in (update, shop):
        t.category_id = None
    mock_client.list_transactions.return_value = [update, shop]

    result = runner.invoke(app, ["tx", "categorize-rules", "--json"])

    assert result.exit_code == 0, result.output
    assert [m["payee"] for m in json.loads(result.output)] == ["Farmers Market"]


# ── JSON output stays clean ──────────────────────────────────────────────────


def _misrouted_payment(checking_name):
    """A checking payment linked to one card while another card imported it."""
    accounts = [_card("card-a", "Card A"), _card("card-b", "Card B")]
    out = _tx("checking", -250_000, "2026-09-15", import_id="imp-1", transfer_account_id="card-a")
    out.account_name = checking_name
    arrived = _tx("card-b", 250_000, "2026-09-15", payee="Automatic Payment", import_id="imp-2")
    return [out, arrived], accounts


@pytest.mark.parametrize("command", ["audit", "ghosts", "transfers"])
@patch("ynab_tools.commands.tx.client")
def test_json_output_is_pure_json_and_progress_goes_to_stderr(mock_client, command):
    txs, accounts = _misrouted_payment("Checking")
    mock_client.list_transactions.return_value = txs
    mock_client.list_accounts.return_value = accounts

    result = runner.invoke(app, ["tx", command, "--json"])

    assert result.exit_code == 0
    json.loads(result.stdout)
    assert "Fetching" not in result.stdout
    assert "Fetching" in result.stderr


@patch("ynab_tools.commands.tx.client")
def test_audit_reports_misrouted_payment_whatever_the_checking_account_is_called(mock_client):
    txs, accounts = _misrouted_payment("Everyday Spending")
    mock_client.list_transactions.return_value = txs
    mock_client.list_accounts.return_value = accounts

    result = runner.invoke(app, ["tx", "audit", "--json"])

    issues = [i for i in json.loads(result.stdout) if i["type"] == "misrouted_payment"]
    assert len(issues) == 1
    assert issues[0]["linked_to"] == "Card A"
    assert issues[0]["should_be"] == "card-b"
