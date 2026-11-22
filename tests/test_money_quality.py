"""Tests for the money snapshot, data-quality pass and answer shape."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock, patch

from ynab_tools.config import Profile, Statement
from ynab_tools.money.answer import Figure, build_answer
from ynab_tools.money.quality import (
    CARD_OWED,
    CHECKING_RECOMMENDATION,
    FREE,
    DataWarning,
    account_balance,
    check_quality,
    dedupe,
    stale_tracking_values,
)
from ynab_tools.money.snapshot import Snapshot, load_snapshot

TODAY = date(2026, 10, 5)


def _days_ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def _reconciled(n: int) -> datetime:
    d = _days_ago(n)
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _account(aid, balance=0, acct_type="checking", reconciled_days_ago=5, in_error=False,
             on_budget=True, linked=None):
    a = MagicMock()
    a.id = aid
    a.name = aid
    a.type = acct_type
    a.balance = balance
    a.on_budget = on_budget
    a.closed = False
    a.deleted = False
    a.direct_import_linked = in_error if linked is None else linked
    a.direct_import_in_error = in_error
    a.last_reconciled_at = None if reconciled_days_ago is None else _reconciled(reconciled_days_ago)
    return a


def _tx(tid, account_id, amount, days_ago, payee=None, import_id=None, cleared="cleared",
        transfer_account_id=None, transfer_transaction_id=None):
    t = MagicMock()
    t.id = tid
    t.account_id = account_id
    t.account_name = account_id
    t.amount = amount
    t.var_date = _days_ago(days_ago)
    t.payee_name = payee
    t.import_id = import_id
    t.cleared = cleared
    t.transfer_account_id = transfer_account_id
    t.transfer_transaction_id = transfer_transaction_id
    t.matched_transaction_id = None
    t.deleted = False
    return t


def _snapshot(accounts, txs, statements=None) -> Snapshot:
    return Snapshot(
        month=TODAY.replace(day=1),
        month_detail=MagicMock(),
        accounts=accounts,
        transactions=txs,
        profile=Profile(statements=statements or {}),
        today=TODAY,
        since=_days_ago(120),
    )


def _ghost_and_twin(account_id="checking", days_ago=3):
    """A scheduled entry the bank never confirmed, beside the real imported charge."""
    return [
        _tx("ghost", account_id, -100_000, days_ago, payee="Water Utility", cleared="uncleared"),
        _tx("real", account_id, -98_000, days_ago, payee="Water Utility", import_id="imp-1"),
    ]


# ── Statement comparison (KTD8) ──────────────────────────────────────────────


def test_unexplained_statement_gap_bounds_the_account_balance():
    """AE1: the bank says $1,000 less than YNAB did on the statement date."""
    checking = _account("checking", balance=5_000_000)
    snapshot = _snapshot(
        [checking], [],
        statements={"checking": Statement(balance=4_000_000, on=_days_ago(5))},
    )

    warnings = check_quality(snapshot)

    assert len(warnings) == 1
    gap = warnings[0]
    assert gap.code == "statement_gap"
    assert gap.bound == 1_000_000
    assert gap.affects == [account_balance("checking")]
    assert "explain" in gap.fix.lower()

    answer = build_answer(
        "money available", snapshot.month,
        {FREE: Figure(value=2_000_000, formula="f", inputs={},
                      depends_on=[account_balance("checking")])},
        warnings,
    )
    assert answer["status"] == "approximate"
    assert answer["figures"][FREE]["uncertainty"] == 1_000_000
    assert answer["figures"][FREE]["value"] == 2_000_000


def test_statement_compares_as_of_its_date():
    """Activity after the statement date is not part of the gap."""
    checking = _account("checking", balance=4_700_000)
    txs = [_tx("later", "checking", -300_000, 2, import_id="imp-9")]
    snapshot = _snapshot(
        [checking], txs,
        statements={"checking": Statement(balance=5_000_000, on=_days_ago(5))},
    )

    assert check_quality(snapshot) == []


def test_ghosts_in_the_statement_window_are_removed_before_the_gap():
    """A ghost inflates YNAB's outflows; the bank never saw it, so it is not a gap."""
    checking = _account("checking", balance=902_000)
    snapshot = _snapshot(
        [checking], _ghost_and_twin(days_ago=10),
        statements={"checking": Statement(balance=1_002_000, on=_days_ago(5))},
    )

    warnings = check_quality(snapshot)

    assert [w.code for w in warnings] == ["ghost"]
    assert warnings[0].bound == 100_000
    assert warnings[0].affects == [account_balance("checking"), FREE]
    assert warnings[0].items == ["ghost"]


