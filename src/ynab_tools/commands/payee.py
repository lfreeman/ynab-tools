"""Payee commands."""

import json

import typer
from rich.console import Console
from rich.table import Table

from ..api import client

app = typer.Typer(help="Payee management.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("list")
def list_payees(
    search: str | None = typer.Option(None, "--search", "-s", help="Filter by name substring"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Limit number of results"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """List all payees."""
    try:
        payees = client.list_payees(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    payees = [p for p in payees if not p.deleted]

    if search:
        payees = [p for p in payees if search.lower() in (p.name or "").lower()]

    payees.sort(key=lambda p: (p.name or "").lower())

    if limit:
        payees = payees[:limit]

    if json_output:
        console.print(json.dumps([p.to_dict() for p in payees], indent=2, default=str))
        return

    table = Table(title=f"Payees ({len(payees)})")
    table.add_column("Name")
    table.add_column("ID", style="dim")

    for p in payees:
        table.add_row(p.name or "—", str(p.id)[:8])

    console.print(table)
