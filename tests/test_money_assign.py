"""Tests for `ynab budget assign`: preview a plan, then apply only that reviewed plan (KTD9)."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.config import Goal, Profile
from ynab_tools.money.assign import propose
from ynab_tools.money.snapshot import Snapshot

runner = CliRunner()

TODAY = date(2026, 10, 5)
MONTH = "2026-10-01"


def _account(aid, balance=0, acct_type="checking"):
    a = MagicMock()
    a.id = aid
    a.name = aid
    a.type = acct_type
    a.balance = balance
    a.on_budget = True
    a.closed = False
    a.deleted = False
    a.direct_import_linked = False
    a.direct_import_in_error = False
    a.last_reconciled_at = (TODAY - timedelta(days=3)).isoformat()
    return a


def _category(cid, name, group, balance=0, budgeted=0, goal_under_funded=0, goal_type=None,
              goal_target=None, goal_overall_left=None):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.category_group_name = group
    c.balance = balance
    c.budgeted = budgeted
    c.hidden = False
    c.deleted = False
    c.goal_under_funded = goal_under_funded
    c.goal_type = goal_type
    c.goal_target = goal_target
    c.goal_overall_left = goal_overall_left
    c.goal_target_date = date(2027, 10, 1) if goal_type else None
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


def _snapshot(rta, categories, accounts=None, profile=None) -> Snapshot:
    return Snapshot(
        month=TODAY.replace(day=1),
        month_detail=_month(rta, categories),
        accounts=accounts if accounts is not None else [_account("checking-a", 10_000_000)],
        transactions=[],
        profile=profile or _profile(),
        today=TODAY,
        since=TODAY - timedelta(days=120),
    )


def _four_steps_snapshot(**kw) -> Snapshot:
    """RTA $1,000. Card owes $300 with $100 set aside; Groceries needs $150 this month;
    the Safety Net (TBD) needs $400 more; Next Car is still a NEED goal; School has no category."""
    return _snapshot(1_000_000, [
        _category("safety", "Safety Net", "Goals", balance=100_000, budgeted=100_000,
                  goal_type="TBD", goal_target=500_000, goal_overall_left=400_000),
        _category("car", "Next Car", "Goals", balance=0, goal_type="NEED",
                  goal_target=2_000_000, goal_overall_left=2_000_000, goal_under_funded=50_000),
        _category("groc", "Groceries", "Food", balance=50_000, budgeted=50_000, goal_under_funded=150_000),
        _category("visa-pay", "Visa", "Credit Card Payments", balance=100_000),
    ], accounts=[_account("checking-a", 10_000_000), _account("Visa", -300_000, "creditCard")], **kw)


def _items(answer):
    return [(i["category"], i["step"], i["from"], i["to"]) for i in answer["items"]]


# ── Preview ──────────────────────────────────────────────────────────────────


def test_steps_run_card_then_month_needs_then_goals_with_from_and_to():
    answer = propose(_four_steps_snapshot())

    assert _items(answer) == [
        ("Visa", "cc_mismatch", 0, 200_000),
        ("Groceries", "underfunded", 50_000, 200_000),
        ("Safety Net", "goal", 100_000, 500_000),
    ]
    assert answer["month"] == MONTH
    assert answer["figures"]["total"]["value"] == 750_000
    assert answer["figures"]["remaining"]["value"] == 250_000


def test_need_goal_and_missing_goal_category_are_excluded_with_a_reason():
    answer = propose(_four_steps_snapshot())

    excluded = {e["item"]: e["reason"] for e in answer["excluded"]}
    assert "NEED" in excluded["Next Car"]
    assert "School" in excluded
    assert "Next Car" not in [i["category"] for i in answer["items"]]


def test_overspending_comes_first_and_priority_orders_within_a_step_when_rta_is_short():
    """RTA $100: Hobbies and Rent are each overspent by $80 and the Safety Net needs $80.

    Overspending is covered before goals whatever the priority says; within
    overspending, the profile's priority picks Rent over Hobbies.
    """
    snapshot = _snapshot(100_000, [
        _category("hob", "Hobbies", "Lifestyle", balance=-80_000),
        _category("rent", "Rent", "Housing", balance=-80_000, budgeted=1_000_000),
        _category("safety", "Safety Net", "Goals", goal_type="TBD", goal_target=80_000, goal_overall_left=80_000),
    ], profile=_profile(funding_priority=["Goals", "Housing", "Lifestyle"]))

    answer = propose(snapshot)

    assert _items(answer) == [("Rent", "overspent", 1_000_000, 1_080_000)]
    skipped = {e["item"] for e in answer["excluded"] if "Ready to Assign" in e["reason"]}
    assert skipped == {"Hobbies", "Safety Net"}


def test_negative_rta_gives_no_items_and_a_shortfall():
    snapshot = _snapshot(-120_000, [_category("hob", "Hobbies", "Lifestyle", balance=-80_000)])

    answer = propose(snapshot)

    assert answer["items"] == []
    assert answer["figures"]["shortfall"]["value"] == 120_000


def test_preview_writes_nothing_and_json_carries_the_plan():
    with patch("ynab_tools.money.assign.load_snapshot", return_value=_four_steps_snapshot()), \
            patch("ynab_tools.money.assign.client") as client:
        result = runner.invoke(app, ["budget", "assign", "--json"])

    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["command"] == "budget assign"
    assert {"category_id", "category", "step", "from", "to"} <= set(plan["items"][0])
    client.update_month_category.assert_not_called()


def test_plan_without_apply_or_apply_without_plan_is_refused(tmp_path):
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"month": MONTH, "items": []}))
    with patch("ynab_tools.money.assign.load_snapshot", return_value=_four_steps_snapshot()), \
            patch("ynab_tools.money.assign.client") as client:
        no_apply = runner.invoke(app, ["budget", "assign", "--plan", str(plan_file)])
        no_plan = runner.invoke(app, ["budget", "assign", "--apply"])

    assert no_apply.exit_code != 0
    assert no_plan.exit_code != 0
    client.update_month_category.assert_not_called()


# ── Apply ────────────────────────────────────────────────────────────────────


def _plan_file(tmp_path, items):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"month": MONTH, "items": items}))
    return path


def _item(cid, name, frm, to, step="overspent"):
    return {"category_id": cid, "category": name, "step": step, "from": frm, "to": to}


def _apply(plan_file, rta, budgeted: dict, update_side_effect=None):
    """Run `--apply --plan` against a month whose categories hold `budgeted` (id -> amount)."""
    month = _month(rta, [_category(cid, cid, "G", budgeted=b) for cid, b in budgeted.items()])
    with patch("ynab_tools.money.assign.client") as client:
        client.get_month.return_value = month
        if update_side_effect is not None:
            client.update_month_category.side_effect = update_side_effect
        result = runner.invoke(app, ["budget", "assign", "--apply", "--plan", str(plan_file), "--json"])
    return result, client


def test_apply_the_preview_unchanged_writes_each_absolute_to(tmp_path):
    with patch("ynab_tools.money.assign.load_snapshot", return_value=_four_steps_snapshot()), \
            patch("ynab_tools.money.assign.client"):
        preview = runner.invoke(app, ["budget", "assign", "--json"])
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(preview.stdout)

    result, client = _apply(plan_file, 1_000_000, {"visa-pay": 0, "groc": 50_000, "safety": 100_000})

    assert result.exit_code == 0, result.output
    client.get_month.assert_called_once_with(MONTH, budget_id=None)
    writes = [c.args[:3] for c in client.update_month_category.call_args_list]
    assert writes == [(MONTH, "visa-pay", 200_000), (MONTH, "groc", 200_000), (MONTH, "safety", 500_000)]
    answer = json.loads(result.stdout)
    assert answer["outcome"] == "applied"
    assert [i["outcome"] for i in answer["items"]] == ["written"] * 3


def test_applying_the_same_plan_again_writes_nothing(tmp_path):
    plan_file = _plan_file(tmp_path, [_item("rent", "Rent", 0, 80_000), _item("hob", "Hobbies", 10_000, 90_000)])

    result, client = _apply(plan_file, 0, {"rent": 80_000, "hob": 90_000})

    assert result.exit_code == 0, result.output
    client.update_month_category.assert_not_called()
    answer = json.loads(result.stdout)
    assert [i["outcome"] for i in answer["items"]] == ["already_applied"] * 2


def test_ae7_changed_category_makes_the_plan_stale_and_nothing_is_written(tmp_path):
    """Rent was assigned $0 at preview; it now holds $30, which is neither $0 nor $80."""
    plan_file = _plan_file(tmp_path, [_item("hob", "Hobbies", 0, 80_000), _item("rent", "Rent", 0, 80_000)])

    result, client = _apply(plan_file, 500_000, {"hob": 0, "rent": 30_000})

    assert result.exit_code != 0
    client.update_month_category.assert_not_called()
    answer = json.loads(result.stdout)
    assert answer["status"] == "stale_plan"
    stale = [i for i in answer["items"] if i["outcome"] == "stale"]
    assert [(i["category"], i["current"]) for i in stale] == [("Rent", 30_000)]


def test_edited_plan_over_ready_to_assign_is_refused_before_any_write(tmp_path):
    """RTA $100; the owner raised Rent's `to` so the plan now needs $150."""
    plan_file = _plan_file(tmp_path, [_item("rent", "Rent", 0, 150_000)])

    result, client = _apply(plan_file, 100_000, {"rent": 0})

    assert result.exit_code != 0
    client.update_month_category.assert_not_called()
    answer = json.loads(result.stdout)
    assert answer["outcome"] == "refused"
    assert answer["figures"]["pending"]["value"] == 150_000


def test_write_failing_partway_reports_written_and_not_written(tmp_path):
    plan_file = _plan_file(tmp_path, [
        _item("a", "A", 0, 10_000), _item("b", "B", 0, 20_000), _item("c", "C", 0, 30_000),
    ])

    result, client = _apply(plan_file, 100_000, {"a": 0, "b": 0, "c": 0},
                            update_side_effect=[MagicMock(), RuntimeError("rate limited"), MagicMock()])

    assert result.exit_code != 0
    assert client.update_month_category.call_count == 2
    answer = json.loads(result.stdout)
    assert answer["outcome"] == "partial"
    assert [(i["category"], i["outcome"]) for i in answer["items"]] == [
        ("A", "written"), ("B", "failed"), ("C", "not_written"),
    ]
    assert "rate limited" in answer["items"][1]["error"]
