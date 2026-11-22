"""Tests for `ynab money split`: what to keep in checking, move to savings, or invest."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.config import Goal, Profile
from ynab_tools.money.snapshot import Snapshot
from ynab_tools.money.split import split

runner = CliRunner()

TODAY = date(2026, 10, 5)
# A day inside each of the three full months before October.
IN_WINDOW = (date(2026, 7, 15), date(2026, 8, 15), date(2026, 9, 15))


def _account(aid, balance=0, acct_type="checking", on_budget=True, closed=False):
    a = MagicMock()
    a.id = aid
    a.name = aid
    a.type = acct_type
    a.balance = balance
    a.on_budget = on_budget
    a.closed = closed
    a.deleted = False
    a.direct_import_linked = False
    a.direct_import_in_error = False
    a.last_reconciled_at = (TODAY - timedelta(days=3)).isoformat()
    return a


def _tx(tid, account_id, amount, on, transfer_account_id=None, transfer_transaction_id=None,
        import_id="imp", cleared="cleared", payee="Payee"):
    t = MagicMock()
    t.id = tid
    t.account_id = account_id
    t.account_name = account_id
    t.amount = amount
    t.var_date = on
    t.payee_name = payee
    t.import_id = f"{import_id}-{tid}" if import_id else None
    t.cleared = cleared
    t.transfer_account_id = transfer_account_id
    t.transfer_transaction_id = transfer_transaction_id
    t.matched_transaction_id = None
    t.deleted = False
    return t


def _category(cid, name, group, balance=0, activity=0):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.category_group_name = group
    c.balance = balance
    c.activity = activity
    c.hidden = False
    c.deleted = False
    return c


def _month(categories):
    m = MagicMock()
    m.to_be_budgeted = 0
    m.categories = categories
    return m


def _history(spending_per_month):
    """Three full months, each with `spending_per_month` of grocery activity."""
    return [_month([_category("groc", "Groceries", "Food", activity=-spending_per_month)]) for _ in range(3)]


def _profile(**overrides) -> Profile:
    fields = dict(goals={"safety_net": Goal(category="Safety Net")})
    fields.update(overrides)
    return Profile(**fields)


def _snapshot(accounts, categories=(), txs=(), profile=None) -> Snapshot:
    return Snapshot(
        month=TODAY.replace(day=1),
        month_detail=_month(list(categories)),
        accounts=accounts,
        transactions=list(txs),
        profile=profile or _profile(),
        today=TODAY,
        since=date(2026, 6, 1),
    )


def _bills(per_month, account="checking-a"):
    """One plain outflow of `per_month` from checking in each window month."""
    return [_tx(f"bill-{i}", account, -per_month, on) for i, on in enumerate(IN_WINDOW)]


def _recompute(figure: dict):
    """Evaluate a figure's formula with only its own inputs in scope."""
    scope = {"__builtins__": {}, "sum": sum, "max": max, "min": min, "round": round, **figure["inputs"]}
    return eval(figure["formula"], scope)  # noqa: S307 - formulas are our own code


def _ae2():
    """Bills $5,000 + card owed $3,000; half of $6,000 spending; checking $20,000;
    savings $10,000 against a $15,000 safety net."""
    accounts = [
        _account("checking-a", 20_000_000),
        _account("savings-a", 10_000_000, "savings"),
        _account("card-a", -3_000_000, "creditCard"),
    ]
    categories = [_category("sn", "Safety Net", "Goals", balance=15_000_000)]
    return _snapshot(accounts, categories, _bills(5_000_000)), _history(6_000_000)


# ── Figures ──────────────────────────────────────────────────────────────────


def test_ae2_keep_11000_move_5000_invest_4000():
    answer = split(*_ae2())

    f = {name: fig["value"] for name, fig in answer["figures"].items()}
    assert f["bills"] + f["card_payments"] == 8_000_000
    assert f["spending_cushion"] == 3_000_000
    assert f["checking_recommendation"] == 11_000_000
    assert f["checking_difference"] == 9_000_000
    assert f["savings_shortfall"] == 5_000_000
    assert f["move_to_savings"] == 5_000_000
    assert f["available_to_invest"] == 4_000_000
    assert f["from_savings"] == 0
    assert answer["status"] == "exact"


def test_every_figure_recomputes_from_its_own_inputs_and_formula():
    answer = split(*_ae2())

    for name, fig in answer["figures"].items():
        assert fig["formula"] and fig["inputs"] is not None, name
        assert _recompute(fig) == fig["value"], name


def test_savings_held_group_raises_what_savings_must_hold():
    snapshot, history = _ae2()
    snapshot.profile = _profile(savings_held_groups=["Sinking Funds"])
    snapshot.month_detail.categories.append(
        _category("ins", "Insurance", "Sinking Funds", balance=2_000_000))

    f = {name: fig["value"] for name, fig in split(snapshot, history)["figures"].items()}

    assert f["savings_must_hold"] == 17_000_000
    assert f["move_to_savings"] == 7_000_000
    assert f["available_to_invest"] == 2_000_000


def test_checking_short_suggests_savings_surplus():
    accounts = [
        _account("checking-a", 2_000_000),
        _account("savings-a", 16_000_000, "savings"),
        _account("card-a", -3_000_000, "creditCard"),
    ]
    categories = [_category("sn", "Safety Net", "Goals", balance=15_000_000)]
    answer = split(_snapshot(accounts, categories, _bills(5_000_000)), _history(6_000_000))

    f = {name: fig["value"] for name, fig in answer["figures"].items()}
    assert f["checking_difference"] == -9_000_000
    assert f["savings_surplus"] == 1_000_000
    assert f["from_savings"] == 1_000_000
    assert f["move_to_savings"] == 0
    assert f["available_to_invest"] == 0
    assert any("savings" in a and "checking" in a for a in answer["next_actions"])


