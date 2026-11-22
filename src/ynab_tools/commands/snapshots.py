"""Budget snapshots: save the plan and account balances, and diff two of them.

A snapshot file is JSON with amounts in integer milliunits:

    {"taken_at", "label", "budget_id",
     "months": {"YYYY-MM": {"ready_to_assign", "income", "budgeted", "activity",
                            "categories": {id: {"name", "group", "budgeted", "activity", "balance"}}}},
     "accounts": {id: {"name", "type", "on_budget", "closed", "balance", "cleared_balance"}}}
"""

import json
import os
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .. import config
from ..api import client
from ..utils.formatting import err_console, format_amount, format_amount_colored, print_json

app = typer.Typer(help="Save budget snapshots and diff them.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")

MONTH_FIELDS = {"ready_to_assign": "to_be_budgeted", "income": "income", "budgeted": "budgeted"}


# ── Reading and storing ──────────────────────────────────────────────────────


def read_budget(months: int, label: str | None, taken: datetime, budget_id: str | None = None) -> dict:
    """Read the current month, the `months` full months before it, and the accounts.

    Makes `months + 2` API requests. Deleted categories and accounts are left
    out. Returns the snapshot dict described in the module docstring. Raises
    `RuntimeError` when no API key or budget is configured.
    """
    current = taken.date().replace(day=1).isoformat()
    month_details = [client.get_month(current, budget_id=budget_id)]
    month_details += client.get_month_history(current, months, budget_id=budget_id)
    accounts = client.list_accounts(budget_id=budget_id)
    return {
        "taken_at": taken.isoformat(timespec="seconds"),
        "label": label,
        "budget_id": client._budget_id(budget_id),
        "months": {str(m.month)[:7]: _month_entry(m) for m in month_details},
        "accounts": {
            str(a.id): {
                "name": a.name,
                "type": getattr(a.type, "value", a.type),
                "on_budget": a.on_budget,
                "closed": a.closed,
                "balance": a.balance,
                "cleared_balance": a.cleared_balance,
            }
            for a in accounts if not a.deleted
        },
    }


def _month_entry(m) -> dict:
    """One month detail as a snapshot month entry, without deleted categories."""
    return {
        "ready_to_assign": m.to_be_budgeted,
        "income": m.income,
        "budgeted": m.budgeted,
        "activity": m.activity,
        "categories": {
            str(c.id): {
                "name": c.name,
                "group": c.category_group_name,
                "budgeted": c.budgeted,
                "activity": c.activity,
                "balance": c.balance,
            }
            for c in m.categories if not c.deleted
        },
    }


def write_snapshot(data: dict, taken: datetime) -> Path:
    """Write `data` to `SNAPSHOT_DIR/YYYY-MM-DDTHHMMSS.json` with mode 600.

    Creates the directory if needed. Raises `FileExistsError` rather than
    overwrite a snapshot taken in the same second.
    """
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SNAPSHOT_DIR / f"{taken.strftime('%Y-%m-%dT%H%M%S')}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def saved_snapshots() -> list[Path]:
    """Every saved snapshot file, newest first (file stems sort by time taken)."""
    if not config.SNAPSHOT_DIR.exists():
        return []
    return sorted(config.SNAPSHOT_DIR.glob("*.json"), key=lambda p: p.stem, reverse=True)


def resolve_snapshot(ref: str, paths: list[Path]) -> Path:
    """The snapshot `ref` names among `paths` (newest first).

    `ref` is `latest`, `previous` (the one before latest) or a file-stem
    prefix. Raises `ValueError` when nothing matches or a prefix matches more
    than one snapshot; the message lists the candidates.
    """
    if ref in ("latest", "previous"):
        index = 0 if ref == "latest" else 1
        if len(paths) <= index:
            raise ValueError(f"'{ref}' needs {index + 1} saved snapshot(s); found {len(paths)}")
        return paths[index]
    matches = [p for p in paths if p.stem.startswith(ref)]
    if not matches:
        raise ValueError(f"No snapshot matches '{ref}'")
    if len(matches) > 1:
        names = "\n".join(f"  {p.stem}" for p in matches)
        raise ValueError(f"'{ref}' matches {len(matches)} snapshots — pass a longer prefix:\n{names}")
    return matches[0]


