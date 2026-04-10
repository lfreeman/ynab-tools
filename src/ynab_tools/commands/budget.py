"""Budget commands."""

import json
from datetime import date

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..config import load_config, save_config
from ..utils.formatting import format_amount, format_amount_colored

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
        console.print(json.dumps([b.to_dict() for b in budgets], indent=2, default=str))
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
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be assigned without making changes"),
    budget: str | None = typer.Option(None, "--budget", "-b", help="Budget ID override"),
):
    """Auto-assign RTA: cover overspent → fund goals → fix CC mismatches."""
    import ynab as ynab_sdk

    try:
        month_str = date.today().replace(day=1).isoformat()
        month_data = client.get_month(month_str, budget_id=budget)
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    rta = month_data.to_be_budgeted or 0
    if rta <= 0:
        console.print(f"[dim]Ready to Assign: {format_amount(rta)} — nothing to assign.[/dim]")
        return

    cats = [c for c in month_data.categories if not c.deleted and not c.hidden]
    assignments = []

    # Step 1: Cover overspent
    for c in cats:
        if (c.balance or 0) < 0:
            group = c.category_group_name or "Other"
            if group.lower() == "hidden categories":
                continue
            needed = abs(c.balance)
            assignments.append({"id": str(c.id), "name": c.name, "group": group, "amount": needed, "step": "overspent"})

    # Step 2: Fund underfunded goals
    for c in cats:
        uf = getattr(c, "goal_under_funded", None) or 0
        if uf > 0:
            group = c.category_group_name or "Other"
            # Skip if already in overspent list
            if any(a["id"] == str(c.id) for a in assignments):
                continue
            assignments.append({"id": str(c.id), "name": c.name, "group": group, "amount": uf, "step": "underfunded"})

    # Step 3: Fix CC payment mismatches
    # When step 1 covers overspent-on-credit categories, YNAB automatically
    # moves that money to the CC payment category. Skip CC gap-filling when
    # step 1 has overspent items to avoid double-funding.
    has_overspent = any(a["step"] == "overspent" for a in assignments)
    cc_accounts = {
        a.name: abs(a.balance or 0)
        for a in accounts
        if not a.closed and getattr(a, "type", "") == "creditCard"
    }
    for c in cats:
        if (c.category_group_name or "").lower() != "credit card payments":
            continue
        card_balance = cc_accounts.get(c.name)
        if card_balance is None:
            continue
        available = c.balance or 0
        gap = card_balance - available
        if gap > 0:
            if has_overspent:
                # Overspent coverage will auto-fund CC payments via YNAB;
                # re-run budget assign after to catch any remaining gap
                continue
            assignments.append({
                "id": str(c.id), "name": c.name,
                "group": "Credit Card Payments", "amount": gap, "step": "cc_mismatch",
            })

    if not assignments:
        console.print("[green]✓[/green] Nothing to assign — budget is clean!")
        return

    total = sum(a["amount"] for a in assignments)
    remaining = rta - total

    # Display plan
    console.print(f"\n[bold]Budget Assignment Plan — {month_str[:7]}[/bold]")
    console.print(f"Ready to Assign: {format_amount(rta)}\n")

    step_labels = {"overspent": "Cover Overspent", "underfunded": "Fund Goals", "cc_mismatch": "Fix CC Payments"}
    for step in ("overspent", "underfunded", "cc_mismatch"):
        step_items = [a for a in assignments if a["step"] == step]
        if not step_items:
            continue
        step_total = sum(a["amount"] for a in step_items)
        console.print(f"[bold]{step_labels[step]}[/bold] ({format_amount(step_total)})")
        for a in step_items:
            console.print(f"  {format_amount(a['amount']):>12}  → {a['name']}")
        console.print()

    console.print(f"Total: {format_amount(total)}")
    console.print(f"Remaining RTA: {format_amount(remaining)}")

    if dry_run:
        console.print("\n[dim]Dry run — no changes made.[/dim]")
        return

    # Execute
    console.print()
    api = ynab_sdk.CategoriesApi(client._get_client())
    bid = client._budget_id(budget)
    success = 0
    for a in assignments:
        # Get current budgeted for this category
        cat = next((c for c in cats if str(c.id) == a["id"]), None)
        if not cat:
            continue
        new_budgeted = (cat.budgeted or 0) + a["amount"]
        try:
            wrapper = ynab_sdk.PatchMonthCategoryWrapper(
                category=ynab_sdk.SaveMonthCategory(budgeted=new_budgeted)
            )
            api.update_month_category(bid, month_str, a["id"], wrapper)
            success += 1
        except Exception as e:
            console.print(f"[red]  ✗ {a['name']}: {e}[/red]")

    console.print(f"[green]✓[/green] Assigned {format_amount(total)} across {success} categories")


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
        console.print(json.dumps(out, indent=2, default=str))
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
    budget: str | None = typer.Option(None, "--budget", "-b", help="Budget ID override"),
):
    """Health check — overspent categories, unreconciled accounts, etc."""
    try:
        month_str = date.today().replace(day=1).isoformat()
        month_data = client.get_month(month_str, budget_id=budget)
        accounts = client.list_accounts(budget_id=budget)
        categories = client.list_categories(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    try:
        txs = client.list_transactions(budget_id=budget, since_date=month_str)
    except RuntimeError:
        txs = []

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
    for group in categories:
        group_name = group.name
        if group_name.lower() in ("internal master category", "hidden categories"):
            continue
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
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
    for a in active_accounts:
        last_rec = getattr(a, "last_reconciled_at", None)
        if last_rec:
            rec_date = date.fromisoformat(str(last_rec)[:10])
            days_ago = (date.today() - rec_date).days
            if days_ago > 30:
                stale_accounts.append((a.name, days_ago))
        else:
            stale_accounts.append((a.name, None))

    if not stale_accounts:
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
    cc_accounts = [a for a in active_accounts if getattr(a, "type", "") == "creditCard"]
    cc_issues = []
    for cc in cc_accounts:
        cc_balance = abs(getattr(cc, "balance", 0) or 0)
        # Find matching credit card payment category
        for group in categories:
            if group.name.lower() != "credit card payments":
                continue
            for cat in group.categories:
                if cat.hidden or cat.deleted:
                    continue
                if cat.name == cc.name:
                    payment_available = getattr(cat, "balance", 0) or 0
                    diff = abs(payment_available - cc_balance)
                    if diff > 1000:  # More than $1 difference
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
    for group in categories:
        group_name = group.name
        if group_name.lower() in ("internal master category", "hidden categories"):
            continue
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
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

    # 6. Age of Money
    age_of_money = getattr(month_data, "age_of_money", None)
    if age_of_money is not None:
        if age_of_money >= 30:
            console.print(f"[green]  PASS[/green]  Age of Money: {age_of_money} days")
        else:
            warnings += 1
            console.print(f"[yellow]  WARN[/yellow]  Age of Money: {age_of_money} days (< 30)")
    else:
        console.print("[dim]  INFO[/dim]  Age of Money: not available")

    # 7. Uncategorized transactions
    uncategorized = [t for t in txs if not t.deleted and not t.transfer_account_id and not t.category_id]
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
