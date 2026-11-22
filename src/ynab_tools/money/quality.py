"""The data-quality pass every money answer runs first (R10, KTD4, KTD8).

Each problem becomes a `DataWarning` that says how far it could move the
answer (`bound`, in milliunits, or None when unknown) and which figures it
moves (`affects`). Figure names are these canonical strings:

- `FREE` ("free"): money free to assign or move.
- `CARD_OWED` ("card_owed"): what the credit cards owe.
- `CHECKING_RECOMMENDATION` ("checking_recommendation"): what should stay in checking.
- `account_balance(account_id)` ("account_balance:<account id>"): one
  account's balance. Commands map these to their own figures; a figure built
  from a checking account's balance lists that key in `Figure.depends_on`.

Only open on-budget accounts are held to statements and re-authentication:
tracking accounts never feed a money figure (R6).
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from ..config import Statement
from ..utils.formatting import format_amount
from .snapshot import Snapshot

FREE = "free"
CARD_OWED = "card_owed"
CHECKING_RECOMMENDATION = "checking_recommendation"

# A statement gap smaller than this (one cent) is rounding, not missing data.
GAP_TOLERANCE = 10
# An account holding a balance with no bank import for this long has a dropped
# feed: YNAB does not flag it as an error, but every later figure is incomplete.
FEED_STALE_DAYS = 30


def account_balance(account_id) -> str:
    """The figure name for one account's balance."""
    return f"account_balance:{account_id}"


@dataclass
class DataWarning:
    """One data problem, bounded (KTD4).

    `bound` is the most it can change an affected figure, in milliunits, or
    None when that is unknown. `items` are the transaction ids it covers, used
    to drop duplicate findings of the same transaction.
    """

    code: str
    message: str
    bound: int | None
    affects: list[str]
    fix: str
    items: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """The warning as a JSON-ready dict."""
        return asdict(self)


