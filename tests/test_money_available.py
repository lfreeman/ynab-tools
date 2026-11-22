"""Tests for `ynab money available`: what is free after everything already committed."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.config import Goal, Profile, Statement
from ynab_tools.money.available import available
from ynab_tools.money.snapshot import Snapshot

runner = CliRunner()

TODAY = date(2026, 10, 5)


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


def _category(cid, name, group, balance=0, goal_type=None, goal_target=None,
              goal_overall_left=None, goal_target_date=None):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.category_group_name = group
    c.balance = balance
    c.hidden = False
    c.deleted = False
    c.goal_type = goal_type
    c.goal_target = goal_target
    c.goal_overall_left = goal_overall_left
    c.goal_target_date = goal_target_date
    return c


def _month(rta, categories):
    m = MagicMock()
    m.to_be_budgeted = rta
    m.categories = categories
    return m


def _profile(**overrides) -> Profile:
    fields = dict(goals={
        "safety_net": Goal(category="Safety Net"),
        "car": Goal(category="Next Car"),
        "school": Goal(category="School"),
    })
    fields.update(overrides)
    return Profile(**fields)


def _snapshot(rta=0, categories=None, accounts=None, profile=None) -> Snapshot:
    return Snapshot(
        month=TODAY.replace(day=1),
        month_detail=_month(rta, categories or []),
        accounts=accounts if accounts is not None else [_account("checking-a", 10_000_000)],
        transactions=[],
        profile=profile or _profile(),
        today=TODAY,
        since=TODAY - timedelta(days=120),
    )


def _goal(cid, name, left, goal_type="TBD", target=None, balance=0):
    return _category(cid, name, "Goals", balance=balance, goal_type=goal_type,
                     goal_target=target, goal_overall_left=left, goal_target_date=date(2027, 10, 1))


def _ae6_snapshot(**kw) -> Snapshot:
    """RTA $2,000; Dining overspent by $500; the goals still need $4,000 in all."""
    return _snapshot(rta=2_000_000, categories=[
        _category("dining", "Dining Out", "Fun", balance=-500_000),
        _category("rent", "Rent", "Housing", balance=1_200_000),
        _goal("sn", "Safety Net", 3_000_000),
        _goal("car", "Next Car", 1_000_000),
        _goal("school", "School", 0),
    ], **kw)


def _recompute(figure: dict):
    """Evaluate a figure's formula with only its own inputs in scope."""
    scope = {"__builtins__": {}, "sum": sum, "max": max, "min": min, "round": round, **figure["inputs"]}
    return eval(figure["formula"], scope)  # noqa: S307 - formulas are our own code


# ── Lines of the answer ──────────────────────────────────────────────────────


def test_ae6_free_is_rta_minus_overspending_minus_still_needed():
    answer = available(_ae6_snapshot())

    figures = answer["figures"]
    assert figures["ready_to_assign"]["value"] == 2_000_000
    assert figures["overspending"]["value"] == 500_000
    assert figures["still_needed"]["value"] == 4_000_000
    assert figures["free"]["value"] == -2_500_000
    for name in ("ready_to_assign", "overspending", "uncategorized", "card_shortfall", "still_needed", "free"):
        assert figures[name]["formula"], name
    assert answer["status"] == "exact"


def test_every_figure_recomputes_from_its_own_inputs_and_formula():
    snapshot = _snapshot(rta=1_500_000, accounts=[
        _account("checking-a", 10_000_000),
        _account("card-a", -800_000, acct_type="creditCard"),
        _account("card-b", 200_000, acct_type="creditCard"),
    ], categories=[
        _category("dining", "Dining Out", "Fun", balance=-500_000),
        _category("uncat", "Uncategorized", "Internal Master Category", balance=-70_000),
        _category("pay-a", "card-a", "Credit Card Payments", balance=500_000),
        _category("pay-b", "card-b", "Credit Card Payments", balance=0),
        _goal("sn", "Safety Net", 3_000_000),
        _goal("car", "Next Car", None, goal_type="NEED", target=5_000_000, balance=1_000_000),
    ])

    figures = available(snapshot)["figures"]

    assert figures
    for name, f in figures.items():
        assert _recompute(f) == f["value"], name


def test_uncategorized_outflow_is_its_own_line_not_in_overspending():
    answer = available(_snapshot(rta=1_000_000, categories=[
        _category("uncat", "Uncategorized", "Internal Master Category", balance=-70_000),
        _category("dining", "Dining Out", "Fun", balance=-30_000),
    ]))

    figures = answer["figures"]
    assert figures["uncategorized"]["value"] == 70_000
    assert figures["overspending"]["value"] == 30_000
    assert figures["free"]["value"] == 900_000