def _read(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


# ── Comparison ───────────────────────────────────────────────────────────────


def _change(old: int, new: int) -> dict:
    return {"old": old, "new": new, "delta": new - old}


def diff_snapshots(old: dict, new: dict) -> dict:
    """What changed from snapshot `old` to snapshot `new`; only non-zero changes.

    Months are compared only where both snapshots hold them (`months_compared`).
    Returns `months` (month, field, old, new, delta for ready_to_assign, income
    and budgeted), `categories` (per month balance changes with id, group and
    name, largest |delta| first), `accounts` (balance changes, largest |delta|
    first), and `categories_added`/`categories_removed`/`accounts_added`/
    `accounts_removed` for ids found in only one snapshot (categories across
    all of its months).
    """
    common = sorted(old["months"].keys() & new["months"].keys(), reverse=True)
    months, categories = [], []
    for month in common:
        o, n = old["months"][month], new["months"][month]
        for name in MONTH_FIELDS:
            if o.get(name, 0) != n.get(name, 0):
                months.append({"month": month, "field": name, **_change(o.get(name, 0), n.get(name, 0))})
        o_cats, n_cats = o.get("categories", {}), n.get("categories", {})
        for cid in o_cats.keys() & n_cats.keys():
            before, after = o_cats[cid]["balance"], n_cats[cid]["balance"]
            if before != after:
                categories.append({
                    "month": month, "id": cid, "group": n_cats[cid]["group"], "name": n_cats[cid]["name"],
                    **_change(before, after),
                })
    categories.sort(key=lambda c: (-abs(c["delta"]), c["month"], c["name"]))

    o_all, n_all = _all_categories(old), _all_categories(new)
    o_acct, n_acct = old.get("accounts", {}), new.get("accounts", {})
    accounts = [
        {"id": aid, "name": n_acct[aid]["name"], **_change(o_acct[aid]["balance"], n_acct[aid]["balance"])}
        for aid in o_acct.keys() & n_acct.keys()
        if o_acct[aid]["balance"] != n_acct[aid]["balance"]
    ]
    accounts.sort(key=lambda a: (-abs(a["delta"]), a["name"]))

    def _cats(ids: set, source: dict) -> list[dict]:
        rows = [{"id": cid, "group": source[cid]["group"], "name": source[cid]["name"]} for cid in ids]
        return sorted(rows, key=lambda c: (c["group"] or "", c["name"]))

    def _accts(ids: set, source: dict) -> list[dict]:
        rows = [{"id": aid, "name": source[aid]["name"], "balance": source[aid]["balance"]} for aid in ids]
        return sorted(rows, key=lambda a: a["name"])

    return {
        "months_compared": common,
        "months": months,
        "categories": categories,
        "accounts": accounts,
        "categories_added": _cats(n_all.keys() - o_all.keys(), n_all),
        "categories_removed": _cats(o_all.keys() - n_all.keys(), o_all),
        "accounts_added": _accts(n_acct.keys() - o_acct.keys(), n_acct),
        "accounts_removed": _accts(o_acct.keys() - n_acct.keys(), o_acct),
    }


def _all_categories(snapshot: dict) -> dict:
    """Category id -> its entry from the newest month that holds it."""
    found: dict = {}
    for month in sorted(snapshot["months"], reverse=True):
        for cid, c in snapshot["months"][month].get("categories", {}).items():
            found.setdefault(cid, c)
    return found


def _month_range(snapshot: dict) -> list[str]:
    keys = sorted(snapshot.get("months", {}))
    return [keys[0], keys[-1]] if keys else []


# ── Commands ─────────────────────────────────────────────────────────────────


@app.command("save")
def save(
    months: int = typer.Option(12, "--months", min=0, help="Full months before the current one to include"),
    label: str | None = typer.Option(None, "--label", help="A note saying why the snapshot was taken"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Save the budget's months, categories and account balances to a snapshot file."""
    taken = datetime.now()
    err_console.print(f"[dim]Reading {months + 1} months and the accounts…[/dim]")
    try:
        data = read_budget(months, label, taken, budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    try:
        path = write_snapshot(data, taken)
    except FileExistsError as e:
        console.print(f"[red]Snapshot {e.filename} already exists; not overwriting it.[/red]")
        raise typer.Exit(1)

    if json_output:
        print_json({"path": str(path), "months": list(data["months"])})
        return
    console.print(f"[green]✓[/green] Saved {path}", soft_wrap=True)


@app.command("list")
def list_saved(json_output: bool = JsonFlag):
    """List saved snapshots, newest first."""
    rows = []
    for path in saved_snapshots():
        data = _read(path)
        rows.append({
            "name": path.stem,
            "taken_at": data.get("taken_at"),
            "label": data.get("label"),
            "months": _month_range(data),
        })

    if json_output:
        print_json(rows)
        return
    if not rows:
        console.print("[dim]No snapshots saved. Run: ynab snapshot save[/dim]")
        return

    table = Table(title=f"Snapshots ({len(rows)})")
    table.add_column("Name")
    table.add_column("Taken at", style="dim")
    table.add_column("Label")
    table.add_column("Months")
    for r in rows:
        table.add_row(r["name"], r["taken_at"] or "—", r["label"] or "—", " – ".join(r["months"]) or "—")
    console.print(table)


@app.command("diff")
def diff(
    old_ref: str = typer.Argument(..., metavar="OLD", help="Snapshot name prefix, 'latest' or 'previous'"),
    new_ref: str | None = typer.Argument(
        None, metavar="[NEW]", help="Snapshot to compare with; omitted compares with the budget now"
    ),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Show what changed between two snapshots, or between one and the budget now.

    Without NEW, reads the budget live (as many past months as OLD holds) and
    writes nothing.
    """
    paths = saved_snapshots()
    try:
        old_path = resolve_snapshot(old_ref, paths)
        new_path = resolve_snapshot(new_ref, paths) if new_ref else None
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    old = _read(old_path)
    if new_path:
        new = _read(new_path)
    else:
        count = max(len(old["months"]) - 1, 0)
        err_console.print(f"[dim]Reading {count + 1} months and the accounts…[/dim]")
        try:
            new = read_budget(count, None, datetime.now(), budget_id=budget)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

    changes = diff_snapshots(old, new)
    meta = {
        "old": {"path": str(old_path), "taken_at": old.get("taken_at"), "label": old.get("label")},
        "new": {"path": str(new_path) if new_path else None, "taken_at": new.get("taken_at"),
                "label": new.get("label")},
    }

    if json_output:
        print_json({**meta, **changes})
        return
    _print_diff(old_path.stem, new_path.stem if new_path else "now", changes)


def _print_diff(old_name: str, new_name: str, changes: dict) -> None:
    """Render `diff_snapshots` output as Rich tables."""
    console.print(f"[bold]{old_name} → {new_name}[/bold]")
    shown = False

    if changes["months"]:
        shown = True
        table = Table(title="Month totals")
        for col in ("Month", "Figure", "Old", "New", "Change"):
            table.add_column(col, justify="right" if col in ("Old", "New", "Change") else "left")
        for m in changes["months"]:
            table.add_row(m["month"], m["field"].replace("_", " "), format_amount(m["old"]),
                          format_amount(m["new"]), format_amount_colored(m["delta"]))
        console.print(table)

    if changes["categories"]:
        shown = True
        table = Table(title="Category balances")
        for col in ("Month", "Group", "Category", "Old", "New", "Change"):
            table.add_column(col, justify="right" if col in ("Old", "New", "Change") else "left")
        for c in changes["categories"]:
            table.add_row(c["month"], c["group"] or "—", c["name"], format_amount(c["old"]),
                          format_amount(c["new"]), format_amount_colored(c["delta"]))
        console.print(table)

    if changes["accounts"]:
        shown = True
        table = Table(title="Account balances")
        for col in ("Account", "Old", "New", "Change"):
            table.add_column(col, justify="right" if col != "Account" else "left")
        for a in changes["accounts"]:
            table.add_row(a["name"], format_amount(a["old"]), format_amount(a["new"]),
                          format_amount_colored(a["delta"]))
        console.print(table)

    for key, title in (("categories_added", "Categories added"), ("categories_removed", "Categories removed")):
        if changes[key]:
            shown = True
            console.print(f"\n[bold]{title}[/bold]")
            for c in changes[key]:
                console.print(f"  {c['group'] or '—'} / {c['name']}")
    for key, title in (("accounts_added", "Accounts added"), ("accounts_removed", "Accounts removed")):
        if changes[key]:
            shown = True
            console.print(f"\n[bold]{title}[/bold]")
            for a in changes[key]:
                console.print(f"  {a['name']}  {format_amount(a['balance'])}")

    if not shown:
        console.print(f"[green]No changes[/green] across {len(changes['months_compared'])} month(s).")
