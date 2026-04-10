"""Report commands — spending, income, payees, trends."""

import json
from collections import defaultdict
from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..utils.formatting import format_amount, format_amount_colored

app = typer.Typer(help="Financial reports and analysis.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")

# Internal/transfer category groups to exclude from reports
_EXCLUDED_GROUPS = {
    "internal master category",
    "credit card payments",
    "hidden categories",
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _since_date(months: int, since: str | None = None) -> str:
    """Calculate since_date from --months or --since."""
    if since:
        return since
    today = date.today()
    # Go back N months from the 1st of current month
    first_of_month = today.replace(day=1)
    for _ in range(months):
        first_of_month = (first_of_month - timedelta(days=1)).replace(day=1)
    return first_of_month.isoformat()


def _month_key(d) -> str:
    """Extract YYYY-MM from a date."""
    s = str(d)
    return s[:7]


def _build_category_group_map(budget_id: str | None = None) -> dict[str, str]:
    """Build a map of category_name -> group_name from the categories API."""
    groups = client.list_categories(budget_id=budget_id)
    cat_map = {}
    for group in groups:
        for cat in group.categories:
            if not cat.deleted:
                cat_map[cat.name] = group.name
    return cat_map


def _filter_spending_txs(txs: list, cat_group_map: dict[str, str] | None = None) -> list:
    """Filter to only real spending transactions (no transfers, inflows, internal)."""
    result = []
    for t in txs:
        if t.deleted or t.transfer_account_id or t.amount >= 0:
            continue
        if cat_group_map:
            group = cat_group_map.get(t.category_name or "", "").lower()
            if group in _EXCLUDED_GROUPS:
                continue
        result.append(t)
    return result


def _filter_income_txs(txs: list) -> list:
    """Filter to income transactions (positive, non-transfer)."""
    return [
        t
        for t in txs
        if not t.deleted
        and not t.transfer_account_id
        and t.amount > 0
        and (t.payee_name or "") not in ("Starting Balance", "Manual Balance Adjustment")
    ]


def _get_sorted_months(txs: list) -> list[str]:
    """Get sorted unique month keys from transactions."""
    months = sorted({_month_key(t.var_date) for t in txs})
    return months


def _get_group(t, cat_group_map: dict[str, str]) -> str:
    """Get category group name for a transaction."""
    return cat_group_map.get(t.category_name or "", "Other")


# ── Commands ─────────────────────────────────────────────────────────────────


@app.command("spending")
def report_spending(
    months: int = typer.Option(6, "--months", "-m", help="Number of months to include"),
    since: str | None = typer.Option(None, "--since", help="Start date (YYYY-MM-DD)"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Monthly spending breakdown by category group and category."""
    try:
        since_date = _since_date(months, since)
        txs = client.list_transactions(budget_id=budget, since_date=since_date)
        cat_group_map = _build_category_group_map(budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    spending = _filter_spending_txs(txs, cat_group_map)
    if not spending:
        console.print("[dim]No spending transactions found.[/dim]")
        return

    month_keys = _get_sorted_months(spending)

    # Build: group -> category -> month -> total
    data: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for t in spending:
        group = _get_group(t, cat_group_map)
        cat = t.category_name or "Uncategorized"
        mk = _month_key(t.var_date)
        data[group][cat][mk] += abs(t.amount)

    if json_output:
        out = []
        for group, cats in sorted(data.items()):
            for cat, by_month in sorted(cats.items()):
                row = {"group": group, "category": cat}
                for mk in month_keys:
                    row[mk] = by_month.get(mk, 0)
                row["total"] = sum(by_month.values())
                out.append(row)
        console.print(json.dumps(out, indent=2, default=str))
        return

    table = Table(title=f"Spending by Category ({month_keys[0]} to {month_keys[-1]})")
    table.add_column("Category", min_width=20)
    for mk in month_keys:
        table.add_column(mk[5:], justify="right")  # Show MM only
    table.add_column("Total", justify="right", style="bold")

    # Grand totals per month
    month_totals: dict[str, int] = defaultdict(int)

    for group in sorted(data.keys()):
        # Group header
        group_totals: dict[str, int] = defaultdict(int)
        rows = []
        for cat in sorted(data[group].keys()):
            by_month = data[group][cat]
            row_cells = [f"  {cat}"]
            cat_total = 0
            for mk in month_keys:
                val = by_month.get(mk, 0)
                cat_total += val
                group_totals[mk] += val
                month_totals[mk] += val
                row_cells.append(format_amount(val) if val else "[dim]—[/dim]")
            row_cells.append(format_amount(cat_total))
            rows.append(row_cells)

        # Group header row
        header_cells = [f"[bold]{group}[/bold]"]
        group_total = 0
        for mk in month_keys:
            gt = group_totals.get(mk, 0)
            group_total += gt
            header_cells.append(f"[bold]{format_amount(gt)}[/bold]" if gt else "[dim]—[/dim]")
        header_cells.append(f"[bold]{format_amount(group_total)}[/bold]")
        table.add_row(*header_cells)

        for row in rows:
            table.add_row(*row, style="dim")

    # Totals row
    table.add_section()
    total_cells = ["[bold]Total[/bold]"]
    grand_total = 0
    for mk in month_keys:
        mt = month_totals.get(mk, 0)
        grand_total += mt
        total_cells.append(f"[bold]{format_amount(mt)}[/bold]")
    total_cells.append(f"[bold]{format_amount(grand_total)}[/bold]")
    table.add_row(*total_cells)

    console.print(table)


@app.command("income")
def report_income(
    months: int = typer.Option(6, "--months", "-m", help="Number of months to include"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Monthly income vs expenses and savings rate."""
    try:
        since_date = _since_date(months)
        txs = client.list_transactions(budget_id=budget, since_date=since_date)
        cat_group_map = _build_category_group_map(budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Split income and spending
    all_txs = [t for t in txs if not t.deleted and not t.transfer_account_id]
    income_txs = _filter_income_txs(txs)
    spending_txs = _filter_spending_txs(txs, cat_group_map)

    if not all_txs:
        console.print("[dim]No transactions found.[/dim]")
        return

    month_keys = _get_sorted_months(all_txs)

    # Build monthly totals
    income_by_month: dict[str, int] = defaultdict(int)
    expense_by_month: dict[str, int] = defaultdict(int)

    for t in income_txs:
        income_by_month[_month_key(t.var_date)] += t.amount

    for t in spending_txs:
        expense_by_month[_month_key(t.var_date)] += abs(t.amount)

    if json_output:
        out = []
        for mk in month_keys:
            inc = income_by_month.get(mk, 0)
            exp = expense_by_month.get(mk, 0)
            net = inc - exp
            rate = (net / inc * 100) if inc > 0 else 0
            out.append(
                {
                    "month": mk,
                    "income": inc,
                    "expenses": exp,
                    "net": net,
                    "savings_rate": round(rate, 1),
                }
            )
        console.print(json.dumps(out, indent=2, default=str))
        return

    table = Table(title="Income vs Expenses")
    table.add_column("Month")
    table.add_column("Income", justify="right")
    table.add_column("Expenses", justify="right")
    table.add_column("Net", justify="right")
    table.add_column("Savings %", justify="right")

    total_income = 0
    total_expense = 0

    for mk in month_keys:
        inc = income_by_month.get(mk, 0)
        exp = expense_by_month.get(mk, 0)
        net = inc - exp
        rate = (net / inc * 100) if inc > 0 else 0
        total_income += inc
        total_expense += exp

        net_fmt = format_amount_colored(net) if net != 0 else format_amount(0)
        rate_color = "green" if rate >= 20 else "yellow" if rate >= 0 else "red"

        table.add_row(
            mk,
            format_amount(inc),
            format_amount(exp),
            net_fmt,
            f"[{rate_color}]{rate:.1f}%[/{rate_color}]",
        )

    # Totals
    table.add_section()
    total_net = total_income - total_expense
    total_rate = (total_net / total_income * 100) if total_income > 0 else 0
    rate_color = "green" if total_rate >= 20 else "yellow" if total_rate >= 0 else "red"

    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{format_amount(total_income)}[/bold]",
        f"[bold]{format_amount(total_expense)}[/bold]",
        f"[bold]{format_amount_colored(total_net)}[/bold]",
        f"[bold][{rate_color}]{total_rate:.1f}%[/{rate_color}][/bold]",
    )

    console.print(table)


@app.command("payees")
def report_payees(
    months: int = typer.Option(6, "--months", "-m", help="Number of months to include"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of payees to show"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Top payees by spending."""
    try:
        since_date = _since_date(months)
        txs = client.list_transactions(budget_id=budget, since_date=since_date)
        cat_group_map = _build_category_group_map(budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    spending = _filter_spending_txs(txs, cat_group_map)
    if not spending:
        console.print("[dim]No spending transactions found.[/dim]")
        return

    # Group by payee
    payee_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "count": 0, "categories": defaultdict(int)})
    for t in spending:
        payee = t.payee_name or "(no payee)"
        payee_data[payee]["total"] += abs(t.amount)
        payee_data[payee]["count"] += 1
        cat = t.category_name or "Uncategorized"
        payee_data[payee]["categories"][cat] += 1

    # Sort by total descending
    sorted_payees = sorted(payee_data.items(), key=lambda x: -x[1]["total"])[:limit]

    if json_output:
        out = []
        for payee, info in sorted_payees:
            top_cat = max(info["categories"], key=info["categories"].get)
            out.append(
                {
                    "payee": payee,
                    "count": info["count"],
                    "total": info["total"],
                    "average": info["total"] // info["count"],
                    "top_category": top_cat,
                }
            )
        console.print(json.dumps(out, indent=2, default=str))
        return

    table = Table(title=f"Top {len(sorted_payees)} Payees by Spending")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Payee")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Average", justify="right")
    table.add_column("Top Category", style="dim")

    for i, (payee, info) in enumerate(sorted_payees, 1):
        avg = info["total"] // info["count"]
        top_cat = max(info["categories"], key=info["categories"].get)
        table.add_row(
            str(i),
            payee,
            str(info["count"]),
            format_amount(info["total"]),
            format_amount(avg),
            top_cat,
        )

    console.print(table)


@app.command("trends")
def report_trends(
    months: int = typer.Option(6, "--months", "-m", help="Number of months to include"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Month-over-month spending trends by category group."""
    try:
        since_date = _since_date(months)
        txs = client.list_transactions(budget_id=budget, since_date=since_date)
        cat_group_map = _build_category_group_map(budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    spending = _filter_spending_txs(txs, cat_group_map)
    if not spending:
        console.print("[dim]No spending transactions found.[/dim]")
        return

    month_keys = _get_sorted_months(spending)

    # Group by category group -> month -> total
    data: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in spending:
        group = _get_group(t, cat_group_map)
        data[group][_month_key(t.var_date)] += abs(t.amount)

    # Sort groups by total spending
    group_totals = {g: sum(by_m.values()) for g, by_m in data.items()}
    sorted_groups = sorted(group_totals, key=group_totals.get, reverse=True)

    if json_output:
        out = []
        for group in sorted_groups:
            by_month = data[group]
            row = {"group": group}
            for mk in month_keys:
                row[mk] = by_month.get(mk, 0)
            # Calculate trend from last two months
            if len(month_keys) >= 2:
                prev = by_month.get(month_keys[-2], 0)
                curr = by_month.get(month_keys[-1], 0)
                row["change_pct"] = _change_pct(prev, curr)
            out.append(row)
        console.print(json.dumps(out, indent=2, default=str))
        return

    table = Table(title=f"Spending Trends ({month_keys[0]} to {month_keys[-1]})")
    table.add_column("Category Group", min_width=18)
    for mk in month_keys:
        table.add_column(mk[5:], justify="right")
    table.add_column("Trend", justify="center")

    for group in sorted_groups:
        by_month = data[group]
        row = [f"[bold]{group}[/bold]"]
        for mk in month_keys:
            val = by_month.get(mk, 0)
            row.append(format_amount(val) if val else "[dim]—[/dim]")

        # Trend arrow from last two months
        if len(month_keys) >= 2:
            prev = by_month.get(month_keys[-2], 0)
            curr = by_month.get(month_keys[-1], 0)
            row.append(_trend_arrow(prev, curr))
        else:
            row.append("—")

        table.add_row(*row)

    console.print(table)


@app.command("subscriptions")
def report_subscriptions(
    months: int = typer.Option(12, "--months", "-m", help="Number of months to analyze"),
    min_count: int = typer.Option(3, "--min-count", help="Minimum occurrences to qualify"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Detect recurring charges (subscriptions and regular bills)."""
    try:
        since_date = _since_date(months)
        txs = client.list_transactions(budget_id=budget, since_date=since_date)
        cat_group_map = _build_category_group_map(budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    spending = _filter_spending_txs(txs, cat_group_map)
    if not spending:
        console.print("[dim]No spending transactions found.[/dim]")
        return

    subs = _detect_subscriptions(spending, min_count)
    if not subs:
        console.print("[dim]No recurring charges detected.[/dim]")
        return

    # Sort by annual cost descending
    subs.sort(key=lambda s: s["annual_cost"], reverse=True)

    if json_output:
        console.print(json.dumps(subs, indent=2, default=str))
        return

    table = Table(title="Recurring Charges (Subscriptions & Bills)")
    table.add_column("Payee", min_width=20)
    table.add_column("Frequency")
    table.add_column("Avg Amount", justify="right")
    table.add_column("Last Charge")
    table.add_column("Category")
    table.add_column("Status")
    table.add_column("Monthly", justify="right")
    table.add_column("Annual", justify="right", style="bold")

    total_annual = 0
    for s in subs:
        total_annual += s["annual_cost"]
        status_style = "green" if s["status"] == "Fixed" else "yellow"
        table.add_row(
            s["payee"],
            s["frequency"],
            format_amount(s["avg_amount"]),
            s["last_charge"],
            s["category"],
            f"[{status_style}]{s['status']}[/{status_style}]",
            format_amount(s["monthly_cost"]),
            format_amount(s["annual_cost"]),
        )

    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        "",
        "",
        "",
        "",
        "",
        "",
        f"[bold]{format_amount(total_annual)}[/bold]",
    )
    console.print(table)


def _detect_subscriptions(txs: list, min_count: int) -> list[dict]:
    """Analyze transactions to find recurring charges.

    Groups by payee, checks for roughly monthly intervals (25-35 day gaps),
    and classifies as Fixed (consistent amount) or Variable.
    """
    # Group by payee
    payee_txs: dict[str, list] = defaultdict(list)
    for t in txs:
        payee = t.payee_name or "(no payee)"
        payee_txs[payee].append(t)

    results = []
    for payee, ptxs in payee_txs.items():
        if len(ptxs) < min_count:
            continue

        # Sort by date
        ptxs.sort(key=lambda t: str(t.var_date))

        # Check intervals
        dates = [date.fromisoformat(str(t.var_date)) for t in ptxs]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]

        if not gaps:
            continue

        # Count how many gaps are roughly monthly (25-35 days)
        monthly_gaps = [g for g in gaps if 25 <= g <= 35]
        if len(monthly_gaps) < min_count - 1:
            continue

        # Amount analysis
        amounts = [abs(t.amount) for t in ptxs]
        avg_amount = sum(amounts) // len(amounts)
        amount_spread = max(amounts) - min(amounts)
        # Fixed if spread is < 10% of average (or all same)
        status = "Fixed" if avg_amount == 0 or amount_spread <= avg_amount * 0.10 else "Variable"

        # Most common category
        cat_counts: dict[str, int] = defaultdict(int)
        for t in ptxs:
            cat_counts[t.category_name or "Uncategorized"] += 1
        category = max(cat_counts, key=cat_counts.get)

        monthly_cost = avg_amount
        annual_cost = monthly_cost * 12

        results.append(
            {
                "payee": payee,
                "frequency": "Monthly",
                "avg_amount": avg_amount,
                "last_charge": str(dates[-1]),
                "category": category,
                "status": status,
                "monthly_cost": monthly_cost,
                "annual_cost": annual_cost,
                "count": len(ptxs),
            }
        )

    return results


def _change_pct(prev: int, curr: int) -> float | None:
    """Calculate percentage change, or None if no previous data."""
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 1)


def _trend_arrow(prev: int, curr: int) -> str:
    """Return a colored trend arrow based on month-over-month change."""
    if prev == 0 and curr == 0:
        return "[dim]—[/dim]"
    if prev == 0:
        return "[red]↑ new[/red]"
    pct = (curr - prev) / prev * 100
    if pct > 15:
        return f"[red]↑ {pct:+.0f}%[/red]"
    if pct < -15:
        return f"[green]↓ {pct:+.0f}%[/green]"
    return f"[dim]→ {pct:+.0f}%[/dim]"