def test_ae3_tracking_account_is_excluded_and_changes_no_figure():
    accounts = [_account("checking-a", 10_000_000)]
    base = available(_snapshot(rta=2_000_000, accounts=accounts))
    with_brokerage = available(_snapshot(rta=2_000_000, accounts=[
        *accounts, _account("brokerage", 5_000_000, acct_type="otherAsset", on_budget=False),
    ]))

    assert with_brokerage["figures"] == base["figures"]
    excluded = with_brokerage["excluded"]
    assert [e["item"] for e in excluded] == ["brokerage"]
    assert excluded[0]["balance"] == 5_000_000


def test_card_shortfall_is_owed_minus_payment_category_and_card_in_credit_gives_zero():
    answer = available(_snapshot(rta=1_000_000, accounts=[
        _account("checking-a", 10_000_000),
        _account("card-a", -800_000, acct_type="creditCard"),
        _account("card-b", 300_000, acct_type="creditCard"),
    ], categories=[
        _category("pay-a", "card-a", "Credit Card Payments", balance=500_000),
        _category("pay-b", "card-b", "Credit Card Payments", balance=0),
    ]))

    shortfall = answer["figures"]["card_shortfall"]
    assert shortfall["value"] == 300_000
    assert shortfall["inputs"]["owed"] == {"card-a": 800_000, "card-b": 0}
    assert answer["figures"]["free"]["value"] == 700_000


def test_card_payment_category_is_not_overspending():
    answer = available(_snapshot(rta=0, accounts=[
        _account("checking-a", 10_000_000),
        _account("card-a", 0, acct_type="creditCard"),
    ], categories=[_category("pay-a", "card-a", "Credit Card Payments", balance=-100_000)]))

    assert answer["figures"]["overspending"]["value"] == 0


def test_tbd_goal_uses_overall_left_and_need_goal_is_estimate_with_ui_action():
    answer = available(_snapshot(rta=10_000_000, categories=[
        _goal("sn", "Safety Net", 3_000_000, target=24_000_000, balance=21_000_000),
        _goal("car", "Next Car", 99_000_000, goal_type="NEED", target=5_000_000, balance=1_000_000),
    ]))

    still = answer["figures"]["still_needed"]
    # Safety net: its goal_overall_left. Car: target minus balance, ignoring the
    # NEED goal's own (monthly) remaining amount.
    assert still["value"] == 3_000_000 + 4_000_000
    assert still["estimate"] is True
    assert [a["category"] for a in answer["ui_actions"]] == ["Next Car"]
    assert answer["ui_actions"][0]["set"]["goal_type"] == "TBD"


def test_only_finished_goals_is_not_an_estimate():
    answer = available(_ae6_snapshot())

    assert answer["figures"]["still_needed"]["estimate"] is False
    assert answer["ui_actions"] == []


def test_negative_rta_is_reported_and_free_is_not_clamped():
    answer = available(_snapshot(rta=-1_200_000, categories=[_goal("sn", "Safety Net", 800_000)]))

    assert answer["figures"]["ready_to_assign"]["value"] == -1_200_000
    assert answer["figures"]["free"]["value"] == -2_000_000


def test_ae1_unexplained_gap_makes_answer_approximate_with_bound_on_free():
    gapped = _account("checking-a", 11_000_000)
    profile = _profile(statements={"checking-a": Statement(balance=10_000_000, on=TODAY - timedelta(days=2))})
    snapshot = _snapshot(rta=2_000_000, accounts=[gapped, _account("savings-a", 4_000_000, "savings")],
                         profile=profile)

    answer = available(snapshot)

    assert answer["status"] == "approximate"
    assert answer["figures"]["free"]["value"] == 2_000_000
    assert answer["figures"]["free"]["uncertainty"] == 1_000_000
    [warning] = answer["warnings"]
    assert warning["code"] == "statement_gap"
    assert warning["bound"] == 1_000_000
    assert "checking-a" in warning["message"]
    assert "Explain the gap" in warning["fix"]


def test_clean_data_gives_zero_uncertainty():
    answer = available(_ae6_snapshot())

    assert answer["warnings"] == []
    assert answer["figures"]["free"]["uncertainty"] == 0


# ── Command ──────────────────────────────────────────────────────────────────


def test_command_json_is_the_answer_alone_on_stdout():
    with patch("ynab_tools.money.available.load_snapshot") as load:
        load.return_value = _ae6_snapshot()
        result = runner.invoke(app, ["money", "available", "--json"])

    assert result.exit_code == 0, result.output
    answer = json.loads(result.stdout)
    assert answer["command"] == "money available"
    assert answer["figures"]["free"]["value"] == -2_500_000


def test_command_renders_without_json():
    with patch("ynab_tools.money.available.load_snapshot") as load:
        load.return_value = _ae6_snapshot()
        result = runner.invoke(app, ["money", "available"])

    assert result.exit_code == 0, result.output
    assert "free" in result.stdout


def test_command_reports_missing_configuration():
    with patch("ynab_tools.money.available.load_snapshot", side_effect=RuntimeError("No budget set")):
        result = runner.invoke(app, ["money", "available", "--json"])

    assert result.exit_code == 1
    assert "No budget set" in result.output
