"""Account commands."""

import json

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..utils.formatting import format_amount, format_amount_colored

app = typer.Typer(help="Account management.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("list")
def list_accounts(
    include_closed: bool = typer.Option(False, "--closed", help="Include closed accounts"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """List all accounts with balances."""
    try:
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if not include_closed:
        accounts = [a for a in accounts if not a.closed]

    if json_output:
        console.print(json.dumps([a.to_dict() for a in accounts], indent=2, default=str))
        return

    table = Table(title=f"Accounts ({len(accounts)})")
    table.add_column("Name")
    table.add_column("Type", style="dim")
    table.add_column("Balance", justify="right")
    table.add_column("Cleared", justify="right", style="dim")
    table.add_column("Uncleared", justify="right", style="dim")

    for a in accounts:
        table.add_row(
            a.name,
            a.type,
            format_amount_colored(a.balance),
            format_amount(a.cleared_balance),
            format_amount(a.uncleared_balance),
        )

    console.print(table)
