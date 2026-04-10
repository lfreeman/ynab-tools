"""Transaction commands."""

import json
from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..config import load_config
from ..utils.formatting import format_amount, format_amount_colored, short_id

app = typer.Typer(help="Transaction management.", no_args_is_help=True)
console = Console()

BudgetOpt = typer.Option(None, "--budget", "-b", help="Budget ID override")
JsonFlag = typer.Option(False, "--json", help="Output as JSON")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resolve_category(name: str, budget_id: str | None = None) -> str | None:
    """Resolve a category name (substring) to its ID."""
    groups = client.list_categories(budget_id=budget_id)
    matches = []

    for group in groups:
        for cat in group.categories:
            if cat.hidden or cat.deleted:
                continue
            if name.lower() in cat.name.lower():
                matches.append(cat)

    if not matches:
        console.print(f"[red]No category matching '{name}'[/red]")
        return None

    if len(matches) == 1:
        console.print(f"[dim]Category: {matches[0].name}[/dim]")
        return str(matches[0].id)

    # Exact match takes priority
    exact = [c for c in matches if c.name.lower() == name.lower()]
    if len(exact) == 1:
        console.print(f"[dim]Category: {exact[0].name}[/dim]")
        return str(exact[0].id)

    console.print(f"[yellow]Multiple categories match '{name}':[/yellow]")
    for cat in matches:
        console.print(f"  {cat.name}  [dim]{cat.id}[/dim]")
    return None


def _resolve_account(name: str, budget_id: str | None = None) -> str | None:
    """Resolve an account name (substring) to its ID."""
    accounts = client.list_accounts(budget_id=budget_id)
    matches = [a for a in accounts if not a.closed and name.lower() in a.name.lower()]

    if not matches:
        console.print(f"[red]No account matching '{name}'[/red]")
        return None

    if len(matches) == 1:
        return str(matches[0].id)

    exact = [a for a in matches if a.name.lower() == name.lower()]
    if len(exact) == 1:
        return str(exact[0].id)

    console.print(f"[yellow]Multiple accounts match '{name}':[/yellow]")
    for a in matches:
        console.print(f"  {a.name}  [dim]{a.id}[/dim]")
    return None


# ── Commands ─────────────────────────────────────────────────────────────────


