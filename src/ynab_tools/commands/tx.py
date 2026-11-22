"""Transaction commands."""

import re
from collections import defaultdict
from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from ..api import client
from ..config import load_config
from ..utils.formatting import err_console, format_amount, format_amount_colored, print_json, short_id

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
        print_json([t.to_dict() for t in txs])
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
        print_json(out)
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
        print_json(tx.to_dict())
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


# Words that appear in so many bank descriptions that matching on them alone
# pairs unrelated payees — two different bills can both read
# "Online Payment to <something>".
_GENERIC_PAYEE_WORDS = {
    "online", "payment", "payments", "to", "the", "a", "of", "and", "inc", "llc",
    "co", "com", "bill", "pay", "auto", "recurring", "transfer", "web", "id",
}


def _payee_tokens(name: str | None) -> set[str]:
    """Significant lowercase word tokens of a payee name."""
    words = re.findall(r"[a-z]+", (name or "").lower())
    return {w for w in words if len(w) > 2 and w not in _GENERIC_PAYEE_WORDS}


def _is_uncleared(tx) -> bool:
    return "UNCLEARED" in str(getattr(tx, "cleared", "")).upper()


def _real_twin(ghost, txs: list, window_days: int):
    """Find a bank-confirmed transaction that `ghost` appears to duplicate.

    A twin is within `window_days`, shares a significant payee token, and is
    itself confirmed by the bank — either imported directly or matched to an
    import. Amounts are deliberately NOT compared: a scheduled entry carries one
    fixed amount while the real charge varies month to month, which is precisely
    why YNAB fails to auto-match them.

    A twin on the ghost's own account is preferred. Failing that, one on another
    account counts too: when a subscription moves to a different card, the
    schedule keeps posting to the old one while the real charge imports on the
    new one.
    """
    g_tokens = _payee_tokens(ghost.payee_name)
    if not g_tokens:
        return None
    g_date = ghost.var_date if isinstance(ghost.var_date, date) else date.fromisoformat(str(ghost.var_date))
    other_account = None
    for t in txs:
        if str(t.id) == str(ghost.id):
            continue
        if not (t.import_id or getattr(t, "matched_transaction_id", None)):
            continue
        if not (g_tokens & _payee_tokens(t.payee_name)):
            continue
        t_date = t.var_date if isinstance(t.var_date, date) else date.fromisoformat(str(t.var_date))
        if abs((t_date - g_date).days) > window_days:
            continue
        if str(t.account_id) == str(ghost.account_id):
            return t
        other_account = other_account or t
    return other_account


def _find_ghosts(txs: list, window_days: int = 5) -> tuple[list, list]:
    """Split unconfirmed transactions into (ghosts_with_twin, unconfirmed).

    A YNAB-generated entry carries no `import_id`; if it also has no
    `matched_transaction_id` and never cleared, the bank never confirmed it.
    Those with a real twin are duplicates of a charge that did import; those
    without may be a payment that genuinely stopped happening.
    """
    candidates = [
        t for t in txs
        if _is_uncleared(t)
        and not t.import_id
        and not getattr(t, "matched_transaction_id", None)
        and not t.transfer_account_id
    ]
    ghosts, unconfirmed = [], []
    for c in candidates:
        twin = _real_twin(c, txs, window_days)
        (ghosts if twin else unconfirmed).append((c, twin))
    return ghosts, unconfirmed


def _ghost_schedules(scheduled: list, ghosts: list) -> list[tuple]:
    """Pair each scheduled transaction with the ghosts it has produced.

    `ghosts` is the first element of `_find_ghosts`. Returns `(schedule,
    [ghost, ...])` for every non-deleted schedule sharing a significant payee
    token with at least one ghost; schedules with none are omitted. Deleting
    the ghosts first removes the only evidence, so call this before deleting.
    """
    by_tokens: dict[frozenset, list] = defaultdict(list)
    for g, _twin in ghosts:
        tokens = _payee_tokens(g.payee_name)
        if tokens:
            by_tokens[frozenset(tokens)].append(g)

    offenders = []
    for s in scheduled:
        if getattr(s, "deleted", False):
            continue
        tokens = _payee_tokens(getattr(s, "payee_name", None))
        if not tokens:
            continue
        produced = [g for key, items in by_tokens.items() if tokens & key for g in items]
        if produced:
            offenders.append((s, produced))
    return offenders