def check_quality(snapshot: Snapshot) -> list[DataWarning]:
    """Run every KTD4 detector over `snapshot` and return deduped warnings.

    Codes: `ghost`, `misrouted_payment`, `unconfirmed_payment`,
    `mirrored_adjustment`, `statement_gap`, `not_checked`, `needs_reauth`,
    `not_importing`.
    A payment warning is dropped when the receiving card's recent statement
    already accounts for the payment (see `_statement_settles`).
    An empty list means the data is clean. Makes no API requests.
    """
    from ..commands.tx import (
        _find_ghosts,
        _find_mirrored_adjustments,
        _find_misrouted_payments,
        _find_unconfirmed_payments,
    )

    txs = snapshot.transactions
    warnings: list[DataWarning] = []

    ghosts, _ = _find_ghosts(txs)
    ghost_txs = [g for g, _twin in ghosts]
    for g, _twin in ghosts:
        warnings.append(DataWarning(
            code="ghost",
            message=(
                f"{format_amount(g.amount)} to {g.payee_name} on {_as_date(g.var_date)} in"
                f" {g.account_name} was never confirmed by the bank and duplicates a real charge"
            ),
            bound=abs(g.amount),
            affects=[account_balance(g.account_id), FREE],
            fix="Delete the ghost: ynab tx ghosts",
            items=[str(g.id)],
        ))

    for issue in _find_misrouted_payments(txs, snapshot.accounts):
        if _statement_settles(issue["linked_to"], issue["date"], snapshot, ghost_txs):
            continue
        warnings.append(DataWarning(
            code="misrouted_payment",
            message=issue["description"],
            bound=abs(issue["amount"]),
            affects=[CARD_OWED, CHECKING_RECOMMENDATION, FREE],
            fix=f"Relink the payment to {issue['should_be']}: ynab tx transfers",
            items=[issue["transaction_id"], issue["related_id"]],
        ))

    for issue in _find_unconfirmed_payments(txs, today=snapshot.today):
        if _statement_settles(issue["account"], issue["date"], snapshot, ghost_txs):
            continue
        warnings.append(DataWarning(
            code="unconfirmed_payment",
            message=issue["description"],
            bound=abs(issue["amount"]),
            affects=[CARD_OWED, CHECKING_RECOMMENDATION, FREE],
            fix=f"Find where the payment went and check {issue['account']}'s connection: ynab tx transfers",
            items=[issue["transaction_id"], issue["related_id"]],
        ))

    for neg, pos in _find_mirrored_adjustments(txs):
        warnings.append(DataWarning(
            code="mirrored_adjustment",
            message=(
                f"Reconciliation adjustments of {format_amount(pos.amount)} in {pos.account_name}"
                f" and {format_amount(neg.amount)} in {neg.account_name} cancel out"
            ),
            bound=abs(pos.amount),
            affects=[account_balance(neg.account_id), account_balance(pos.account_id)],
            fix="Find the payment linked to the wrong account, relink it and delete both adjustments",
            items=[str(neg.id), str(pos.id)],
        ))

    for acct, last in stale_feeds(snapshot.accounts, txs, snapshot.today):
        since = f"since {last}" if last else f"in the last {(snapshot.today - snapshot.since).days} days"
        warnings.append(DataWarning(
            code="not_importing",
            message=f"{acct.name} has not imported from the bank {since}; its recent activity may be missing",
            bound=None,
            affects=[account_balance(acct.id)],
            fix="Check the account under Bank Connections in the YNAB app and relink it",
        ))

    for acct in snapshot.accounts:
        if acct.closed or not acct.on_budget:
            continue
        if acct.direct_import_linked and acct.direct_import_in_error:
            warnings.append(DataWarning(
                code="needs_reauth",
                message=f"{acct.name}'s bank connection needs re-authentication; recent activity may be missing",
                bound=None,
                affects=[account_balance(acct.id)],
                fix="Reconnect the account in the YNAB app",
            ))
        statement = snapshot.profile.statements.get(acct.name)
        if statement and statement.on >= snapshot.since:
            gap = _statement_gap(acct, statement, txs, ghost_txs)
            if abs(gap) >= GAP_TOLERANCE:
                warnings.append(DataWarning(
                    code="statement_gap",
                    message=(
                        f"{acct.name} differs from its {statement.on} statement by"
                        f" {format_amount(gap)} after allowing for ghosts"
                    ),
                    bound=abs(gap),
                    affects=[account_balance(acct.id)],
                    fix="Explain the gap (missing or wrong transactions) before reconciling: ynab acct verify",
                ))
        if not _checked_recently(acct, statement, snapshot):
            warnings.append(DataWarning(
                code="not_checked",
                message=(
                    f"{acct.name} has no statement or reconcile in the last"
                    f" {snapshot.profile.statement_max_age_days} days, so it is not checked against the bank"
                ),
                bound=None,
                affects=[account_balance(acct.id)],
                fix="Reconcile it, or record a statement: ynab acct verify --balance X --date D --save",
            ))

    return dedupe(warnings)


def stale_feeds(accounts: list, txs: list, today: date, days: int = FEED_STALE_DAYS) -> list[tuple]:
    """Open on-budget accounts whose bank feed has stopped, with their last import date.

    An account qualifies when it holds a balance, its latest imported
    transaction in `txs` is more than `days` before `today`, and it is either
    linked or imported something in `txs`. A linked account with no import in
    `txs` at all reports None as its last date. Accounts kept by hand (never
    linked, nothing imported) and idle zero-balance accounts never qualify.
    """
    last_import: dict[str, date] = {}
    for t in txs:
        if t.import_id:
            d = _as_date(t.var_date)
            key = str(t.account_id)
            if key not in last_import or d > last_import[key]:
                last_import[key] = d
    cutoff = today - timedelta(days=days)
    stale = []
    for acct in accounts:
        if acct.closed or not acct.on_budget or not acct.balance:
            continue
        last = last_import.get(str(acct.id))
        if last is None and not acct.direct_import_linked:
            continue
        if last is None or last < cutoff:
            stale.append((acct, last))
    return stale