@app.command("list")
def list_txs(
    unapproved: bool = typer.Option(False, "--unapproved", "-u", help="Only unapproved"),
    uncategorized: bool = typer.Option(False, "--uncategorized", help="Only uncategorized"),
    account: str | None = typer.Option(None, "--account", "-a", help="Filter by account name"),
    since: str | None = typer.Option(None, "--since", "-s", help="Since date (YYYY-MM-DD)"),
    days: int | None = typer.Option(None, "--days", "-d", help="Last N days"),
    payee: str | None = typer.Option(None, "--payee", "-p", help="Filter by payee name (substring)"),
    memo: str | None = typer.Option(None, "--memo", help="Filter by memo (substring)"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Limit number of results"),
    group_by_payee: bool = typer.Option(False, "--group-by-payee", "-g", help="Group by payee (summary view)"),
    include_transfers: bool = typer.Option(
        False, "--include-transfers", help="Include transfers (hidden with --uncategorized/--group-by-payee)"
    ),
    show_original: bool = typer.Option(False, "--original", "-o", help="Show original bank payee description"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """List transactions with optional filters."""
    try:
        # Server-side type filter
        tx_type = None
        if unapproved:
            tx_type = "unapproved"
        elif uncategorized:
            tx_type = "uncategorized"

        # Date filter
        since_date = since
        if days:
            since_date = (date.today() - timedelta(days=days)).isoformat()

        # Fetch — by account or all
        if account:
            account_id = _resolve_account(account, budget)
            if not account_id:
                raise typer.Exit(1)
            txs = client.list_transactions_by_account(account_id, budget_id=budget, since_date=since_date, type=tx_type)
        else:
            txs = client.list_transactions(budget_id=budget, since_date=since_date, type=tx_type)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    # Client-side filters
    txs = [t for t in txs if not t.deleted]
    if payee:
        txs = [t for t in txs if payee.lower() in (t.payee_name or "").lower()]
    if memo:
        txs = [t for t in txs if memo.lower() in (t.memo or "").lower()]

    # Filter out transfers and balance adjustments when viewing uncategorized or grouping
    if (group_by_payee or uncategorized) and not include_transfers:
        txs = [t for t in txs if not t.transfer_account_id]
        txs = [t for t in txs if (t.payee_name or "") not in ("Starting Balance", "Manual Balance Adjustment")]

    # Sort by date descending
    txs.sort(key=lambda t: t.var_date, reverse=True)

    if limit:
        txs = txs[:limit]

    # Grouped output
    if group_by_payee:
        _print_grouped_by_payee(txs, json_output)
        return

    # Output
    if json_output:
        console.print(json.dumps([t.to_dict() for t in txs], indent=2, default=str))
        return

    if not txs:
        console.print("[dim]No transactions found.[/dim]")
        return

    table = Table(title=f"Transactions ({len(txs)})")
    table.add_column("Date", style="dim")
    table.add_column("Payee")
    if show_original:
        table.add_column("Original", style="dim")
    table.add_column("Category")
    table.add_column("Amount", justify="right")
    table.add_column("Account", style="dim")
    table.add_column("St", justify="center")
    table.add_column("ID", style="dim")

    for t in txs:
        status = "✓" if t.approved else "[yellow]○[/yellow]"
        cat_name = t.category_name or "[red]—[/red]"
        row = [str(t.var_date), t.payee_name or "—"]
        if show_original:
            row.append(t.import_payee_name_original or "—")
        row.extend(
            [
                cat_name,
                format_amount_colored(t.amount),
                t.account_name or "—",
                status,
                short_id(t.id),
            ]
        )
        table.add_row(*row)

    console.print(table)

    # Actionable hints
    if unapproved:
        console.print(
            "\n[dim]Approve all:[/dim] ynab tx approve --all"
            "\n[dim]Approve one:[/dim] ynab tx approve <ID>"
        )
    elif uncategorized:
        console.print(
            "\n[dim]By payee:[/dim] ynab tx categorize-payee <PAYEE> <CATEGORY>"
            "\n[dim]Single:[/dim] ynab tx categorize <ID> <CATEGORY>"
        )

def _print_grouped_by_payee(txs: list, json_output: bool) -> None:
    """Print transactions grouped by payee with count and total."""
    from collections import defaultdict

    groups: dict[str, dict] = defaultdict(lambda: {"count": 0, "total": 0, "dates": []})
    for t in txs:
        payee = t.payee_name or "(no payee)"
        groups[payee]["count"] += 1
        groups[payee]["total"] += t.amount
        groups[payee]["dates"].append(str(t.var_date))

    if not groups:
        console.print("[dim]No transactions found.[/dim]")
        return

    # Sort by count descending, then by total absolute value
    sorted_groups = sorted(groups.items(), key=lambda x: (-x[1]["count"], -abs(x[1]["total"])))

    if json_output:
        out = [
            {
                "payee": payee,
                "count": info["count"],
                "total": info["total"],
                "date_range": f"{min(info['dates'])} – {max(info['dates'])}",
            }
            for payee, info in sorted_groups
        ]
        console.print(json.dumps(out, indent=2, default=str))
        return

    total_txs = sum(info["count"] for _, info in sorted_groups)
    table = Table(title=f"Uncategorized by Payee ({total_txs} transactions, {len(sorted_groups)} payees)")
    table.add_column("Payee")
    table.add_column("Count", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Date Range", style="dim")

    for payee, info in sorted_groups:
        date_range = f"{min(info['dates'])} – {max(info['dates'])}" if info["count"] > 1 else min(info["dates"])
        table.add_row(
            payee,
            str(info["count"]),
            format_amount_colored(info["total"]),
            date_range,
        )

    console.print(table)


@app.command("show")
def show_tx(
    id: str = typer.Argument(..., help="Transaction ID"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Show full transaction details."""
    try:
        tx = client.get_transaction(id, budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(json.dumps(tx.to_dict(), indent=2, default=str))
        return

    console.print(f"\n[bold]{tx.payee_name or 'No payee'}[/bold]")
    console.print(f"  Date:       {tx.var_date}")
    console.print(f"  Amount:     {format_amount(tx.amount)}")
    console.print(f"  Account:    {tx.account_name}")
    console.print(f"  Category:   {tx.category_name or '—'}")
    console.print(f"  Memo:       {tx.memo or '—'}")
    console.print(f"  Approved:   {'Yes' if tx.approved else 'No'}")
    console.print(f"  Cleared:    {tx.cleared.value if hasattr(tx.cleared, 'value') else tx.cleared}")
    console.print(f"  Flag:       {tx.flag_color or '—'}")
    if tx.import_payee_name:
        console.print(f"  Import:     {tx.import_payee_name}")
    if tx.import_payee_name_original:
        console.print(f"  Original:   {tx.import_payee_name_original}")
    if tx.import_id:
        console.print(f"  Import ID:  {tx.import_id}")
    if tx.transfer_account_id:
        console.print(f"  Transfer:   {tx.transfer_account_id}")
    if tx.debt_transaction_type:
        dt = tx.debt_transaction_type
        debt_type = dt.value if hasattr(dt, "value") else dt
        console.print(f"  Debt Type:  {debt_type}")
    console.print(f"  ID:         {tx.id}")
    console.print()


@app.command("approve")
def approve_txs(
    ids: list[str] = typer.Argument(None, help="Transaction IDs to approve"),
    all_unapproved: bool = typer.Option(False, "--all", help="Approve all unapproved transactions"),
    budget: str | None = BudgetOpt,
):
    """Approve one or more transactions."""
    try:
        if all_unapproved:
            txs = client.list_transactions(budget_id=budget, type="unapproved")
            txs = [t for t in txs if not t.deleted]
            if not txs:
                console.print("[dim]No unapproved transactions.[/dim]")
                return
            ids = [str(t.id) for t in txs]
            console.print(f"[dim]Approving {len(ids)} transactions...[/dim]")

        if not ids:
            console.print("[yellow]No IDs specified. Use --all or pass transaction IDs.[/yellow]")
            raise typer.Exit(1)

        if len(ids) == 1:
            client.approve_transaction(ids[0], budget_id=budget)
            console.print(f"[green]✓[/green] Approved {short_id(ids[0])}")
        else:
            client.approve_transactions_batch(ids, budget_id=budget)
            console.print(f"[green]✓[/green] Approved {len(ids)} transactions")
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("categorize")
def categorize_tx(
    id: str = typer.Argument(..., help="Transaction ID"),
    category: str = typer.Argument(..., help="Category name (or substring)"),
    budget: str | None = BudgetOpt,
):
    """Set category on a transaction by name."""
    try:
        cat_id = _resolve_category(category, budget)
        if not cat_id:
            raise typer.Exit(1)

        client.categorize_transaction(id, cat_id, budget_id=budget)
        console.print(f"[green]✓[/green] Categorized {short_id(id)}")
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("categorize-payee")
def categorize_by_payee(
    payee_name: str = typer.Argument(..., help="Payee name (exact match)"),
    category: str = typer.Argument(..., help="Category name (or substring)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be categorized without making changes"),
    budget: str | None = BudgetOpt,
):
    """Categorize all uncategorized transactions from a payee."""
    try:
        # Resolve category first
        cat_id = _resolve_category(category, budget)
        if not cat_id:
            raise typer.Exit(1)

        # Fetch uncategorized transactions
        txs = client.list_transactions(budget_id=budget, type="uncategorized")
        txs = [t for t in txs if not t.deleted and t.payee_name == payee_name]

        if not txs:
            console.print(f"[yellow]No uncategorized transactions for '{payee_name}'[/yellow]")
            raise typer.Exit(1)

        if dry_run:
            console.print(f"[dim]Would categorize {len(txs)} transactions:[/dim]")
            for t in txs:
                console.print(f"  {t.var_date}  {format_amount_colored(t.amount)}  {t.account_name}")
            return

        # Batch update
        updates = [{"id": str(t.id), "category_id": cat_id} for t in txs]
        client.update_transactions_batch(updates, budget_id=budget)
        console.print(f"[green]✓[/green] Categorized {len(txs)} transactions from [bold]{payee_name}[/bold]")

    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("move")
def move_tx(
    id: str = typer.Argument(..., help="Transaction ID"),
    account: str = typer.Argument(..., help="Target account name (substring)"),
    budget: str | None = BudgetOpt,
):
    """Move a transaction to a different account."""
    try:
        account_id = _resolve_account(account, budget)
        if not account_id:
            raise typer.Exit(1)

        tx = client.get_transaction(id, budget_id=budget)
        console.print(f"[dim]Moving {tx.payee_name} {format_amount(tx.amount)} from {tx.account_name}[/dim]")

        client.update_transaction(id, {"account_id": account_id}, budget_id=budget)
        console.print(f"[green]✓[/green] Moved {short_id(id)} to {account}")
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


@app.command("update")
def update_tx(
    id: str = typer.Argument(..., help="Transaction ID"),
    category: str | None = typer.Option(None, "--category", "-c", help="Category name"),
    payee_name: str | None = typer.Option(None, "--payee", help="Payee name"),
    memo_text: str | None = typer.Option(None, "--memo", help="Memo text"),
    flag: str | None = typer.Option(
        None,
        "--flag",
        help="Flag color (red/orange/yellow/green/blue/purple/clear)",
    ),
    clear: bool = typer.Option(False, "--clear", help="Mark as cleared"),
    approve: bool = typer.Option(False, "--approve", help="Mark as approved"),
    budget: str | None = BudgetOpt,
):
    """Update transaction fields."""
    data = {}

    if category:
        cat_id = _resolve_category(category, budget)
        if not cat_id:
            raise typer.Exit(1)
        data["category_id"] = cat_id

    if payee_name is not None:
        data["payee_name"] = payee_name

    if memo_text is not None:
        data["memo"] = memo_text

    if flag is not None:
        data["flag_color"] = "" if flag == "clear" else flag

    if clear:
        data["cleared"] = "cleared"

    if approve:
        data["approved"] = True

    if not data:
        console.print("[yellow]No updates specified.[/yellow]")
        raise typer.Exit(1)

    try:
        client.update_transaction(id, data, budget_id=budget)
        console.print(f"[green]✓[/green] Updated {short_id(id)}")
    except Exception as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)


# ── Audit ───────────────────────────────────────────────────────────────────


def _ynab_url(account_id: str) -> str:
    """Build a YNAB web app URL for an account."""
    config = load_config()
    return f"https://app.ynab.com/{config.default_budget_id}/accounts/{account_id}"


def _audit_mismatched_transfers(txs: list, accounts: list) -> list[dict]:
    """Find transfers linked to wrong credit card account."""
    acct_map = {str(a.id): a.name for a in accounts}

    # Orphans: card-side inflows, bank-imported, not linked as transfer
    orphans = [
        t
        for t in txs
        if t.import_id
        and not t.transfer_account_id
        and t.amount > 0
        and t.account_name != "🏦Checking"
        and (
            "payment" in (t.import_payee_name_original or "").lower()
            or "automatic" in (t.import_payee_name_original or "").lower()
        )
    ]

    # Transfer outflows from checking
    transfers = [t for t in txs if t.transfer_account_id and t.amount < 0 and t.account_name == "🏦Checking"]

    issues = []
    matched_orphans = set()
    matched_transfers = set()

    for orphan in orphans:
        for transfer in transfers:
            if str(orphan.id) in matched_orphans or str(transfer.id) in matched_transfers:
                continue
            if abs(orphan.amount) != abs(transfer.amount):
                continue
            d1 = orphan.var_date if isinstance(orphan.var_date, date) else date.fromisoformat(str(orphan.var_date))
            d2 = (
                transfer.var_date if isinstance(transfer.var_date, date) else date.fromisoformat(str(transfer.var_date))
            )
            if abs((d1 - d2).days) > 5:
                continue

            wrong_target = acct_map.get(str(transfer.transfer_account_id), "?")
            correct_target = orphan.account_name
            if wrong_target == correct_target:
                continue

            matched_orphans.add(str(orphan.id))
            matched_transfers.add(str(transfer.id))
            issues.append(
                {
                    "type": "mismatched_transfer",
                    "severity": "high",
                    "description": (
                        f"${abs(orphan.amount) / 1000:,.2f} payment linked to"
                        f" [{wrong_target}] instead of [{correct_target}]"
                    ),
                    "date": str(d1),
                    "amount": orphan.amount,
                    "account": orphan.account_name,
                    "account_id": str(orphan.account_id),
                    "transaction_id": str(orphan.id),
                    "related_id": str(transfer.id),
                }
            )

    return issues


def _audit_duplicates(txs: list) -> list[dict]:
    """Find potential duplicate transactions on the same account."""
    from collections import defaultdict

    # Group by (account_id, amount)
    groups = defaultdict(list)
    for t in txs:
        if t.import_id and not t.transfer_account_id:
            groups[(str(t.account_id), t.amount)].append(t)

    issues = []
    for (acct_id, amount), group in groups.items():
        if len(group) < 2:
            continue
        # Check for close dates
        sorted_group = sorted(group, key=lambda t: str(t.var_date))
        for i in range(len(sorted_group) - 1):
            t1, t2 = sorted_group[i], sorted_group[i + 1]
            d1 = t1.var_date if isinstance(t1.var_date, date) else date.fromisoformat(str(t1.var_date))
            d2 = t2.var_date if isinstance(t2.var_date, date) else date.fromisoformat(str(t2.var_date))
            day_diff = abs((d1 - d2).days)
            if day_diff == 0 and t1.payee_name == t2.payee_name:
                # Same day, same payee, same amount — likely duplicate
                issues.append(
                    {
                        "type": "duplicate",
                        "severity": "medium",
                        "description": (
                            f"${abs(amount) / 1000:,.2f} at {t1.payee_name or '?'}"
                            " — 2 transactions same day"
                        ),
                        "date": str(d1),
                        "amount": amount,
                        "account": t1.account_name,
                        "account_id": str(t1.account_id),
                        "transaction_id": str(t1.id),
                        "related_id": str(t2.id),
                    }
                )

    return issues


def _audit_orphaned_transfers(txs: list) -> list[dict]:
    """Find transfer transactions where the other side is missing."""
    tx_ids = {str(t.id) for t in txs if not t.deleted}
    issues = []

    for t in txs:
        if t.transfer_transaction_id and not t.deleted:
            partner_id = str(t.transfer_transaction_id)
            if partner_id not in tx_ids:
                issues.append(
                    {
                        "type": "orphaned_transfer",
                        "severity": "high",
                        "description": f"Transfer partner missing — {t.payee_name} ${abs(t.amount) / 1000:,.2f}",
                        "date": str(t.var_date),
                        "amount": t.amount,
                        "account": t.account_name,
                        "account_id": str(t.account_id),
                        "transaction_id": str(t.id),
                        "related_id": partner_id,
                    }
                )

    return issues


def _audit_stale_uncleared(txs: list) -> list[dict]:
    """Find imported transactions still uncleared after 7+ days."""
    cutoff = date.today() - timedelta(days=7)
    issues = []

    for t in txs:
        if not t.import_id or t.deleted:
            continue
        cleared = t.cleared.value if hasattr(t.cleared, "value") else str(t.cleared)
        if cleared == "uncleared":
            tx_date = t.var_date if isinstance(t.var_date, date) else date.fromisoformat(str(t.var_date))
            if tx_date < cutoff:
                issues.append(
                    {
                        "type": "stale_uncleared",
                        "severity": "low",
                        "description": (
                            f"Imported {abs((date.today() - tx_date).days)}d ago,"
                            f" still uncleared — {t.payee_name or '?'}"
                        ),
                        "date": str(tx_date),
                        "amount": t.amount,
                        "account": t.account_name,
                        "account_id": str(t.account_id),
                        "transaction_id": str(t.id),
                        "related_id": None,
                    }
                )

    return issues


def _audit_uncategorized(txs: list) -> list[dict]:
    """Count uncategorized non-transfer transactions."""
    uncategorized = [
        t
        for t in txs
        if not t.deleted and not t.transfer_account_id and (t.category_name or "").lower() in ("uncategorized", "")
    ]

    if not uncategorized:
        return []

    return [
        {
            "type": "uncategorized",
            "severity": "info",
            "description": f"{len(uncategorized)} uncategorized transactions",
            "date": "",
            "amount": 0,
            "account": "",
            "account_id": "",
            "transaction_id": "",
            "related_id": None,
        }
    ]


@app.command("audit")
def audit_txs(
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Audit transactions for common issues."""
    try:
        console.print("[dim]Fetching all transactions...[/dim]")
        txs = client.list_transactions(budget_id=budget)
        txs = [t for t in txs if not t.deleted]
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Analyzing {len(txs)} transactions...[/dim]\n")

    all_issues = []
    all_issues.extend(_audit_mismatched_transfers(txs, accounts))
    all_issues.extend(_audit_orphaned_transfers(txs))
    all_issues.extend(_audit_duplicates(txs))
    all_issues.extend(_audit_stale_uncleared(txs))
    all_issues.extend(_audit_uncategorized(txs))

    if json_output:
        console.print(json.dumps(all_issues, indent=2, default=str))
        return

    if not all_issues:
        console.print("[green]✓ No issues found![/green]")
        return

    severity_style = {
        "high": "red bold",
        "medium": "yellow",
        "low": "dim",
        "info": "blue",
    }
    severity_icon = {
        "high": "🔴",
        "medium": "🟡",
        "low": "⚪",
        "info": "🔵",
    }

    # Group by type
    from collections import defaultdict

    by_type = defaultdict(list)
    for issue in all_issues:
        by_type[issue["type"]].append(issue)

    type_labels = {
        "mismatched_transfer": "Mismatched Transfers",
        "orphaned_transfer": "Orphaned Transfers",
        "duplicate": "Potential Duplicates",
        "stale_uncleared": "Stale Uncleared",
        "uncategorized": "Uncategorized",
    }

    total = len(all_issues)
    high = sum(1 for i in all_issues if i["severity"] == "high")
    medium = sum(1 for i in all_issues if i["severity"] == "medium")

    console.print(f"Found [bold]{total}[/bold] issue(s): [red]{high} high[/red], [yellow]{medium} medium[/yellow]\n")

    for issue_type, label in type_labels.items():
        issues = by_type.get(issue_type, [])
        if not issues:
            continue

        console.print(f"[bold]── {label} ({len(issues)}) ──[/bold]")

        for issue in issues:
            icon = severity_icon[issue["severity"]]
            style = severity_style[issue["severity"]]

            console.print(f"\n  {icon} [{style}]{issue['description']}[/{style}]")

            if issue["date"]:
                console.print(f"     Date:    {issue['date']}")
            if issue["account"]:
                console.print(f"     Account: {issue['account']}")
            if issue["amount"]:
                console.print(f"     Amount:  {format_amount(issue['amount'])}")
            if issue["transaction_id"]:
                tid = issue["transaction_id"]
                console.print(f"     TX:      {short_id(tid)}  [dim]ynab tx show {tid}[/dim]")
            if issue.get("related_id"):
                rid = issue["related_id"]
                console.print(f"     Related: {short_id(rid)}  [dim]ynab tx show {rid}[/dim]")
            if issue.get("account_id"):
                url = _ynab_url(issue["account_id"])
                console.print(f"     YNAB:    [link={url}]{url}[/link]")

        console.print()
