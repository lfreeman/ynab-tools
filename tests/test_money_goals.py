"""Tests for `ynab money goals`: sizing the safety net and goals, and creating them."""

import json
from datetime import date
from unittest.mock import MagicMock, call, patch

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.config import Goal, Profile
from ynab_tools.money.goals import (
    average_monthly_spending,
    car_estimate,
    months_out,
    resolve_essentials,
    safety_net_target,
    starting_balance,
)
from ynab_tools.money.snapshot import Snapshot

runner = CliRunner()

TODAY = date(2026, 10, 5)
NEXT_YEAR = date(2027, 10, 5)


def _category(cid, name, group, activity=0, goal_type=None, goal_target=None,
              goal_target_date=None, hidden=False):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.category_group_name = group
    c.category_group_id = f"group-{group}"
    c.activity = activity
    c.balance = 0
    c.hidden = hidden
    c.deleted = False
    c.goal_type = goal_type
    c.goal_target = goal_target
    c.goal_target_date = goal_target_date
    return c


def _month(categories):
    m = MagicMock()
    m.categories = categories
    return m


def _group(name, categories, gid=None):
    g = MagicMock()
    g.id = gid or f"group-{name}"
    g.name = name
    g.hidden = False
    g.deleted = False
    g.categories = categories
    return g


def _account(aid, name, acct_type, on_budget=False, closed=True):
    a = MagicMock()
    a.id = aid
    a.name = name
    a.type = acct_type
    a.on_budget = on_budget
    a.closed = closed
    a.deleted = False
    a.balance = 0
    a.direct_import_linked = False
    a.direct_import_in_error = False
    a.last_reconciled_at = None
    return a


def _starting_balance_tx(amount, on):
    t = MagicMock()
    t.payee_name = "Starting Balance"
    t.amount = amount
    t.var_date = on
    t.deleted = False
    return t


# Current month: two essential categories in a "Housing" group, one want.
def _current_categories():
    return [
        _category("rent", "Rent", "Housing"),
        _category("power", "Electric", "Utilities"),
        _category("tuition", "Tuition", "College"),
        _category("dining", "Dining Out", "Fun"),
    ]


def _history(rent=-3_000_000, power=-1_000_000, tuition=-500_000, dining=-400_000):
    """Twelve full months with the same activity each month."""
    return [
        _month([
            _category("rent", "Rent", "Housing", activity=rent),
            _category("power", "Electric", "Utilities", activity=power),
            _category("tuition", "Tuition", "College", activity=tuition),
            _category("dining", "Dining Out", "Fun", activity=dining),
        ])
        for _ in range(12)
    ]


def _profile(**overrides) -> Profile:
    fields = dict(
        essential_categories=["Rent", "Utilities"],
        college_categories=["Tuition"],
        goals={
            "safety_net": Goal(category="Safety Net"),
            "car": Goal(category="Next Car", amount=20_000_000, by=date(2028, 6, 1)),
            "school": Goal(category="School", amount=40_000_000, by=date(2030, 9, 1)),
        },
        goal_group="Goals",
    )
    fields.update(overrides)
    return Profile(**fields)


def _snapshot(profile, accounts=None) -> Snapshot:
    return Snapshot(
        month=TODAY.replace(day=1),
        month_detail=_month(_current_categories()),
        accounts=accounts or [],
        transactions=[],
        profile=profile,
        today=TODAY,
        since=date(2026, 6, 7),
    )


def _run(args, profile, groups=None, accounts=None, regroups=None, loan_txs=None):
    """Invoke `ynab money goals` against a mocked budget; returns (result, client mock)."""
    with patch("ynab_tools.money.goals.load_snapshot") as load, \
            patch("ynab_tools.money.goals.client") as client:
        load.return_value = _snapshot(profile, accounts)
        client.get_month_history.return_value = _history()
        current = groups if groups is not None else [_group("Housing", [])]
        client.list_categories.side_effect = [current, regroups if regroups is not None else current]
        client.list_transactions_by_account.return_value = loan_txs or []
        client.create_category_group.return_value = _group("Goals", [], gid="new-group")
        client.create_category.side_effect = lambda name, group_id, **kw: _category(
            f"new-{name}", name, "Goals")
        result = runner.invoke(app, ["money", "goals", *args])
    return result, client