def stale_tracking_values(accounts: list, txs: list, today: date, days: int = FEED_STALE_DAYS) -> list[tuple]:
    """Open tracking accounts whose value was last updated more than `days` before `today`.

    YNAB never imports investment or retirement accounts, so their balance only
    changes when the owner reconciles it by hand. The last update is the later
    of `last_reconciled_at` and the newest transaction in `txs`, so a starting
    balance counts. Returns (account, last update date or None) pairs.
    Zero-balance accounts never qualify.
    """
    newest: dict[str, date] = {}
    for t in txs:
        if t.deleted:
            continue
        d = _as_date(t.var_date)
        key = str(t.account_id)
        if key not in newest or d > newest[key]:
            newest[key] = d
    cutoff = today - timedelta(days=days)
    stale = []
    for acct in accounts:
        if acct.closed or acct.on_budget or not acct.balance:
            continue
        updates = [d for d in (newest.get(str(acct.id)),) if d]
        if acct.last_reconciled_at:
            updates.append(date.fromisoformat(str(acct.last_reconciled_at)[:10]))
        last = max(updates, default=None)
        if last is None or last < cutoff:
            stale.append((acct, last))
    return stale


def dedupe(warnings: list[DataWarning]) -> list[DataWarning]:
    """Keep one warning per transaction id: the one with the larger bound.

    An unknown bound counts as larger than any known one; ties keep the
    earlier warning. A warning sharing any item with a kept one is dropped.
    Warnings without items are always kept. Original order is preserved.
    """
    ranked = sorted(
        range(len(warnings)),
        key=lambda i: (warnings[i].bound is not None, -(warnings[i].bound or 0)),
    )
    claimed: set[str] = set()
    keep: set[int] = set()
    for i in ranked:
        items = set(warnings[i].items)
        if items & claimed:
            continue
        claimed |= items
        keep.add(i)
    return [w for i, w in enumerate(warnings) if i in keep]


def balance_as_of(account, txs: list, on: date) -> int:
    """`account`'s YNAB balance at the end of `on`, in milliunits.

    Works back from the current balance by removing the account's
    transactions dated after `on`, so `txs` must hold every non-deleted
    transaction of that account dated after `on`.
    """
    after = sum(
        t.amount for t in txs
        if str(t.account_id) == str(account.id) and _as_date(t.var_date) > on
    )
    return (account.balance or 0) - after


def statement_gap(account, statement: Statement, txs: list) -> int:
    """YNAB's balance as of the statement date, less ghosts, minus the statement.

    Positive means YNAB shows more than the bank did. `txs` must hold every
    transaction of the account dated after the statement, and any ghosts on
    or before it (KTD8).
    """
    from ..commands.tx import _find_ghosts

    ghosts, _ = _find_ghosts(txs)
    return _statement_gap(account, statement, txs, [g for g, _twin in ghosts])


def _statement_gap(account, statement: Statement, txs: list, ghosts: list) -> int:
    ghost_total = sum(
        g.amount for g in ghosts
        if str(g.account_id) == str(account.id) and _as_date(g.var_date) <= statement.on
    )
    return balance_as_of(account, txs, statement.on) - ghost_total - statement.balance


def _statement_settles(account_name: str, paid_on: str, snapshot: Snapshot, ghosts: list) -> bool:
    """True if the receiving account's statement already accounts for a payment.

    Holds when `account_name` has a stored statement dated on or after
    `paid_on` (YYYY-MM-DD), within `statement_max_age_days` of `snapshot.today`
    and covered by `snapshot.transactions`, whose gap is below `GAP_TOLERANCE`:
    the bank's own balance then includes the payment.
    """
    statement = snapshot.profile.statements.get(account_name)
    account = next((a for a in snapshot.accounts if a.name == account_name), None)
    if not statement or account is None:
        return False
    cutoff = snapshot.today - timedelta(days=snapshot.profile.statement_max_age_days)
    if statement.on < max(cutoff, snapshot.since, date.fromisoformat(paid_on)):
        return False
    return abs(_statement_gap(account, statement, snapshot.transactions, ghosts)) < GAP_TOLERANCE


def _checked_recently(account, statement: Statement | None, snapshot: Snapshot) -> bool:
    """True if a statement or reconcile falls within `statement_max_age_days`."""
    cutoff = snapshot.today - timedelta(days=snapshot.profile.statement_max_age_days)
    if statement and statement.on >= cutoff:
        return True
    reconciled = account.last_reconciled_at
    return bool(reconciled) and date.fromisoformat(str(reconciled)[:10]) >= cutoff


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