def test_old_statement_without_reconcile_is_not_checked_against_the_bank():
    checking = _account("checking", balance=1_000_000, reconciled_days_ago=None)
    snapshot = _snapshot(
        [checking], [],
        statements={"checking": Statement(balance=1_000_000, on=_days_ago(60))},
    )

    warnings = check_quality(snapshot)

    assert [w.code for w in warnings] == ["not_checked"]
    assert warnings[0].bound is None
    assert warnings[0].affects == [account_balance("checking")]


def test_old_statement_with_recent_reconcile_is_checked():
    checking = _account("checking", balance=1_000_000, reconciled_days_ago=10)
    snapshot = _snapshot(
        [checking], [],
        statements={"checking": Statement(balance=1_000_000, on=_days_ago(60))},
    )

    assert check_quality(snapshot) == []


def test_tracking_accounts_are_not_held_to_statements():
    """A brokerage balance never feeds a money figure (R6)."""
    brokerage = _account("brokerage", acct_type="otherAsset", on_budget=False,
                         reconciled_days_ago=None)

    assert check_quality(_snapshot([brokerage], [])) == []


# ── Re-authentication ────────────────────────────────────────────────────────


def test_account_needing_reauth_makes_its_figures_uncertainty_unknown():
    checking = _account("checking", in_error=True)
    snapshot = _snapshot([checking], [])

    warnings = check_quality(snapshot)

    assert [w.code for w in warnings] == ["needs_reauth"]
    assert warnings[0].bound is None
    answer = build_answer(
        "money available", snapshot.month,
        {FREE: Figure(value=1, formula="f", inputs={}, depends_on=[account_balance("checking")])},
        warnings,
    )
    assert answer["figures"][FREE]["uncertainty"] is None
    assert answer["status"] == "approximate"


# ── Card payments and adjustments ────────────────────────────────────────────


def _misrouted_unconfirmed_payment():
    """Paid from checking to card A, but card B's feed imported the money.

    Card A's side never cleared, so the same payment is both misrouted
    (checking side) and unconfirmed (card A side).
    """
    accounts = [
        _account("checking"),
        _account("card-a", acct_type="creditCard"),
        _account("card-b", acct_type="creditCard"),
    ]
    txs = [
        _tx("pay-out", "checking", -500_000, 20, import_id="imp-1",
            transfer_account_id="card-a", transfer_transaction_id="pay-in"),
        _tx("pay-in", "card-a", 500_000, 20, cleared="uncleared",
            transfer_account_id="checking", transfer_transaction_id="pay-out"),
        _tx("card-b-import", "card-b", 500_000, 19, payee="Payment Thank You", import_id="imp-2"),
    ]
    return accounts, txs


def test_payment_flagged_by_two_detectors_produces_one_warning():
    accounts, txs = _misrouted_unconfirmed_payment()

    warnings = check_quality(_snapshot(accounts, txs))

    assert len(warnings) == 1
    w = warnings[0]
    assert w.code == "misrouted_payment"
    assert w.bound == 500_000
    assert w.affects == [CARD_OWED, CHECKING_RECOMMENDATION, FREE]
    assert "pay-out" in w.items


def test_unconfirmed_payment_alone():
    accounts = [_account("checking"), _account("card-a", acct_type="creditCard")]
    txs = [
        _tx("pay-out", "checking", -250_000, 20, import_id="imp-1",
            transfer_account_id="card-a", transfer_transaction_id="pay-in"),
        _tx("pay-in", "card-a", 250_000, 20, cleared="uncleared",
            transfer_account_id="checking", transfer_transaction_id="pay-out"),
    ]

    warnings = check_quality(_snapshot(accounts, txs))

    assert [w.code for w in warnings] == ["unconfirmed_payment"]
    assert warnings[0].bound == 250_000
    assert warnings[0].affects == [CARD_OWED, CHECKING_RECOMMENDATION, FREE]