def _existing_goals(goal_type="NEED", **targets):
    """A "Goals" group already holding all three goal categories."""
    return [_group("Goals", [
        _category("sn", "Safety Net", "Goals", goal_type=goal_type,
                  goal_target=targets.get("sn"), goal_target_date=targets.get("sn_by")),
        _category("car", "Next Car", "Goals", goal_type=goal_type,
                  goal_target=targets.get("car"), goal_target_date=targets.get("car_by")),
        _category("school", "School", "Goals", goal_type=goal_type,
                  goal_target=targets.get("school"), goal_target_date=targets.get("school_by")),
    ], gid="goals-group")]


# ── Pure sizing ──────────────────────────────────────────────────────────────


def test_essential_average_of_4000_gives_24000_safety_net():
    essentials = resolve_essentials(_profile(), _current_categories())

    monthly = average_monthly_spending(_history(), [c.id for c in essentials.categories])

    assert monthly == 4_000_000
    assert safety_net_target(monthly, 6) == 24_000_000


def test_unmatched_essential_name_is_reported_and_left_out():
    essentials = resolve_essentials(_profile(essential_categories=["Rent", "Moat Upkeep"]),
                                    _current_categories())

    assert [c.id for c in essentials.categories] == ["rent"]
    assert essentials.unmatched == ["Moat Upkeep"]
    assert average_monthly_spending(_history(), ["rent"]) == 3_000_000


def test_no_essential_list_proposes_set_from_group_keywords():
    essentials = resolve_essentials(_profile(essential_categories=[]), _current_categories())

    assert essentials.estimate is True
    # Housing and Utilities by keyword, College by the configured college categories.
    assert sorted(c.id for c in essentials.categories) == ["power", "rent", "tuition"]


def test_car_estimate_uses_most_recent_auto_loan_opening_balance():
    older = starting_balance([_starting_balance_tx(-18_000_000, date(2019, 3, 1))])
    newer = starting_balance([_starting_balance_tx(-26_000_000, date(2023, 5, 1))])

    amount, basis = car_estimate([("Old Car Loan", *older), ("Newer Car Loan", *newer)])

    assert amount == 26_000_000
    assert "Newer Car Loan" in basis


def test_months_out_handles_leap_day():
    assert months_out(date(2028, 2, 29), 12) == date(2029, 2, 28)
    assert months_out(TODAY, 12) == NEXT_YEAR


# ── Command ──────────────────────────────────────────────────────────────────


def test_preview_json_matches_answer_shape_and_makes_no_writes():
    result, client = _run(["--json"], _profile())

    assert result.exit_code == 0, result.output
    answer = json.loads(result.stdout)
    assert set(answer) == {
        "command", "month", "units", "status", "figures", "rules_applied",
        "excluded", "warnings", "ui_actions", "next_actions",
    }
    assert answer["command"] == "money goals"
    assert answer["figures"]["essential_monthly"]["value"] == 4_000_000
    assert answer["figures"]["safety_net_target"]["value"] == 24_000_000
    assert answer["figures"]["safety_net_target"]["inputs"]["action"] == "create"
    client.create_category_group.assert_not_called()
    client.create_category.assert_not_called()
    client.update_category.assert_not_called()


def test_apply_with_missing_group_creates_group_then_categories_with_target_and_date():
    result, client = _run(["--apply", "--json"], _profile())

    assert result.exit_code == 0, result.output
    client.create_category_group.assert_called_once_with("Goals", budget_id=None)
    assert client.create_category.call_args_list == [
        call("Safety Net", "new-group", budget_id=None,
             goal_target=24_000_000, goal_target_date=NEXT_YEAR),
        call("Next Car", "new-group", budget_id=None,
             goal_target=20_000_000, goal_target_date=date(2028, 6, 1)),
        call("School", "new-group", budget_id=None,
             goal_target=40_000_000, goal_target_date=date(2030, 9, 1)),
    ]
    names = [c[0] for c in client.mock_calls]
    assert names.index("create_category_group") < names.index("create_category")


