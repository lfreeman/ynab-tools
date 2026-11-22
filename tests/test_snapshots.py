"""Tests for `ynab snapshot`: saving budget snapshots and diffing them."""

import json
import stat
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from ynab_tools.cli import app
from ynab_tools.commands.snapshots import (
    diff_snapshots,
    resolve_snapshot,
    saved_snapshots,
    write_snapshot,
)

runner = CliRunner()


def _category(cid, name, group, budgeted=0, activity=0, balance=0, deleted=False):
    c = MagicMock()
    c.id = cid
    c.name = name
    c.category_group_name = group
    c.budgeted = budgeted
    c.activity = activity
    c.balance = balance
    c.deleted = deleted
    return c


def _month(on, categories, rta=0, income=0, budgeted=0, activity=0):
    m = MagicMock()
    m.month = on
    m.to_be_budgeted = rta
    m.income = income
    m.budgeted = budgeted
    m.activity = activity
    m.categories = categories
    return m


def _account(aid, name, balance, acct_type="checking", deleted=False):
    a = MagicMock()
    a.id = aid
    a.name = name
    a.type = acct_type
    a.on_budget = True
    a.closed = False
    a.balance = balance
    a.cleared_balance = balance
    a.deleted = deleted
    return a


@pytest.fixture
def snap_dir(monkeypatch, tmp_path):
    d = tmp_path / "snapshots"
    monkeypatch.setattr("ynab_tools.config.SNAPSHOT_DIR", d)
    return d


def _mock_reads(mock_client):
    mock_client._budget_id.return_value = "budget-1"
    mock_client.get_month.return_value = _month(
        date(2026, 10, 1),
        [
            _category("cat-groceries", "Groceries", "Everyday", 400_000, -150_000, 250_000),
            _category("cat-gone", "Old Thing", "Everyday", deleted=True),
        ],
        rta=50_000, income=3_000_000, budgeted=2_950_000, activity=-150_000,
    )
    mock_client.get_month_history.return_value = [
        _month(date(2026, 9, 1), [_category("cat-groceries", "Groceries", "Everyday", 400_000, -390_000, 10_000)]),
        _month(date(2026, 8, 1), [_category("cat-groceries", "Groceries", "Everyday", 400_000, -400_000, 0)]),
    ]
    mock_client.list_accounts.return_value = [
        _account("acct-checking", "Main Checking", 1_200_000),
        _account("acct-closed", "Gone Account", 0, deleted=True),
    ]


# ── save ─────────────────────────────────────────────────────────────────────