def _find_misrouted_payments(txs: list, accounts: list, window_days: int = 7) -> list[dict]:
    """Find card payments linked to the wrong credit card.

    Bank autopay descriptions frequently do not say which card was paid, so YNAB
    guesses — and with several cards from one issuer it guesses wrong. The
    outflow is real and the money did leave, but the credit lands on a card that
    never received it, inflating that card's balance indefinitely.

    The tell is a payment transferred to card A while card B has an unlinked
    inflow of the same amount, imported from B's own feed. B's feed is the
    authority: the card itself reported the money arriving.
    """
    cards = {
        str(a.id): a.name for a in accounts
        if not a.closed and getattr(a, "type", "") == "creditCard"
    }
    issues = []
    for t in txs:
        dest = str(t.transfer_account_id) if t.transfer_account_id else None
        if not dest or dest not in cards or t.amount >= 0:
            continue
        t_date = t.var_date if isinstance(t.var_date, date) else date.fromisoformat(str(t.var_date))
        for other in txs:
            if str(other.account_id) not in cards or str(other.account_id) == dest:
                continue
            # The receiving card's own import, not yet tied to anything.
            if other.transfer_account_id or not other.import_id:
                continue
            if other.amount != -t.amount:
                continue
            o_date = other.var_date if isinstance(other.var_date, date) else date.fromisoformat(str(other.var_date))
            if abs((o_date - t_date).days) > window_days:
                continue
            issues.append({
                "type": "misrouted_payment",
                "severity": "high",
                "description": (
                    f"{format_amount(t.amount)} paid to {cards[dest]}"
                    f" but {other.account_name} imported it on {o_date}"
                ),
                "date": str(t_date),
                "amount": t.amount,
                "account": t.account_name,
                "account_id": str(t.account_id),
                "transaction_id": str(t.id),
                "linked_to": cards[dest],
                "should_be": other.account_name,
                "related_id": str(other.id),
            })
            break
    return issues


def _audit_ghosts(txs: list) -> list[dict]:
    """Find scheduled-transaction ghosts the bank never confirmed."""
    ghosts, unconfirmed = _find_ghosts(txs)
    issues = []
    for g, twin in ghosts:
        issues.append({
            "type": "ghost",
            "severity": "high",
            "description": (
                f"{format_amount(g.amount)} at {g.payee_name or '?'}"
                f" — never imported; real charge {format_amount(twin.amount)}"
                f" on {twin.var_date}"
                + (f" ({twin.account_name})" if str(twin.account_id) != str(g.account_id) else "")
            ),
            "date": str(g.var_date),
            "amount": g.amount,
            "account": g.account_name,
            "account_id": str(g.account_id),
            "transaction_id": str(g.id),
            "related_id": str(twin.id),
        })
    for u, _ in unconfirmed:
        issues.append({
            "type": "unconfirmed",
            "severity": "medium",
            "description": (
                f"{format_amount(u.amount)} at {u.payee_name or '?'}"
                " — never imported and no matching real charge"
            ),
            "date": str(u.var_date),
            "amount": u.amount,
            "account": u.account_name,
            "account_id": str(u.account_id),
            "transaction_id": str(u.id),
            "related_id": None,
        })
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
        err_console.print("[dim]Fetching all transactions...[/dim]")
        txs = client.list_transactions(budget_id=budget)
        txs = [t for t in txs if not t.deleted]
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    err_console.print(f"[dim]Analyzing {len(txs)} transactions...[/dim]\n")

    all_issues = []
    all_issues.extend(_find_misrouted_payments(txs, accounts))
    all_issues.extend(_audit_orphaned_transfers(txs))
    all_issues.extend(_audit_ghosts(txs))
    all_issues.extend(_audit_duplicates(txs))
    all_issues.extend(_audit_stale_uncleared(txs))
    all_issues.extend(_audit_uncategorized(txs))

    if json_output:
        print_json(all_issues)
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
        "misrouted_payment": "Misrouted Card Payments",
        "orphaned_transfer": "Orphaned Transfers",
        "ghost": "Ghost Transactions (never imported)",
        "unconfirmed": "Unconfirmed (no matching real charge)",
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


