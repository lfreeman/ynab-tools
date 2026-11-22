"""Budget commands."""

import json
from datetime import date, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from ..api import client
from ..config import ProfileError, load_config, save_config
from ..utils.formatting import err_console, format_amount, format_amount_colored, print_json

app = typer.Typer(help="Budget management.", no_args_is_help=True)
console = Console()

JsonFlag = typer.Option(False, "--json", help="Output as JSON")


@app.command("list")
def list_budgets(
    json_output: bool = JsonFlag,
):
    """List all budgets."""
    try:
        budgets = client.list_budgets()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if json_output:
        print_json([b.to_dict() for b in budgets])
        return

    config = load_config()

    table = Table(title="Budgets")
    table.add_column("", width=2)
    table.add_column("Name")
    table.add_column("ID", style="dim")

    for b in budgets:
        bid = str(b.id)
        marker = "[green]●[/green]" if bid == config.default_budget_id else " "
        table.add_row(marker, b.name, bid)

    console.print(table)


@app.command("use")
def use_budget(
    budget_id: str = typer.Argument(..., help="Budget ID (full or prefix) to set as default"),
):
    """Set the default budget."""
    try:
        budgets = client.list_budgets()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    match = next(
        (b for b in budgets if str(b.id) == budget_id or str(b.id).startswith(budget_id)),
        None,
    )

    if not match:
        console.print(f"[red]No budget matching '{budget_id}'[/red]")
        raise typer.Exit(1)

    save_config(default_budget_id=str(match.id), default_budget_name=match.name)
    console.print(f"[green]✓[/green] Default budget: {match.name}")


@app.command("assign")
def assign_budget(
    apply: bool = typer.Option(False, "--apply", help="Write the reviewed plan given by --plan"),
    plan_file: Path | None = typer.Option(
        None, "--plan", help="Plan file: the JSON a preview printed, as the owner reviewed it"
    ),
    month: str | None = typer.Option(None, "--month", "-m", help="Month (YYYY-MM) to preview, default current"),
    json_output: bool = JsonFlag,
    budget: str | None = typer.Option(None, "--budget", "-b", help="Budget ID override"),
):
    """Propose how to assign Ready to Assign; write only a reviewed plan.

    Previews by default and writes nothing: cover overspending, then card
    payment shortfalls, then this month's target needs, then the safety net
    and goals. --json prints the plan; save it, edit `to` amounts or remove
    items, then --apply --plan FILE writes it. Apply refuses a plan whose
    categories changed since the preview, and skips items already applied.
    """
    from ..money.assign import PlanError, apply_plan, assign_answer
    from .money import _emit

    if apply != (plan_file is not None):
        err_console.print("[red]--apply and --plan go together: preview first, then apply the saved plan.[/red]")
        raise typer.Exit(1)
    try:
        if plan_file is not None:
            answer = apply_plan(json.loads(plan_file.read_text()), budget_id=budget)
        else:
            answer = assign_answer(
                budget_id=budget, month=date.fromisoformat(f"{month}-01") if month else None
            )
    except (RuntimeError, ProfileError, PlanError, OSError, ValueError) as e:
        err_console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1)

    if json_output:
        print_json(answer)
    else:
        _print_assign_items(answer["items"])
        _emit(answer, False)
    if answer.get("outcome") in ("stale_plan", "refused", "partial"):
        raise typer.Exit(1)


def _print_assign_items(items: list[dict]) -> None:
    """Print a plan's items as a table, with each item's outcome when it has one."""
    if not items:
        console.print("[green]✓[/green] Nothing to assign.")
        return
    table = Table(title="Assignment plan")
    table.add_column("Step")
    table.add_column("Category")
    table.add_column("From", justify="right")
    table.add_column("To", justify="right")
    table.add_column("Change", justify="right")
    with_outcome = "outcome" in items[0]
    if with_outcome:
        table.add_column("Outcome")
    for i in items:
        row = [
            i.get("step", ""),
            escape(str(i.get("category", i["category_id"]))),
            format_amount(i["from"]),
            format_amount(i["to"]),
            format_amount(i["to"] - i["from"]),
        ]
        if with_outcome:
            row.append(i["outcome"])
        table.add_row(*row)
    console.print(table)


