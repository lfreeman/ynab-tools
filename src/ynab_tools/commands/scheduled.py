"""Scheduled transaction commands."""

import json
from collections import defaultdict
from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..utils.formatting import format_amount, format_amount_colored

app = typer.Typer(help="Scheduled/recurring transactions.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("list")
def list_scheduled(
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """List all scheduled transactions."""
    try:
        scheduled = client.list_scheduled_transactions(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Filter out deleted
    scheduled = [s for s in scheduled if not s.deleted]

    if json_output:
        console.print(json.dumps([s.to_dict() for s in scheduled], indent=2, default=str))
        return

    if not scheduled:
        console.print("[dim]No scheduled transactions.[/dim]")
        return

    table = Table(title="Scheduled Transactions")
    table.add_column("Payee", min_width=20)
    table.add_column("Amount", justify="right")
    table.add_column("Category")
    table.add_column("Frequency")
    table.add_column("Next Date")
    table.add_column("Account")

    for s in sorted(scheduled, key=lambda x: str(getattr(x, "date_next", "") or "")):
        payee = getattr(s, "payee_name", None) or "—"
        amount = getattr(s, "amount", 0) or 0
        category = getattr(s, "category_name", None) or "—"
        frequency = getattr(s, "frequency", None) or "—"
        date_next = getattr(s, "date_next", None)
        date_str = str(date_next)[:10] if date_next else "—"
        account = getattr(s, "account_name", None) or "—"

        table.add_row(
            payee,
            format_amount_colored(amount),
            category,
            str(frequency),
            date_str,
            account,
        )

    console.print(table)


def _check_scheduled_mismatches(
    budget_id: str | None = None,
) -> list[dict]:
    """Compare scheduled amounts to recent actuals. Returns list of mismatch dicts."""
    scheduled = client.list_scheduled_transactions(budget_id=budget_id)
    scheduled = [s for s in scheduled if not s.deleted]

    if not scheduled:
        return []

    # Get transactions from the last 3 months
    today = date.today()
    three_months_ago = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    three_months_ago = (three_months_ago - timedelta(days=1)).replace(day=1)
    three_months_ago = (three_months_ago - timedelta(days=1)).replace(day=1)
    since_date = three_months_ago.isoformat()

    txs = client.list_transactions(budget_id=budget_id, since_date=since_date)
    txs = [t for t in txs if not t.deleted]

    # Group actual transactions by (payee_name, category_name)
    actuals: dict[tuple[str, str], list[int]] = defaultdict(list)
    for t in txs:
        payee = getattr(t, "payee_name", None) or ""
        category = getattr(t, "category_name", None) or ""
        if payee and not t.transfer_account_id:
            actuals[(payee, category)].append(getattr(t, "amount", 0) or 0)

    results = []
    for s in scheduled:
        payee = getattr(s, "payee_name", None) or ""
        category = getattr(s, "category_name", None) or ""
        sched_amount = getattr(s, "amount", 0) or 0

        key = (payee, category)
        actual_amounts = actuals.get(key, [])

        if not actual_amounts:
            results.append({
                "payee": payee,
                "category": category,
                "scheduled_amount": sched_amount,
                "actual_avg": None,
                "difference": None,
                "status": "NO_DATA",
            })
            continue

        actual_avg = sum(actual_amounts) / len(actual_amounts)

        # Calculate difference percentage relative to scheduled amount
        if sched_amount != 0:
            diff_pct = abs(actual_avg - sched_amount) / abs(sched_amount) * 100
        else:
            diff_pct = 100.0 if actual_avg != 0 else 0.0

        status = "MISMATCH" if diff_pct > 10 else "OK"

        results.append({
            "payee": payee,
            "category": category,
            "scheduled_amount": sched_amount,
            "actual_avg": round(actual_avg),
            "difference": round(actual_avg - sched_amount),
            "status": status,
        })

    return results


@app.command("check")
def check_scheduled(
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Compare scheduled amounts to actual recent averages."""
    try:
        results = _check_scheduled_mismatches(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps(results, indent=2, default=str))
        return

    if not results:
        console.print("[dim]No scheduled transactions to check.[/dim]")
        return

    table = Table(title="Scheduled vs Actual (last 3 months)")
    table.add_column("Payee", min_width=20)
    table.add_column("Scheduled", justify="right")
    table.add_column("Actual Avg", justify="right")
    table.add_column("Difference", justify="right")
    table.add_column("Status", justify="center")

    mismatches = 0
    for r in results:
        sched_str = format_amount(r["scheduled_amount"])
        if r["actual_avg"] is not None:
            avg_str = format_amount(r["actual_avg"])
            diff_str = format_amount_colored(r["difference"])
        else:
            avg_str = "[dim]no data[/dim]"
            diff_str = "[dim]—[/dim]"

        if r["status"] == "OK":
            status_str = "[green]OK[/green]"
        elif r["status"] == "MISMATCH":
            status_str = "[red]MISMATCH[/red]"
            mismatches += 1
        else:
            status_str = "[dim]NO DATA[/dim]"

        table.add_row(r["payee"], sched_str, avg_str, diff_str, status_str)

    console.print(table)

    if mismatches:
        console.print(f"\n[yellow]{mismatches} mismatch(es) found[/yellow] — review scheduled amounts")
    else:
        console.print("\n[green]All scheduled amounts match recent actuals.[/green]")