def test_dedupe_keeps_the_larger_bound_for_a_shared_transaction():
    ghost = DataWarning("ghost", "m", 40_000, [FREE], "f", ["tx-1"])
    unconfirmed = DataWarning("unconfirmed_payment", "m", 90_000, [FREE], "f", ["tx-1"])
    other = DataWarning("ghost", "m", 10_000, [FREE], "f", ["tx-2"])

    kept = dedupe([ghost, unconfirmed, other])

    assert kept == [unconfirmed, other]


def test_mirrored_adjustments_are_bounded_by_the_positive_side():
    accounts = [_account("card-a", acct_type="creditCard"), _account("card-b", acct_type="creditCard")]
    txs = [
        _tx("adj-neg", "card-a", -70_000, 15, payee="Reconciliation Balance Adjustment"),
        _tx("adj-pos", "card-b", 70_000, 14, payee="Reconciliation Balance Adjustment"),
    ]

    warnings = check_quality(_snapshot(accounts, txs))

    assert [w.code for w in warnings] == ["mirrored_adjustment"]
    assert warnings[0].bound == 70_000
    assert warnings[0].affects == [account_balance("card-a"), account_balance("card-b")]
    assert warnings[0].items == ["adj-neg", "adj-pos"]


# ── Clean data and the answer shape ──────────────────────────────────────────


def test_clean_data_is_exact():
    snapshot = _snapshot([_account("checking", balance=1_000_000)], [])

    warnings = check_quality(snapshot)
    answer = build_answer(
        "money available", snapshot.month,
        {FREE: Figure(value=1_000_000, formula="f", inputs={"a": 1})},
        warnings,
    )

    assert warnings == []
    assert answer["status"] == "exact"
    assert answer["warnings"] == []
    assert answer["units"] == "milliunits"
    assert answer["month"] == "2026-10-01"
    assert answer["figures"][FREE] == {
        "value": 1_000_000, "formula": "f", "inputs": {"a": 1},
        "estimate": False, "uncertainty": 0,
    }
    for key in ("rules_applied", "excluded", "ui_actions", "next_actions"):
        assert answer[key] == []


# ── Snapshot ─────────────────────────────────────────────────────────────────


@patch("ynab_tools.money.snapshot.load_profile", return_value=Profile())
@patch("ynab_tools.money.snapshot.client")
def test_snapshot_reads_the_budget_in_three_requests(mock_client, _profile):
    deleted = _tx("gone", "checking", -1, 1)
    deleted.deleted = True
    mock_client.list_accounts.return_value = [_account("checking")]
    mock_client.list_transactions.return_value = [_tx("kept", "checking", -1, 1), deleted]

    snapshot = load_snapshot(today=TODAY)

    mock_client.get_month.assert_called_once_with("2026-10-01", budget_id=None)
    mock_client.list_accounts.assert_called_once_with(budget_id=None)
    mock_client.list_transactions.assert_called_once_with(
        budget_id=None, since_date=(TODAY - timedelta(days=120)).isoformat()
    )
    assert len(mock_client.method_calls) == 3
    assert [t.id for t in snapshot.transactions] == ["kept"]


def _codes(snapshot) -> list[str]:
    return [w.code for w in check_quality(snapshot)]


def test_linked_account_with_a_balance_and_no_import_for_45_days_is_not_importing():
    card = _account("card", -500_000, "creditCard", linked=True)
    txs = [_tx("old", "card", -20_000, 45, import_id="YNAB:-20000:old:1")]

    warnings = [w for w in check_quality(_snapshot([card], txs)) if w.code == "not_importing"]

    assert len(warnings) == 1
    assert warnings[0].bound is None
    assert warnings[0].affects == [account_balance("card")]


def test_linked_account_that_imported_10_days_ago_is_importing():
    card = _account("card", -500_000, "creditCard", linked=True)
    txs = [_tx("recent", "card", -20_000, 10, import_id="YNAB:-20000:recent:1")]

    assert "not_importing" not in _codes(_snapshot([card], txs))


def test_idle_linked_card_with_zero_balance_is_not_flagged():
    card = _account("card", 0, "creditCard", linked=True)

    assert "not_importing" not in _codes(_snapshot([card], []))