@app.command("ghosts")
def ghosts_txs(
    days: int = typer.Option(5, "--days", help="How far apart a ghost and its real charge may be"),
    delete: bool = typer.Option(False, "--delete", help="Delete confirmed ghosts (never the unconfirmed ones)"),
    keep_schedules: bool = typer.Option(
        False, "--keep-schedules",
        help="Delete even while the scheduled transactions producing them remain",
    ),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Find scheduled-transaction ghosts the bank never confirmed.

    A ghost is uncleared, has no import_id and no matched_transaction_id, and
    sits beside a real charge from the same payee that did import. Scheduled
    transactions whose fixed amount no longer matches the real bill generate one
    of these every month, silently doubling the category's spending.

    Entries with no matching real charge are reported but never deleted: those
    may be a payment that genuinely stopped (verify against a statement first).

    `--delete` refuses while a scheduled transaction is still producing the
    ghosts: they would return next month, and once deleted they are the only
    evidence `ynab scheduled prune` has to find that schedule.
    """
    import ynab as ynab_sdk

    try:
        err_console.print("[dim]Fetching all transactions...[/dim]")
        txs = [t for t in client.list_transactions(budget_id=budget) if not t.deleted]
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    ghosts, unconfirmed = _find_ghosts(txs, window_days=days)

    if json_output:
        print_json(_audit_ghosts(txs))
        return

    if not ghosts and not unconfirmed:
        console.print("[green]\u2713[/green] No ghosts found — every uncleared transaction is bank-confirmed.")
        return

    if ghosts:
        table = Table(title=f"Ghosts ({len(ghosts)}) — safe to delete")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Payee")
        table.add_column("Account")
        table.add_column("Real charge")
        for g, twin in sorted(ghosts, key=lambda x: str(x[0].var_date)):
            table.add_row(
                str(g.var_date),
                format_amount(g.amount),
                (g.payee_name or "?")[:28],
                (g.account_name or "")[:18],
                f"{twin.var_date} {format_amount(twin.amount)}" + (
                    f" on {(twin.account_name or '').strip()[:18]}"
                    if str(twin.account_id) != str(g.account_id) else ""
                ),
            )
        console.print(table)
        console.print(f"[dim]Total: {format_amount(sum(g.amount for g, _ in ghosts))}[/dim]\n")

    if unconfirmed:
        table = Table(title=f"Unconfirmed ({len(unconfirmed)}) — check a statement, never auto-deleted")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("Payee")
        table.add_column("Account")
        for u, _ in sorted(unconfirmed, key=lambda x: str(x[0].var_date)):
            table.add_row(
                str(u.var_date),
                format_amount(u.amount),
                (u.payee_name or "?")[:28],
                (u.account_name or "")[:18],
            )
        console.print(table)

    if not delete:
        if ghosts:
            console.print("\n[dim]Delete them with: ynab tx ghosts --delete[/dim]")
        return

    if not ghosts:
        console.print("\n[dim]Nothing to delete — only unconfirmed entries, which are left alone.[/dim]")
        return

    if not keep_schedules:
        try:
            offenders = _ghost_schedules(client.list_scheduled_transactions(budget_id=budget), ghosts)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
        if offenders:
            names = ", ".join(sorted({s.payee_name or "?" for s, _ in offenders}))
            console.print(
                f"\n[red]Not deleted:[/red] {len(offenders)} scheduled transaction(s) still produce"
                f" these ghosts ({names}). Run [bold]ynab scheduled prune --delete[/bold] first,"
                " or pass --keep-schedules to delete the ghosts anyway."
            )
            raise typer.Exit(1)

    api = ynab_sdk.TransactionsApi(client._get_client())
    bid = client._budget_id(budget)
    deleted = 0
    for g, _ in ghosts:
        try:
            api.delete_transaction(bid, str(g.id))
            deleted += 1
        except Exception as e:
            console.print(f"[red]  \u2717 {g.payee_name}: {e}[/red]")
    console.print(f"\n[green]\u2713[/green] Deleted {deleted} ghost(s)")


def _find_unconfirmed_payments(txs: list, today: date | None = None, grace_days: int = 10) -> list[dict]:
    """Find transfers the sending bank confirmed but the receiving account never did.

    A transfer from checking creates both sides at once. The checking side is
    matched to the bank's import; the receiving side should later be matched to
    the receiving account's own import ("Payment Thank You"). If that never
    happens, the money left but the destination never reported it \u2014 its feed is
    disconnected, or the payment went somewhere else.

    Only inflows older than `grace_days` are reported, since feeds lag the bank
    by a few days.
    """
    today = today or date.today()
    by_id = {str(t.id): t for t in txs}
    issues = []
    for t in txs:
        if not t.transfer_account_id or t.amount <= 0:
            continue
        if not _is_uncleared(t) or t.import_id or getattr(t, "matched_transaction_id", None):
            continue
        t_date = t.var_date if isinstance(t.var_date, date) else date.fromisoformat(str(t.var_date))
        if (today - t_date).days < grace_days:
            continue
        sender = by_id.get(str(getattr(t, "transfer_transaction_id", "")))
        if not sender or not (sender.import_id or getattr(sender, "matched_transaction_id", None)):
            continue
        issues.append({
            "type": "unconfirmed_payment",
            "severity": "high",
            "description": (
                f"{format_amount(t.amount)} sent from {sender.account_name} on {t_date}"
                f" never appeared in {t.account_name}'s own feed"
            ),
            "date": str(t_date),
            "amount": t.amount,
            "account": t.account_name,
            "account_id": str(t.account_id),
            "transaction_id": str(t.id),
            "from": sender.account_name,
            "related_id": str(sender.id),
        })
    return issues


def _find_mirrored_adjustments(txs: list, window_days: int = 7) -> list[tuple]:
    """Pair reconciliation adjustments that cancel out across two accounts.

    Reconciling an account whose balance is off books the difference as a
    "Reconciliation Balance Adjustment". When a payment was linked to the wrong
    card, one card is off by +X and the other by -X, and reconciling both hides
    the misrouting behind two adjustments. Returns `(negative, positive)` pairs
    on different accounts, equal and opposite, within `window_days`.
    """
    adjustments = [
        t for t in txs
        if (t.payee_name or "").lower().startswith("reconciliation balance adjustment") and t.amount
    ]
    pairs, used = [], set()
    for neg in (a for a in adjustments if a.amount < 0):
        n_date = neg.var_date if isinstance(neg.var_date, date) else date.fromisoformat(str(neg.var_date))
        for pos in adjustments:
            if id(pos) in used or pos.amount != -neg.amount or str(pos.account_id) == str(neg.account_id):
                continue
            p_date = pos.var_date if isinstance(pos.var_date, date) else date.fromisoformat(str(pos.var_date))
            if abs((p_date - n_date).days) <= window_days:
                pairs.append((neg, pos))
                used.add(id(pos))
                break
    return pairs


@app.command("transfers")
def transfers_txs(
    unlinked: bool = typer.Option(False, "--unlinked", help="Only show payments routed to the wrong card"),
    days: int = typer.Option(7, "--days", help="How far apart a payment and the card's own import may be"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Check credit-card payments are linked to the card that actually got paid.

    When a bank's autopay description does not name the card, YNAB picks one.
    With several cards from the same issuer it picks wrong, and the mistake is
    invisible in the register: both sides look like an ordinary transfer. Only
    the receiving card's own balance gives it away, drifting further off each
    month.

    Fixing is a UI task — repoint the payment to the right card, then use Match
    to merge it with that card's existing import. The API has no match endpoint,
    and deleting instead of matching discards bank-imported data.
    """
    try:
        err_console.print("[dim]Fetching all transactions...[/dim]")
        txs = [t for t in client.list_transactions(budget_id=budget) if not t.deleted]
        accounts = client.list_accounts(budget_id=budget)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    issues = _find_misrouted_payments(txs, accounts, window_days=days)
    unconfirmed = [] if unlinked else _find_unconfirmed_payments(txs)

    if json_output:
        print_json(issues + unconfirmed)
        return

    if unconfirmed:
        table = Table(title=f"Payments the receiving account never confirmed ({len(unconfirmed)})")
        table.add_column("Date")
        table.add_column("Amount", justify="right")
        table.add_column("From")
        table.add_column("To", style="yellow")
        for i in sorted(unconfirmed, key=lambda x: (x["account"], x["date"])):
            table.add_row(i["date"], format_amount(i["amount"]), i["from"].strip()[:24], i["account"].strip()[:24])
        console.print(table)
        console.print(
            f"[dim]Total: {format_amount(sum(i['amount'] for i in unconfirmed))}. The money left, but the"
            " receiving feed never reported it: reconnect that account, or check its statement.[/dim]\n"
        )

    if not issues:
        console.print("[green]\u2713[/green] Every card payment is linked to the card that received it.")
        return

    table = Table(title=f"Misrouted card payments ({len(issues)})")
    table.add_column("Date")
    table.add_column("Amount", justify="right")
    table.add_column("Linked to", style="red")
    table.add_column("Should be", style="green")
    for i in sorted(issues, key=lambda x: x["date"]):
        table.add_row(
            i["date"],
            format_amount(i["amount"]),
            i["linked_to"][:24],
            i["should_be"][:24],
        )
    console.print(table)
    total = sum(abs(i["amount"]) for i in issues)
    console.print(f"[dim]Total misrouted: {format_amount(total)}[/dim]")
    console.print(
        "\n[dim]Fix in the YNAB UI: repoint each payment to the correct card,"
        " then select both rows and use Match.[/dim]"
    )


def _apply_rules(txs: list, rules: list) -> list[tuple]:
    """Pair each uncategorized transaction with the first rule that matches.

    Returns (transaction, rule) pairs. Order matters: the first match wins, so a
    specific rule above a broad one overrides it.
    """
    matched = []
    for t in txs:
        payee = t.payee_name or ""
        for rule in rules:
            if rule.exclude and re.search(rule.exclude, payee, re.I):
                continue
            if re.search(rule.match, payee, re.I):
                matched.append((t, rule))
                break
    return matched


@app.command("categorize-rules")
def categorize_by_rules(
    apply: bool = typer.Option(False, "--apply", help="Write the categories (previews by default)"),
    json_output: bool = JsonFlag,
    budget: str | None = BudgetOpt,
):
    """Categorize uncategorized transactions using saved payee rules.

    Rules live in ~/.config/ynab-cli/rules.json because they describe one
    person's payees. Each is a regular expression tested against the payee name;
    the first match wins, so a specific rule above a broad one overrides it.

    This previews by default, and that is deliberate. Substring rules match far
    more than intended — a rule for airlines catches any payee containing "air",
    including a dance studio named Astaire. Read the preview before applying.
    """
    import ynab as ynab_sdk

    from ..config import load_rules

    rules = load_rules()
    if not rules:
        console.print("[yellow]No rules defined.[/yellow]")
        console.print("[dim]Create ~/.config/ynab-cli/rules.json with: "
                      '{"rules": [{"match": "cafe|coffee", "category": "Eating Out"}]}[/dim]')
        return

    try:
        txs = [t for t in client.list_transactions(budget_id=budget) if not t.deleted]
        # Tracking accounts sit outside the budget: their transactions never take a category.
        off_budget = {str(a.id) for a in client.list_accounts(budget_id=budget) if not a.on_budget}
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    uncategorized = [
        t for t in txs
        if t.category_id is None
        and not t.transfer_account_id
        and str(t.account_id) not in off_budget
        and "Starting Balance" not in (t.payee_name or "")
    ]
    matched = _apply_rules(uncategorized, rules)

    if json_output:
        print_json([{
            "transaction_id": str(t.id),
            "date": str(t.var_date),
            "amount": t.amount,
            "payee": t.payee_name,
            "rule": r.match,
            "category": r.category,
        } for t, r in matched])
        return

    if not matched:
        console.print(
            f"[dim]No rule matched any of the {len(uncategorized)} uncategorized"
            " transaction(s).[/dim]"
        )
        return

    table = Table(title=f"{'Applying' if apply else 'Preview'} — {len(matched)} match(es)")
    table.add_column("Date")
    table.add_column("Amount", justify="right")
    table.add_column("Payee")
    table.add_column("Category", style="green")
    table.add_column("Matched by", style="dim")
    for t, r in sorted(matched, key=lambda x: str(x[0].var_date)):
        table.add_row(
            str(t.var_date),
            format_amount(t.amount),
            (t.payee_name or "?")[:28],
            r.category[:20],
            r.match[:22],
        )
    console.print(table)
    console.print(f"[dim]{len(uncategorized) - len(matched)} transaction(s) matched no rule.[/dim]")

    if not apply:
        console.print("\n[dim]Check each row, then apply with: ynab tx categorize-rules --apply[/dim]")
        return

    resolved: dict[str, str | None] = {}
    api = ynab_sdk.TransactionsApi(client._get_client())
    bid = client._budget_id(budget)
    ok = failed = 0
    for t, r in matched:
        if r.category not in resolved:
            resolved[r.category] = _resolve_category(r.category, budget_id=budget)
        cat_id = resolved[r.category]
        if not cat_id:
            console.print(f"[red]  ✗ no category matching '{r.category}'[/red]")
            failed += 1
            continue
        try:
            api.update_transaction(bid, str(t.id), ynab_sdk.PutTransactionWrapper(
                transaction=ynab_sdk.ExistingTransaction(category_id=cat_id)))
            ok += 1
        except Exception as e:
            console.print(f"[red]  ✗ {t.payee_name}: {e}[/red]")
            failed += 1
    console.print(f"\n[green]✓[/green] Categorized {ok}" + (f", {failed} failed" if failed else ""))