@app.command("plan")
def plan_budget(
    month: str | None = typer.Option(None, "--month", "-m", help="Month (YYYY-MM), default current"),
    show_empty: bool = typer.Option(False, "--empty", help="Show categories with no activity"),
    json_output: bool = JsonFlag,
    budget: str | None = typer.Option(None, "--budget", "-b", help="Budget ID override"),
):
    """Show budget plan — assigned, activity, available by category (like YNAB Plan page)."""
    try:
        if month:
            month_str = f"{month}-01"
        else:
            month_str = date.today().replace(day=1).isoformat()
        month_data = client.get_month(month_str, budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    cats = month_data.categories or []
    # Filter
    cats = [c for c in cats if not c.deleted]
    if not show_empty:
        cats = [c for c in cats if c.budgeted or c.activity or c.balance]

    # Group by category_group_name
    from collections import OrderedDict

    groups: dict[str, list] = OrderedDict()
    for c in cats:
        gn = c.category_group_name or "Other"
        if gn.lower() in ("hidden categories",):
            continue
        groups.setdefault(gn, []).append(c)

    rta = getattr(month_data, "to_be_budgeted", 0) or 0

    if json_output:
        out = {
            "month": month_str[:7],
            "ready_to_assign": rta,
            "groups": {},
        }
        for gn, gcats in groups.items():
            out["groups"][gn] = [
                {
                    "name": c.name,
                    "budgeted": c.budgeted,
                    "activity": c.activity,
                    "balance": c.balance,
                    "goal_type": getattr(c, "goal_type", None),
                    "goal_under_funded": getattr(c, "goal_under_funded", None),
                }
                for c in gcats
            ]
        print_json(out)
        return

    # Header
    rta_fmt = format_amount_colored(rta)
    console.print(f"\n[bold]Plan — {month_str[:7]}[/bold]  Ready to Assign: {rta_fmt}\n")

    table = Table()
    table.add_column("Category", min_width=25)
    table.add_column("Assigned", justify="right")
    table.add_column("Activity", justify="right")
    table.add_column("Available", justify="right")
    table.add_column("Status", justify="center")

    for gn, gcats in groups.items():
        # Group totals
        g_budgeted = sum(c.budgeted for c in gcats)
        g_activity = sum(c.activity for c in gcats)
        g_balance = sum(c.balance for c in gcats)

        table.add_row(
            f"[bold]{gn}[/bold]",
            f"[bold]{format_amount(g_budgeted)}[/bold]" if g_budgeted else "[dim]—[/dim]",
            f"[bold]{format_amount_colored(g_activity)}[/bold]" if g_activity else "[dim]—[/dim]",
            f"[bold]{format_amount_colored(g_balance)}[/bold]",
            "",
        )

        for c in gcats:
            # Status indicator
            balance = c.balance or 0
            uf = getattr(c, "goal_under_funded", None) or 0
            if balance < 0:
                status = "[red]Overspent[/red]"
            elif uf > 0:
                status = "[yellow]Underfunded[/yellow]"
            elif balance == 0 and not c.budgeted:
                status = "[dim]—[/dim]"
            else:
                status = "[green]Funded[/green]"

            table.add_row(
                f"  {c.name}",
                format_amount(c.budgeted) if c.budgeted else "[dim]—[/dim]",
                format_amount_colored(c.activity) if c.activity else "[dim]—[/dim]",
                format_amount_colored(balance),
                status,
            )

        table.add_section()

    console.print(table)


@app.command("check")
def check_budget(
    month: str | None = typer.Option(None, "--month", "-m", help="Month (YYYY-MM), default current"),
    budget: str | None = typer.Option(None, "--budget", "-b", help="Budget ID override"),
):
    """Health check — overspent categories, unreconciled accounts, etc.

    With `--month`, checks that month's category balances and only the
    transactions dated in it. Checks that describe today rather than a month —
    reconciliation age, card balances against payment categories, Age of
    Money — are skipped for a past month.
    """
    from .tx import _find_mirrored_adjustments

    current = date.today().replace(day=1)
    month_start = date.fromisoformat(f"{month}-01") if month else current
    is_past = month_start < current
    month_str = month_start.isoformat()
    next_month = (month_start + timedelta(days=32)).replace(day=1).isoformat()
    try:
        month_data = client.get_month(month_str, budget_id=budget)
        accounts = client.list_accounts(budget_id=budget)
        if is_past:
            # Only the month endpoint knows a past month's balances.
            rows = [(c.category_group_name or "", c) for c in (month_data.categories or [])]
        else:
            rows = [(g.name, c) for g in client.list_categories(budget_id=budget) for c in g.categories]
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    try:
        txs = client.list_transactions(budget_id=budget, since_date=month_str)
        txs = [t for t in txs if str(t.var_date)[:10] < next_month]
    except RuntimeError:
        txs = []

    rows = [
        (group_name, cat) for group_name, cat in rows
        if group_name.lower() not in ("internal master category", "hidden categories")
        and not cat.hidden and not cat.deleted
    ]
    issues = 0
    warnings = 0

    console.print(f"\n[bold]Budget Health Check — {month_str[:7]}[/bold]\n")

    # 1. Ready to Assign
    rta = getattr(month_data, "to_be_budgeted", 0) or 0
    if rta == 0:
        console.print("[green]  PASS[/green]  Ready to Assign is $0.00")
    elif rta > 0:
        warnings += 1
        console.print(f"[yellow]  WARN[/yellow]  Ready to Assign: {format_amount_colored(rta)} — assign to categories")
    else:
        issues += 1
        console.print(f"[red]  FAIL[/red]  Ready to Assign: {format_amount_colored(rta)} — overbudgeted!")

    # 2. Overspent categories
    overspent = []
    for group_name, cat in rows:
        balance = getattr(cat, "balance", 0) or 0
        if balance < 0:
            overspent.append((group_name, cat.name, balance))

    if not overspent:
        console.print("[green]  PASS[/green]  No overspent categories")
    else:
        issues += len(overspent)
        console.print(f"[red]  FAIL[/red]  {len(overspent)} overspent category(ies):")
        for group_name, cat_name, balance in overspent:
            console.print(f"         {group_name} > {cat_name}: {format_amount_colored(balance)}")

    # 3. Accounts not reconciled in 30+ days
    active_accounts = [a for a in accounts if not a.closed]
    stale_accounts = []
    for a in ([] if is_past else active_accounts):
        last_rec = getattr(a, "last_reconciled_at", None)
        if last_rec:
            rec_date = date.fromisoformat(str(last_rec)[:10])
            days_ago = (date.today() - rec_date).days
            if days_ago > 30:
                stale_accounts.append((a.name, days_ago))
        else:
            stale_accounts.append((a.name, None))

    if is_past:
        console.print(
            "[dim]  INFO[/dim]  Reconciliation age, card balances and Age of Money:"
            " skipped for a past month (they describe today)"
        )
    elif not stale_accounts:
        console.print("[green]  PASS[/green]  All accounts reconciled within 30 days")
    else:
        warnings += len(stale_accounts)
        console.print(f"[yellow]  WARN[/yellow]  {len(stale_accounts)} account(s) need reconciliation:")
        for name, days_ago in stale_accounts:
            if days_ago is not None:
                console.print(f"         {name}: {days_ago} days ago")
            else:
                console.print(f"         {name}: never reconciled")

    # 4. Credit card payment vs balance mismatch
    cc_accounts = [] if is_past else [a for a in active_accounts if getattr(a, "type", "") == "creditCard"]
    cc_issues = []
    for cc in cc_accounts:
        # Negative balance = owed; positive = card is in credit, nothing to fund.
        cc_balance = max(0, -(getattr(cc, "balance", 0) or 0))
        for group_name, cat in rows:
            if group_name.lower() == "credit card payments" and cat.name == cc.name:
                payment_available = getattr(cat, "balance", 0) or 0
                if abs(payment_available - cc_balance) > 1000:  # More than $1 difference
                    cc_issues.append((cc.name, cc_balance, payment_available))

    if cc_accounts and not cc_issues:
        console.print("[green]  PASS[/green]  Credit card payments match balances")
    elif cc_issues:
        warnings += len(cc_issues)
        console.print(f"[yellow]  WARN[/yellow]  {len(cc_issues)} credit card payment mismatch(es):")
        for name, balance, payment in cc_issues:
            console.print(
                f"         {name}: balance {format_amount(balance)}, payment available {format_amount(payment)}"
            )

    # 5. Underfunded goals
    underfunded = []
    for group_name, cat in rows:
        uf = getattr(cat, "goal_under_funded", None) or 0
        if uf > 0:
            underfunded.append((group_name, cat.name, uf))

    if not underfunded:
        console.print("[green]  PASS[/green]  All goals fully funded")
    else:
        warnings += len(underfunded)
        total_uf = sum(uf for _, _, uf in underfunded)
        console.print(
            f"[yellow]  WARN[/yellow]  {len(underfunded)} underfunded goal(s) (total {format_amount(total_uf)}):"
        )
        for group_name, cat_name, uf in underfunded:
            console.print(f"         {group_name} > {cat_name}: {format_amount(uf)} needed")

    # 6. Age of Money (skipped for a past month; reported under check 3)
    age_of_money = getattr(month_data, "age_of_money", None)
    if not is_past:
        if age_of_money is None:
            console.print("[dim]  INFO[/dim]  Age of Money: not available")
        elif age_of_money >= 30:
            console.print(f"[green]  PASS[/green]  Age of Money: {age_of_money} days")
        else:
            warnings += 1
            console.print(f"[yellow]  WARN[/yellow]  Age of Money: {age_of_money} days (< 30)")

    # 7. Uncategorized transactions
    uncategorized = [
        t for t in txs
        if not t.deleted and not t.transfer_account_id and not t.category_id
        and (t.payee_name or "") not in ("Starting Balance", "Manual Balance Adjustment")
    ]
    if not uncategorized:
        console.print("[green]  PASS[/green]  No uncategorized transactions")
    else:
        warnings += len(uncategorized)
        console.print(f"[yellow]  WARN[/yellow]  {len(uncategorized)} uncategorized transaction(s)")

    # 8. Unapproved transactions
    unapproved = [t for t in txs if not t.deleted and not getattr(t, "approved", True)]
    if not unapproved:
        console.print("[green]  PASS[/green]  No unapproved transactions")
    else:
        warnings += len(unapproved)
        console.print(f"[yellow]  WARN[/yellow]  {len(unapproved)} unapproved transaction(s)")

    # 9. Mirrored reconciliation adjustments
    mirrored = _find_mirrored_adjustments([t for t in txs if not t.deleted])
    if not mirrored:
        console.print("[green]  PASS[/green]  No mirrored reconciliation adjustments")
    else:
        warnings += len(mirrored)
        console.print(
            f"[yellow]  WARN[/yellow]  {len(mirrored)} mirrored reconciliation adjustment pair(s)"
            " — likely a payment linked to the wrong account:"
        )
        for neg, pos in mirrored:
            console.print(
                f"         {neg.var_date}: {(neg.account_name or '').strip()} {format_amount(neg.amount)}"
                f" / {(pos.account_name or '').strip()} {format_amount(pos.amount)}"
            )

    # Summary
    console.print()
    if issues == 0 and warnings == 0:
        console.print("[green bold]All checks passed![/green bold]")
    else:
        parts = []
        if issues:
            parts.append(f"[red]{issues} issue(s)[/red]")
        if warnings:
            parts.append(f"[yellow]{warnings} warning(s)[/yellow]")
        console.print(f"Result: {', '.join(parts)}")
    console.print()