def test_unlinked_account_kept_by_hand_is_not_flagged():
    cash = _account("cash", 11_780, linked=False)
    txs = [_tx("manual", "cash", 5_000, 40)]

    assert "not_importing" not in _codes(_snapshot([cash], txs))


def test_unlinked_account_that_used_to_import_is_not_importing():
    card = _account("card", 2_754_290, "creditCard", linked=False)
    txs = [_tx("last", "card", 260_410, 40, import_id="YNAB:260410:last:1")]

    assert "not_importing" in _codes(_snapshot([card], txs))


# ── Payment warnings a statement already settles ─────────────────────────────


def _unconfirmed_payment(paid_days_ago=20):
    accounts = [_account("checking"), _account("card-a", balance=250_000, acct_type="creditCard")]
    txs = [
        _tx("pay-out", "checking", -250_000, paid_days_ago, import_id="imp-1",
            transfer_account_id="card-a", transfer_transaction_id="pay-in"),
        _tx("pay-in", "card-a", 250_000, paid_days_ago, cleared="uncleared",
            transfer_account_id="checking", transfer_transaction_id="pay-out"),
    ]
    return accounts, txs


def test_recent_matching_statement_drops_unconfirmed_payment():
    """The card's own statement balance already includes the payment."""
    accounts, txs = _unconfirmed_payment()
    statements = {"card-a": Statement(balance=250_000, on=_days_ago(5))}

    warnings = check_quality(_snapshot(accounts, txs, statements=statements))

    assert [w.code for w in warnings] == []


def test_recent_matching_statement_drops_misrouted_payment():
    accounts, txs = _misrouted_unconfirmed_payment()
    accounts[1].balance = 500_000
    statements = {"card-a": Statement(balance=500_000, on=_days_ago(5))}

    warnings = check_quality(_snapshot(accounts, txs, statements=statements))

    assert [w.code for w in warnings] == []


def test_statement_with_a_gap_keeps_unconfirmed_payment():
    accounts, txs = _unconfirmed_payment()
    statements = {"card-a": Statement(balance=0, on=_days_ago(5))}

    warnings = check_quality(_snapshot(accounts, txs, statements=statements))

    assert "unconfirmed_payment" in [w.code for w in warnings]


def test_stale_statement_keeps_unconfirmed_payment():
    accounts, txs = _unconfirmed_payment(paid_days_ago=70)
    statements = {"card-a": Statement(balance=250_000, on=_days_ago(60))}

    warnings = check_quality(_snapshot(accounts, txs, statements=statements))

    assert "unconfirmed_payment" in [w.code for w in warnings]


def test_statement_before_the_payment_keeps_unconfirmed_payment():
    """A statement dated before the payment cannot have seen it."""
    accounts, txs = _unconfirmed_payment(paid_days_ago=20)
    accounts[1].balance = 0
    txs[1].amount = 250_000
    statements = {"card-a": Statement(balance=-250_000, on=_days_ago(25))}

    warnings = check_quality(_snapshot(accounts, txs, statements=statements))

    assert "unconfirmed_payment" in [w.code for w in warnings]


def test_stale_tracking_values_flags_investment_left_at_its_starting_balance():
    """A starting balance months old, never reconciled, means the value is out of date."""
    ira = _account("ira", balance=226_815_970, acct_type="otherAsset", on_budget=False,
                   reconciled_days_ago=None, linked=False)
    start = _tx("start", "ira", 226_815_970, 205, payee="Starting Balance")
    assert stale_tracking_values([ira], [start], TODAY) == [(ira, _days_ago(205))]


def test_stale_tracking_values_counts_a_recent_reconcile_and_ignores_budget_accounts():
    fresh = _account("roth", balance=16_807_030, acct_type="otherAsset", on_budget=False,
                     reconciled_days_ago=3)
    empty = _account("old", balance=0, acct_type="otherAsset", on_budget=False, reconciled_days_ago=None)
    checking = _account("checking", balance=1_000_000, reconciled_days_ago=None)
    start = _tx("start", "roth", 16_807_030, 205, payee="Starting Balance")
    assert stale_tracking_values([fresh, empty, checking], [start], TODAY) == []
