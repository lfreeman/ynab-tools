"""One read of the budget that every money answer is computed from (KTD3)."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from ..api import client
from ..config import Profile, load_profile

# How far back transactions are read: enough for the detectors' windows and for
# a statement up to about four months old to be compared as of its date.
HISTORY_DAYS = 120


@dataclass
class Snapshot:
    """The budget as read once for one answer.

    `month` is the first day of the current month and `month_detail` is the
    SDK's month detail for it (its categories carry `category_group_name`).
    `accounts` excludes deleted accounts but keeps closed ones. `transactions`
    holds every non-deleted transaction dated on or after `since`, across all
    accounts. `today` is the date every age and grace period is measured from.
    """

    month: date
    month_detail: Any
    accounts: list
    transactions: list
    profile: Profile
    today: date
    since: date


def load_snapshot(
    budget_id: str | None = None,
    today: date | None = None,
    history_days: int = HISTORY_DAYS,
) -> Snapshot:
    """Read the current month, the accounts and `history_days` of transactions.

    Makes exactly three API requests and reads the local profile. Raises
    `RuntimeError` when no API key or budget is configured, and
    `ProfileError` when profile.json holds a malformed value.
    """
    today = today or date.today()
    month = today.replace(day=1)
    since = today - timedelta(days=history_days)
    month_detail = client.get_month(month.isoformat(), budget_id=budget_id)
    accounts = [a for a in client.list_accounts(budget_id=budget_id) if not a.deleted]
    transactions = [
        t for t in client.list_transactions(budget_id=budget_id, since_date=since.isoformat())
        if not t.deleted
    ]
    return Snapshot(
        month=month,
        month_detail=month_detail,
        accounts=accounts,
        transactions=transactions,
        profile=load_profile(),
        today=today,
        since=since,
    )
