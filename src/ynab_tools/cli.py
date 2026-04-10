"""YNAB CLI — personal finance from the terminal."""

import typer
from rich.console import Console

from .commands import acct, budget, cat, payee, report, routine, scheduled, tx
from .config import load_config

app = typer.Typer(name="ynab", no_args_is_help=True)
console = Console()

app.add_typer(budget.app, name="budget")
app.add_typer(tx.app, name="tx")
app.add_typer(cat.app, name="cat")
app.add_typer(acct.app, name="acct")
app.add_typer(payee.app, name="payee")
app.add_typer(report.app, name="report")
app.add_typer(scheduled.app, name="scheduled")
app.add_typer(routine.app, name="routine")


@app.command()
def status():
    """Show connection status and default budget."""
    from .api import client as api

    config = load_config()

    if not config.api_key:
        console.print("[red]YNAB_API_KEY not configured.[/red]")
        console.print("Add it to .env or set as environment variable.")
        raise typer.Exit(1)

    try:
        budgets = api.list_budgets()
        console.print(f"[green]✓[/green] Connected — {len(budgets)} budget(s)")
    except Exception as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise typer.Exit(1)

    if config.default_budget_id:
        console.print(f"  Default budget: {config.default_budget_name} ({config.default_budget_id[:8]}...)")
    else:
        console.print("  [yellow]No default budget set. Run 'ynab budget use <id>'[/yellow]")