@patch("ynab_tools.commands.snapshots.client")
def test_save_writes_expected_structure(mock_client, snap_dir):
    _mock_reads(mock_client)

    result = runner.invoke(app, ["snapshot", "save", "--months", "2", "--label", "before fixes", "--json"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["months"] == ["2026-10", "2026-09", "2026-08"]
    mock_client.get_month_history.assert_called_once()
    assert mock_client.get_month_history.call_args.args[1] == 2

    files = list(snap_dir.glob("*.json"))
    assert [str(f) for f in files] == [out["path"]]
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    data = json.loads(files[0].read_text())
    assert data["label"] == "before fixes"
    assert data["budget_id"] == "budget-1"
    assert datetime.fromisoformat(data["taken_at"])
    assert data["months"]["2026-10"] == {
        "ready_to_assign": 50_000,
        "income": 3_000_000,
        "budgeted": 2_950_000,
        "activity": -150_000,
        "categories": {
            "cat-groceries": {
                "name": "Groceries", "group": "Everyday",
                "budgeted": 400_000, "activity": -150_000, "balance": 250_000,
            },
        },
    }
    assert data["accounts"] == {
        "acct-checking": {
            "name": "Main Checking", "type": "checking", "on_budget": True, "closed": False,
            "balance": 1_200_000, "cleared_balance": 1_200_000,
        },
    }


def test_write_snapshot_refuses_to_overwrite(snap_dir):
    taken = datetime(2026, 10, 8, 9, 30, 15)
    path = write_snapshot({"label": "first"}, taken)
    assert path.name == "2026-10-08T093015.json"

    with pytest.raises(FileExistsError):
        write_snapshot({"label": "second"}, taken)
    assert json.loads(path.read_text()) == {"label": "first"}


# ── list / resolution ────────────────────────────────────────────────────────


def _saved(snap_dir, stem, label=None, months=("2026-10", "2026-09")):
    snap_dir.mkdir(parents=True, exist_ok=True)
    data = {"taken_at": stem, "label": label, "budget_id": "budget-1",
            "months": {m: {} for m in months}, "accounts": {}}
    (snap_dir / f"{stem}.json").write_text(json.dumps(data))


def test_latest_and_previous_resolve_newest_first(snap_dir):
    _saved(snap_dir, "2026-10-01T080000")
    _saved(snap_dir, "2026-10-08T090000")
    _saved(snap_dir, "2026-10-05T120000")
    paths = saved_snapshots()

    assert resolve_snapshot("latest", paths).stem == "2026-10-08T090000"
    assert resolve_snapshot("previous", paths).stem == "2026-10-05T120000"
    assert resolve_snapshot("2026-10-01", paths).stem == "2026-10-01T080000"
    with pytest.raises(ValueError, match="2 snapshots"):
        resolve_snapshot("2026-10-0", paths[:2])
    with pytest.raises(ValueError):
        resolve_snapshot("2025", paths)


def test_previous_needs_two_snapshots(snap_dir):
    _saved(snap_dir, "2026-10-01T080000")
    with pytest.raises(ValueError):
        resolve_snapshot("previous", saved_snapshots())


def test_list_json_is_newest_first(snap_dir):
    _saved(snap_dir, "2026-10-01T080000", label="old")
    _saved(snap_dir, "2026-10-08T090000", label="new", months=("2026-10", "2026-09", "2026-08"))

    result = runner.invoke(app, ["snapshot", "list", "--json"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert [r["name"] for r in rows] == ["2026-10-08T090000", "2026-10-01T080000"]
    assert rows[0]["label"] == "new"
    assert rows[0]["months"] == ["2026-08", "2026-10"]


# ── diff ─────────────────────────────────────────────────────────────────────


def _snap(rta, groceries_balance, checking, extra_categories=None, taken_at="t"):
    cats = {"cat-groceries": {"name": "Groceries", "group": "Everyday",
                              "budgeted": 400_000, "activity": 0, "balance": groceries_balance},
            "cat-rent": {"name": "Rent", "group": "Bills",
                         "budgeted": 1_000_000, "activity": 0, "balance": 1_000_000}}
    cats.update(extra_categories or {})
    return {
        "taken_at": taken_at, "label": None, "budget_id": "budget-1",
        "months": {"2026-10": {"ready_to_assign": rta, "income": 3_000_000, "budgeted": 2_000_000,
                               "activity": 0, "categories": cats}},
        "accounts": {"acct-checking": {"name": "Main Checking", "type": "checking", "on_budget": True,
                                       "closed": False, "balance": checking, "cleared_balance": checking}},
    }


def test_diff_reports_only_nonzero_changes_and_additions():
    old = _snap(rta=100_000, groceries_balance=250_000, checking=1_000_000)
    new = _snap(rta=40_000, groceries_balance=310_000, checking=900_000, extra_categories={
        "cat-gifts": {"name": "Gifts", "group": "Everyday", "budgeted": 0, "activity": 0, "balance": 0},
    })

    d = diff_snapshots(old, new)

    assert d["months"] == [
        {"month": "2026-10", "field": "ready_to_assign", "old": 100_000, "new": 40_000, "delta": -60_000},
    ]
    assert d["categories"] == [
        {"month": "2026-10", "id": "cat-groceries", "group": "Everyday", "name": "Groceries",
         "old": 250_000, "new": 310_000, "delta": 60_000},
    ]
    assert d["accounts"] == [
        {"id": "acct-checking", "name": "Main Checking", "old": 1_000_000, "new": 900_000, "delta": -100_000},
    ]
    assert d["categories_added"] == [{"id": "cat-gifts", "group": "Everyday", "name": "Gifts"}]
    assert d["categories_removed"] == []
    assert d["accounts_added"] == []
    assert d["accounts_removed"] == []


def test_diff_sorts_category_changes_by_size():
    old = _snap(0, 0, 0)
    new = _snap(0, 5_000, 0)
    new["months"]["2026-10"]["categories"]["cat-rent"]["balance"] = 900_000

    d = diff_snapshots(old, new)

    assert [c["id"] for c in d["categories"]] == ["cat-rent", "cat-groceries"]


@patch("ynab_tools.commands.snapshots.client")
def test_diff_latest_against_live_json(mock_client, snap_dir):
    _mock_reads(mock_client)
    old = {
        "taken_at": "2026-10-07T090000", "label": "before", "budget_id": "budget-1",
        "months": {
            "2026-10": {"ready_to_assign": 80_000, "income": 3_000_000, "budgeted": 2_920_000,
                        "activity": -150_000, "categories": {}},
            "2026-09": {"ready_to_assign": 0, "income": 0, "budgeted": 0, "activity": 0, "categories": {}},
        },
        "accounts": {},
    }
    snap_dir.mkdir(parents=True)
    (snap_dir / "2026-10-07T090000.json").write_text(json.dumps(old))

    result = runner.invoke(app, ["snapshot", "diff", "latest", "--json"])

    assert result.exit_code == 0, result.output
    out = json.loads(result.stdout)
    assert out["new"]["path"] is None
    assert {"month": "2026-10", "field": "ready_to_assign", "old": 80_000, "new": 50_000,
            "delta": -30_000} in out["months"]
    assert [c["id"] for c in out["categories_added"]] == ["cat-groceries"]
    assert [a["id"] for a in out["accounts_added"]] == ["acct-checking"]
    # Reads as many past months as the old snapshot holds, and writes nothing.
    assert mock_client.get_month_history.call_args.args[1] == 1
    assert len(list(snap_dir.glob("*.json"))) == 1


def test_diff_two_saved_snapshots_table(snap_dir):
    snap_dir.mkdir(parents=True)
    old = _snap(rta=100_000, groceries_balance=250_000, checking=1_000_000)
    new = _snap(rta=40_000, groceries_balance=310_000, checking=900_000)
    (snap_dir / "2026-10-01T080000.json").write_text(json.dumps(old))
    (snap_dir / "2026-10-08T090000.json").write_text(json.dumps(new))

    result = runner.invoke(app, ["snapshot", "diff", "previous", "latest"])

    assert result.exit_code == 0, result.output
    assert "Groceries" in result.output
    assert "Main Checking" in result.output
