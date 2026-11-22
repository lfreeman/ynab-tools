"""Account commands."""

import re
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..config import Statement, save_statement
from ..utils.formatting import err_console, format_amount, format_amount_colored, print_json

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
        print_json([a.to_dict() for a in accounts])
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

def _bare_name(name: str) -> str:
    """`name` without leading emoji, symbols or whitespace, casefolded."""
    return re.sub(r"^[\W_]+", "", name).strip().casefold()


def _match_accounts(name: str, accounts: list) -> list:
    """Accounts whose name equals `name`, else those containing it.

    Both comparisons ignore case; the exact one also ignores leading emoji,
    symbols and whitespace on either side. Substring matching is used only
    when no account matches exactly.
    """
    wanted = _bare_name(name)
    exact = [a for a in accounts if _bare_name(a.name or "") == wanted]
    return exact or [a for a in accounts if name.lower() in (a.name or "").lower()]


@app.command("verify")
def verify_account(
    name: str = typer.Argument(..., help="Account name (exact match wins, else substring)"),
    balance: float = typer.Option(..., "--balance", help="The balance your bank shows right now"),
    on: str | None = typer.Option(
        None, "--date", help="Date the bank balance is from, YYYY-MM-DD (a statement date)"
    ),
    save: bool = typer.Option(
        False, "--save", help="Record the balance and date as this account's statement in profile.json"
    ),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Explain the difference between YNAB and your bank for one account.

    Reconciling to a figure you cannot account for books the discrepancy
    permanently as an adjustment, so this decomposes the gap first: what is
    cleared, what is still pending, and which pending entries the bank never
    confirmed. A gap that survives all three is genuinely missing data, and no
    amount of reconciling will explain it afterwards.

    With `--date`, the balance is also compared with YNAB as of that date
    (KTD8). `--save` records it as the account's statement, dated `--date` or
    today, for every later money answer to check against.
    """
    from ..money.quality import statement_gap
    from .tx import _find_ghosts

    try:
        as_of = date.fromisoformat(on) if on else None
    except ValueError:
        console.print(f"[red]--date must be YYYY-MM-DD, got '{on}'[/red]")
        raise typer.Exit(1)

    try:
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    matches = _match_accounts(name, [a for a in accounts if not a.closed])
    if not matches:
        console.print(f"[red]No open account matching '{name}'[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        console.print(f"[red]'{name}' matches {len(matches)} accounts:[/red]")
        for a in matches:
            console.print(f"  {a.name}")
        raise typer.Exit(1)

    acct = matches[0]
    bank = round(balance * 1000)
    working = acct.balance or 0
    cleared = acct.cleared_balance or 0
    uncleared = acct.uncleared_balance or 0

    txs = [t for t in client.list_transactions_by_account(str(acct.id), budget_id=budget)
           if not t.deleted]
    ghosts, unconfirmed = _find_ghosts(txs)
    ghost_total = sum(g.amount for g, _ in ghosts)
    unconfirmed_total = sum(u.amount for u, _ in unconfirmed)

    # What the balance would be once every unconfirmed entry is resolved: ghosts
    # removed (the bank never charged them) and the rest assumed genuine.
    explained = working - ghost_total
    unexplained = explained - bank
    dated_gap = statement_gap(acct, Statement(balance=bank, on=as_of), txs) if as_of else None

    if save:
        statement_on = as_of or date.today()
        save_statement(acct.name, bank, statement_on)
        err_console.print(f"Saved {acct.name} statement: {format_amount(bank)} on {statement_on}")

    if json_output:
        print_json({
            "account": acct.name,
            "ynab_working": working,
            "ynab_cleared": cleared,
            "ynab_uncleared": uncleared,
            "bank": bank,
            "ghosts": {"count": len(ghosts), "total": ghost_total},
            "unconfirmed": {"count": len(unconfirmed), "total": unconfirmed_total},
            "unexplained": unexplained,
            "as_of": {"date": as_of.isoformat(), "unexplained": dated_gap} if as_of else None,
        })
        return

    table = Table(title=f"{acct.name} vs bank")
    table.add_column("")
    table.add_column("Amount", justify="right")
    table.add_row("Bank balance", format_amount(bank))
    table.add_row("YNAB working balance", format_amount(working))
    table.add_row("  of which cleared", format_amount(cleared), style="dim")
    table.add_row("  of which pending", format_amount(uncleared), style="dim")
    table.add_row("Raw difference", format_amount_colored(working - bank))
    console.print(table)

    if ghosts:
        console.print(
            f"\n[yellow]{len(ghosts)} ghost(s) worth {format_amount(ghost_total)}[/yellow]"
            " — never imported, each beside a real charge. Run: ynab tx ghosts"
        )
    if unconfirmed:
        console.print(
            f"[yellow]{len(unconfirmed)} unconfirmed worth {format_amount(unconfirmed_total)}[/yellow]"
            " — no matching real charge; check a statement before removing."
        )

    console.print()
    if abs(unexplained) < 10:
        console.print(
            "[green]✓[/green] Accounted for. Removing the ghosts brings YNAB"
            " to the bank's figure — safe to reconcile."
        )
    else:
        console.print(
            f"[red]Unexplained: {format_amount(unexplained)}[/red]"
            " — remains after allowing for every ghost, so the bank holds"
            " activity YNAB never received. Reconciling now books this as a"
            " permanent adjustment."
        )
    if as_of:
        console.print(
            f"As of {as_of}, allowing for ghosts, YNAB differs from this figure by"
            f" {format_amount(dated_gap)}."
        )
