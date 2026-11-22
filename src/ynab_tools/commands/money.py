"""Money questions answered from one budget snapshot (`ynab money ...`).

Each command builds a KTD1 answer in `ynab_tools.money` and hands it to
`_emit`, which prints it as JSON with `--json` or as Rich tables otherwise.
"""

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..config import ProfileError
from ..utils.formatting import format_amount, print_json

app = typer.Typer(help="Money questions: goals, available money, checking-savings split.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("goals")
def goals(
    apply: bool = typer.Option(False, "--apply", help="Create or update the goal categories (previews by default)"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Size the safety net, car and school goals, and create their categories.

    The safety net is the profile's safety_net_months of average essential
    spending over the last 12 full months. Car and school targets come from
    profile.json, or are estimated from the budget and flagged as estimates.
    Previews by default; --apply creates the goal group and categories and
    sets each target amount and date. The target type is set in the app.
    """
    from ..money.goals import goals_answer

    try:
        answer = goals_answer(budget_id=budget, apply=apply)
    except (RuntimeError, ProfileError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _emit(answer, json_output)


@app.command("available")
def available(
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """How much money is really free after everything already committed.

    Starts from Ready to Assign and subtracts this month's overspending,
    uncategorized outflows, card payment shortfalls and what the safety net
    and goals still need. Tracking accounts (brokerage, retirement) never
    count. Writes nothing.
    """
    from ..money.available import available_answer

    try:
        answer = available_answer(budget_id=budget)
    except (RuntimeError, ProfileError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _emit(answer, json_output)


@app.command("split")
def split(
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """How much to keep in checking, move to savings, or invest.

    Checking should hold next month's bills and card payments plus
    checking_cushion_months of spending (averages over the last 3 full
    months). Money above that goes to savings until savings holds the safety
    net, goals and savings_held_groups balances; the rest is available to
    invest. Gives amounts only; the owner makes the transfers.
    """
    from ..money.split import split_answer

    try:
        answer = split_answer(budget_id=budget)
    except (RuntimeError, ProfileError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _emit(answer, json_output)


def _emit(answer: dict, json_output: bool) -> None:
    """Print a KTD1 answer: the JSON object alone with `json_output`, else Rich tables."""
    if json_output:
        print_json(answer)
        return

    status = answer["status"]
    title = f"{answer['command']} — {answer['month']}"
    if status != "exact":
        title += f" ({status})"
    table = Table(title=title)
    table.add_column("Figure")
    table.add_column("Value", justify="right")
    table.add_column("How", style="dim")
    for name, f in answer["figures"].items():
        value = format_amount(f["value"])
        if f["estimate"]:
            value += " (estimate)"
        how = f["formula"]
        if "category" in f["inputs"]:
            how = f"{f['inputs']['category']} by {f['inputs']['by']}: {how} [{f['inputs']['action']}]"
        table.add_row(name, value, escape(how))
    console.print(table)

    _print_list("Rules applied", answer["rules_applied"])
    _print_list("Excluded", [f"{e['item']}: {e['reason']}" for e in answer["excluded"]])
    _print_list("Warnings", [f"{w['message']} — {w['fix']}" for w in answer["warnings"]], style="yellow")
    _print_list("Do in the YNAB app", [f"{a['change']} ({a['why']})" for a in answer["ui_actions"]])
    _print_list("Next", answer["next_actions"])


def _print_list(heading: str, lines: list[str], style: str | None = None) -> None:
    """Print `lines` as bullets under `heading`; nothing when empty. Lines are not markup."""
    if not lines:
        return
    console.print(f"\n[bold]{heading}[/bold]")
    for line in lines:
        console.print(f"  • {escape(line)}", style=style, highlight=False)
