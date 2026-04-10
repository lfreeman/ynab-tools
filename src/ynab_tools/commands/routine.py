"""Routine check commands — daily and monthly checklists."""

from datetime import date

import typer
from rich.console import Console

from ..api import client
from ..utils.formatting import format_amount, format_amount_colored

app = typer.Typer(help="Daily/monthly routine checks.", invoke_without_command=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")


def _daily_checks(budget_id: str | None = None) -> int:
    """Run daily checks. Returns count of items needing attention."""
    month_str = date.today().replace(day=1).isoformat()
    month_data = client.get_month(month_str, budget_id=budget_id)
    accounts = client.list_accounts(budget_id=budget_id)
    categories = client.list_categories(budget_id=budget_id)

    # Get current month transactions for uncategorized/unapproved
    txs = client.list_transactions(budget_id=budget_id, since_date=month_str)
    txs = [t for t in txs if not t.deleted]

    attention = 0

    # 0. Accounts needing re-auth
    active_accounts = [a for a in accounts if not a.closed]
    error_accounts = [
        a.name for a in active_accounts
        if getattr(a, "direct_import_linked", False) and getattr(a, "direct_import_in_error", False)
    ]
    if error_accounts:
        attention += 1
        console.print(
            f"[red]  FAIL[/red]  {len(error_accounts)} account(s) need re-authentication:"
        )
        for name in error_accounts:
            console.print(f"         {name}")
    else:
        linked = [a for a in active_accounts if getattr(a, "direct_import_linked", False)]
        if linked:
            console.print("[green]  PASS[/green]  All linked accounts connected")

    # 1. Uncategorized transactions
    uncategorized = [
        t for t in txs
        if not t.transfer_account_id and not t.category_id
        and (t.payee_name or "") not in ("Starting Balance", "Manual Balance Adjustment")
    ]
    if uncategorized:
        attention += 1
        console.print(
            f"[yellow]  WARN[/yellow]  {len(uncategorized)} uncategorized transaction(s)"
            f" -- run: ynab tx list --uncategorized --group-by-payee"
        )
    else:
        console.print("[green]  PASS[/green]  No uncategorized transactions")

    # 2. Unapproved transactions
    unapproved = [t for t in txs if not getattr(t, "approved", True)]
    if unapproved:
        attention += 1
        console.print(
            f"[yellow]  WARN[/yellow]  {len(unapproved)} unapproved transaction(s)"
            f" -- run: ynab tx list --unapproved"
        )
    else:
        console.print("[green]  PASS[/green]  All transactions approved")

    # 3. Overspent categories
    overspent = []
    for group in categories:
        if group.name.lower() in ("internal master category", "hidden categories"):
            continue
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
            balance = getattr(cat, "balance", 0) or 0
            if balance < 0:
                overspent.append((group.name, cat.name, balance))

    if overspent:
        attention += 1
        console.print(
            f"[red]  FAIL[/red]  {len(overspent)} overspent category(ies)"
            f" -- run: ynab budget assign"
        )
        for gn, cn, bal in overspent:
            console.print(f"         {gn} > {cn}: {format_amount_colored(bal)}")
    else:
        console.print("[green]  PASS[/green]  No overspent categories")

    # 4. CC payment mismatches
    active_accounts = [a for a in accounts if not a.closed]
    cc_accounts = [a for a in active_accounts if getattr(a, "type", "") == "creditCard"]
    cc_mismatches = []
    for cc in cc_accounts:
        cc_balance = abs(getattr(cc, "balance", 0) or 0)
        for group in categories:
            if group.name.lower() != "credit card payments":
                continue
            for cat in group.categories:
                if cat.hidden or cat.deleted:
                    continue
                if cat.name == cc.name:
                    payment_available = getattr(cat, "balance", 0) or 0
                    diff = payment_available - cc_balance
                    if abs(diff) > 1000:  # More than $1
                        cc_mismatches.append((cc.name, payment_available, cc_balance, diff))

    if cc_mismatches:
        attention += 1
        console.print(
            f"[yellow]  WARN[/yellow]  {len(cc_mismatches)} credit card payment mismatch(es)"
        )
        for name, payment, owed, diff in cc_mismatches:
            console.print(
                f"         {name}: payment {format_amount_colored(payment)}"
                f" vs owed {format_amount_colored(-owed)}"
                f" (diff {format_amount_colored(diff)})"
            )
    elif cc_accounts:
        console.print("[green]  PASS[/green]  Credit card payments match balances")

    # 5. RTA > 0
    rta = getattr(month_data, "to_be_budgeted", 0) or 0
    if rta > 0:
        attention += 1
        console.print(
            f"[yellow]  WARN[/yellow]  Ready to Assign: {format_amount_colored(rta)}"
            f" -- run: ynab budget assign"
        )
    elif rta < 0:
        attention += 1
        console.print(f"[red]  FAIL[/red]  Ready to Assign: {format_amount_colored(rta)} -- overbudgeted!")
    else:
        console.print("[green]  PASS[/green]  Ready to Assign is $0.00")

    # 6. Age of Money
    age_of_money = getattr(month_data, "age_of_money", None)
    if age_of_money is not None:
        if age_of_money >= 30:
            console.print(f"[green]  PASS[/green]  Age of Money: {age_of_money} days (target: 30+)")
        else:
            attention += 1
            console.print(f"[yellow]  WARN[/yellow]  Age of Money: {age_of_money} days (target: 30+)")
    else:
        console.print("[dim]  INFO[/dim]  Age of Money: not available")

    return attention


def _monthly_checks(budget_id: str | None = None) -> int:
    """Run monthly-only checks. Returns count of items needing attention."""
    accounts = client.list_accounts(budget_id=budget_id)
    categories = client.list_categories(budget_id=budget_id)
    attention = 0

    # 7. Accounts needing reconciliation (> 14 days)
    active_accounts = [a for a in accounts if not a.closed]
    stale = []
    for a in active_accounts:
        last_rec = getattr(a, "last_reconciled_at", None)
        if last_rec:
            rec_date = date.fromisoformat(str(last_rec)[:10])
            days_ago = (date.today() - rec_date).days
            if days_ago > 14:
                stale.append((a.name, days_ago))
        else:
            stale.append((a.name, None))

    if stale:
        attention += 1
        console.print(f"[yellow]  WARN[/yellow]  {len(stale)} account(s) need reconciliation (>14 days):")
        for name, days_ago in stale:
            if days_ago is not None:
                console.print(f"         {name}: {days_ago} days ago")
            else:
                console.print(f"         {name}: never reconciled")
    else:
        console.print("[green]  PASS[/green]  All accounts reconciled within 14 days")

    # 8. Underfunded goals
    underfunded = []
    for group in categories:
        if group.name.lower() in ("internal master category", "hidden categories"):
            continue
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
            uf = getattr(cat, "goal_under_funded", None) or 0
            if uf > 0:
                underfunded.append((group.name, cat.name, uf))

    if underfunded:
        attention += 1
        total_uf = sum(uf for _, _, uf in underfunded)
        console.print(
            f"[yellow]  WARN[/yellow]  {len(underfunded)} underfunded goal(s)"
            f" (total {format_amount(total_uf)})"
        )
    else:
        console.print("[green]  PASS[/green]  All goals fully funded")

    # 9. Scheduled transaction mismatches
    from .scheduled import _check_scheduled_mismatches

    try:
        sched_results = _check_scheduled_mismatches(budget_id=budget_id)
        mismatches = [r for r in sched_results if r["status"] == "MISMATCH"]
        if mismatches:
            attention += 1
            console.print(
                f"[yellow]  WARN[/yellow]  {len(mismatches)} scheduled transaction mismatch(es)"
                f" -- run: ynab scheduled check"
            )
        else:
            console.print("[green]  PASS[/green]  Scheduled amounts match recent actuals")
    except RuntimeError:
        console.print("[dim]  INFO[/dim]  Could not check scheduled transactions")

    # 10. Target coverage — categories without goal_type
    no_goal = []
    for group in categories:
        if group.name.lower() in ("internal master category", "hidden categories", "credit card payments"):
            continue
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
            goal_type = getattr(cat, "goal_type", None)
            if not goal_type:
                no_goal.append(cat.name)

    if no_goal:
        attention += 1
        console.print(f"[yellow]  WARN[/yellow]  {len(no_goal)} category(ies) have no target/goal set")
    else:
        console.print("[green]  PASS[/green]  All categories have targets")

    # 11. Reimbursements balance check
    month_str = date.today().replace(day=1).isoformat()
    month_data = client.get_month(month_str, budget_id=budget_id)
    reimb_cats = [
        c for c in month_data.categories
        if not c.deleted and not c.hidden and "reimbursement" in c.name.lower()
    ]
    for rc in reimb_cats:
        bal = rc.balance or 0
        if bal > 0:
            attention += 1
            console.print(
                f"[yellow]  WARN[/yellow]  {rc.name} has {format_amount(bal)}"
                f" -- move to other categories"
            )
        elif bal < 0:
            attention += 1
            console.print(
                f"[red]  FAIL[/red]  {rc.name} has {format_amount_colored(bal)}"
                f" -- pending reimbursement"
            )
        else:
            console.print(f"[green]  PASS[/green]  {rc.name} is settled ($0)")

    # 12. Suggest commands
    console.print()
    console.print("[bold]Suggested commands:[/bold]")
    console.print("  ynab tx audit")
    console.print("  ynab report spending")
    console.print("  ynab report trends")

    return attention


@app.callback(invoke_without_command=True)
def routine(
    ctx: typer.Context,
    monthly: bool = typer.Option(False, "--monthly", help="Include monthly checks"),
    budget: str | None = BudgetOpt,
):
    """Run daily (or monthly) routine checks."""
    if ctx.invoked_subcommand is not None:
        return

    try:
        console.print(f"\n[bold]{'Monthly' if monthly else 'Daily'} Routine — {date.today().isoformat()}[/bold]\n")

        attention = _daily_checks(budget_id=budget)

        if monthly:
            console.print()
            console.print("[bold]Monthly Checks[/bold]\n")
            attention += _monthly_checks(budget_id=budget)

        # Summary
        console.print()
        if attention == 0:
            console.print("[green bold]All clear![/green bold]")
        else:
            console.print(f"[yellow bold]{attention} item(s) need attention.[/yellow bold]")
        console.print()

    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