def test_checking_short_without_savings_surplus_suggests_no_transfer():
    accounts = [
        _account("checking-a", 2_000_000),
        _account("savings-a", 15_000_000, "savings"),
        _account("card-a", -3_000_000, "creditCard"),
    ]
    categories = [_category("sn", "Safety Net", "Goals", balance=15_000_000)]
    answer = split(_snapshot(accounts, categories, _bills(5_000_000)), _history(6_000_000))

    f = {name: fig["value"] for name, fig in answer["figures"].items()}
    assert f["checking_difference"] == -9_000_000
    assert f["from_savings"] == 0
    assert not any("Move" in a for a in answer["next_actions"])


def test_mortgage_transfer_is_a_bill_and_savings_brokerage_and_card_transfers_are_not():
    accounts = [
        _account("checking-a", 0),
        _account("savings-a", 0, "savings"),
        _account("card-a", 0, "creditCard"),
        _account("mortgage-a", -300_000_000, "mortgage"),
        _account("brokerage-a", 20_000_000, "otherAsset"),
    ]
    on = IN_WINDOW[1]
    txs = [
        _tx("to-mortgage", "checking-a", -400_000, on, transfer_account_id="mortgage-a"),
        _tx("to-savings", "checking-a", -900_000, on, transfer_account_id="savings-a"),
        _tx("to-card", "checking-a", -700_000, on, transfer_account_id="card-a"),
        # Investing is what the split itself decides, not a bill.
        _tx("to-brokerage", "checking-a", -1_000_000, on, transfer_account_id="brokerage-a"),
        # Outside the three full months: this month, and before the window.
        _tx("this-month", "checking-a", -50_000, TODAY - timedelta(days=1)),
        _tx("too-old", "checking-a", -50_000, date(2026, 6, 30)),
        # Inflows are not bills.
        _tx("paycheck", "checking-a", 5_000_000, on),
    ]

    bills = split(_snapshot(accounts, txs=txs), _history(0))["figures"]["bills"]

    assert bills["value"] == round(400_000 / 3)


def test_split_transaction_counts_in_spending_through_month_activity():
    # One split purchase of $90: $60 groceries and $30 household, every month.
    history = [
        _month([
            _category("groc", "Groceries", "Food", activity=-60_000),
            _category("home", "Household", "Home", activity=-30_000),
            _category("pay", "card-a", "Credit Card Payments", activity=-500_000),
            _category("inflow", "Inflow: Ready to Assign", "Internal Master Category", activity=9_000_000),
        ])
        for _ in range(3)
    ]
    snapshot = _snapshot([_account("checking-a", 0)])

    cushion = split(snapshot, history)["figures"]["spending_cushion"]

    assert cushion["value"] == 45_000


def test_unconfirmed_card_payment_warns_on_the_checking_recommendation():
    snapshot, history = _ae2()
    pay_on = TODAY - timedelta(days=20)
    snapshot.transactions += [
        _tx("pay-out", "checking-a", -250_000, pay_on,
            transfer_account_id="card-a", transfer_transaction_id="pay-in"),
        _tx("pay-in", "card-a", 250_000, pay_on, import_id=None, cleared="uncleared",
            transfer_account_id="checking-a", transfer_transaction_id="pay-out"),
    ]

    answer = split(snapshot, history)

    assert answer["status"] == "approximate"
    [warning] = answer["warnings"]
    assert warning["code"] == "unconfirmed_payment"
    assert "checking_recommendation" in warning["affects"]
    assert answer["figures"]["checking_recommendation"]["uncertainty"] == 250_000


def test_tracking_accounts_are_excluded():
    snapshot, history = _ae2()
    snapshot.accounts.append(_account("retirement", 50_000_000, "otherAsset", on_budget=False))

    answer = split(snapshot, history)

    assert [e["item"] for e in answer["excluded"]] == ["retirement"]
    assert answer["figures"]["available_to_invest"]["value"] == 4_000_000


# ── Command ──────────────────────────────────────────────────────────────────


def test_command_reads_three_full_months_and_prints_only_json():
    snapshot, history = _ae2()
    with patch("ynab_tools.money.split.load_snapshot") as load, \
            patch("ynab_tools.money.split.client") as client:
        load.return_value = snapshot
        client.get_month_history.return_value = history
        result = runner.invoke(app, ["money", "split", "--json"])

    assert result.exit_code == 0, result.output
    answer = json.loads(result.stdout)
    assert answer["command"] == "money split"
    assert answer["figures"]["available_to_invest"]["value"] == 4_000_000
    assert client.get_month_history.call_args.args[:2] == ("2026-10-01", 3)


def test_snapshot_reaches_back_to_the_first_full_month():
    with patch("ynab_tools.money.split.load_snapshot") as load, \
            patch("ynab_tools.money.split.client") as client:
        load.return_value = _ae2()[0]
        client.get_month_history.return_value = _ae2()[1]
        from ynab_tools.money.split import split_answer
        split_answer(today=date(2026, 10, 31))

    # July 1 is 122 days before October 31, beyond the default 120.
    assert load.call_args.kwargs["history_days"] == 122


def test_command_renders_without_json():
    snapshot, history = _ae2()
    with patch("ynab_tools.money.split.load_snapshot") as load, \
            patch("ynab_tools.money.split.client") as client:
        load.return_value = snapshot
        client.get_month_history.return_value = history
        result = runner.invoke(app, ["money", "split"])

    assert result.exit_code == 0, result.output
    assert "checking_recommendation" in result.stdout