def test_car_without_amount_is_estimate_from_auto_loan():
    """Covers AE4."""
    profile = _profile(goals={"car": Goal(category="Next Car")})
    loan = _account("loan-1", "Car Loan", "autoLoan")

    result, _ = _run(["--json"], profile, accounts=[loan],
                     loan_txs=[_starting_balance_tx(-25_000_000, date(2022, 4, 1))])

    answer = json.loads(result.stdout)
    car = answer["figures"]["car_target"]
    assert car["value"] == 25_000_000
    assert car["estimate"] is True
    assert car["inputs"]["by"] == NEXT_YEAR.isoformat()
    assert any("auto-loan" in r and "Car Loan" in r for r in answer["rules_applied"])


def test_unmatched_essential_name_produces_warning():
    result, _ = _run(["--json"], _profile(essential_categories=["Rent", "Moat Upkeep"]))

    answer = json.loads(result.stdout)
    codes = [w["code"] for w in answer["warnings"]]
    assert "unmatched_essential" in codes
    assert answer["figures"]["safety_net_target"]["value"] == 18_000_000
    assert answer["status"] == "approximate"


def test_no_essential_list_flags_safety_net_as_estimate():
    result, _ = _run(["--json"], _profile(essential_categories=[]))

    answer = json.loads(result.stdout)
    assert answer["figures"]["essential_monthly"]["estimate"] is True
    assert answer["figures"]["safety_net_target"]["estimate"] is True
    assert any("keyword" in r for r in answer["rules_applied"])


def test_existing_goal_category_is_updated_not_duplicated():
    result, client = _run(["--apply", "--json"], _profile(), groups=_existing_goals())

    assert result.exit_code == 0, result.output
    client.create_category_group.assert_not_called()
    client.create_category.assert_not_called()
    client.update_category.assert_any_call(
        "sn", {"goal_target": 24_000_000, "goal_target_date": NEXT_YEAR}, budget_id=None)


def test_goal_still_need_after_apply_gets_ui_action_naming_target_type():
    result, _ = _run(["--apply", "--json"], _profile(), groups=_existing_goals())

    answer = json.loads(result.stdout)
    actions = {a["category"]: a for a in answer["ui_actions"]}
    assert set(actions) == {"Safety Net", "Next Car", "School"}
    assert actions["Safety Net"]["current_goal_type"] == "NEED"
    assert actions["Safety Net"]["set"]["goal_type"] == "TBD"


def test_second_apply_writes_nothing_when_targets_already_set():
    done = _existing_goals(
        goal_type="TBD",
        sn=24_000_000, sn_by=NEXT_YEAR,
        car=20_000_000, car_by=date(2028, 6, 1),
        school=40_000_000, school_by=date(2030, 9, 1),
    )

    result, client = _run(["--apply", "--json"], _profile(), groups=done)

    assert result.exit_code == 0, result.output
    client.create_category_group.assert_not_called()
    client.create_category.assert_not_called()
    client.update_category.assert_not_called()
    assert json.loads(result.stdout)["ui_actions"] == []


def test_emergency_fund_target_reported_unused():
    result, _ = _run(["--json"], _profile(emergency_fund_target=10000))

    answer = json.loads(result.stdout)
    assert any(e["item"] == "emergency_fund_target" for e in answer["excluded"])


def test_preview_renders_without_json():
    result, _ = _run([], _profile())

    assert result.exit_code == 0, result.output
    assert "Safety Net" in result.stdout
    assert "--apply" in result.stdout


def test_defaulted_date_keeps_the_date_already_on_the_category():
    """A later run must not push a defaulted goal date another 12 months out."""
    earlier = date(2027, 3, 1)
    done = _existing_goals(
        goal_type="TBD",
        sn=24_000_000, sn_by=earlier,
        car=20_000_000, car_by=date(2028, 6, 1),
        school=40_000_000, school_by=date(2030, 9, 1),
    )

    result, client = _run(["--apply", "--json"], _profile(), groups=done)

    answer = json.loads(result.stdout)
    assert answer["figures"]["safety_net_target"]["inputs"]["by"] == earlier.isoformat()
    assert answer["figures"]["safety_net_target"]["estimate"] is True
    client.update_category.assert_not_called()


def test_apply_reports_actions_taken():
    result, _ = _run(["--apply", "--json"], _profile())

    answer = json.loads(result.stdout)
    assert answer["figures"]["car_target"]["inputs"]["action"] == "created"
