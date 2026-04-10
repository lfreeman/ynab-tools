"""Category commands."""

import json

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..utils.formatting import format_amount, format_amount_colored, short_id

app = typer.Typer(help="Category management.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("list")
def list_categories(
    include_hidden: bool = typer.Option(False, "--hidden", help="Include hidden categories"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """List all categories grouped by category group."""
    try:
        groups = client.list_categories(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps([g.to_dict() for g in groups], indent=2, default=str))
        return

    table = Table(title="Categories")
    table.add_column("Group / Category")
    table.add_column("Budgeted", justify="right")
    table.add_column("Activity", justify="right")
    table.add_column("Balance", justify="right")
    table.add_column("ID", style="dim")

    for group in groups:
        if group.hidden and not include_hidden:
            continue

        table.add_row(f"[bold]{group.name}[/bold]", "", "", "", "")

        for cat in group.categories:
            if cat.hidden and not include_hidden:
                continue
            if cat.deleted:
                continue

            table.add_row(
                f"  {cat.name}",
                format_amount(cat.budgeted),
                format_amount_colored(cat.activity),
                format_amount_colored(cat.balance),
                short_id(cat.id),
            )

    console.print(table)
