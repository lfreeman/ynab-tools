"""YNAB API client wrapper."""

from datetime import date, timedelta
from functools import lru_cache

import ynab

from ..config import load_config
from .ratelimit import RateLimitedApiClient


@lru_cache
def _get_client() -> ynab.ApiClient:
    """Create and cache the YNAB API client."""
    config = load_config()
    if not config.api_key:
        raise RuntimeError("YNAB_API_KEY not set. Add it to .env or set as environment variable.")
    configuration = ynab.Configuration(access_token=config.api_key)
    return RateLimitedApiClient(configuration)


def _budget_id(budget_id: str | None = None) -> str:
    """Resolve budget ID: explicit arg > config default."""
    if budget_id:
        return budget_id
    config = load_config()
    if not config.default_budget_id:
        raise RuntimeError("No default budget set. Run 'ynab budget use' first.")
    return config.default_budget_id


# ── Budgets (Plans in SDK v2+) ───────────────────────────────────────────────


def list_budgets() -> list:
    """List all budgets."""
    api = ynab.PlansApi(_get_client())
    response = api.get_plans()
    return response.data.plans


# ── Accounts ─────────────────────────────────────────────────────────────────


def list_accounts(budget_id: str | None = None) -> list:
    """List all accounts for a budget."""
    api = ynab.AccountsApi(_get_client())
    response = api.get_accounts(_budget_id(budget_id))
    return response.data.accounts


# ── Categories ───────────────────────────────────────────────────────────────


def list_categories(budget_id: str | None = None) -> list:
    """List all category groups (each containing categories)."""
    api = ynab.CategoriesApi(_get_client())
    response = api.get_categories(_budget_id(budget_id))
    return response.data.category_groups


def create_category_group(name: str, budget_id: str | None = None):
    """Create a category group named `name`; returns the new group."""
    api = ynab.CategoriesApi(_get_client())
    wrapper = ynab.PostCategoryGroupWrapper(category_group=ynab.SaveCategoryGroup(name=name))
    response = api.create_category_group(_budget_id(budget_id), wrapper)
    return response.data.category_group


def create_category(name: str, category_group_id: str, budget_id: str | None = None, **fields):
    """Create a category in a group; returns the new category.

    `fields` may set any other `NewCategory` field, such as `goal_target`
    (milliunits) or `goal_target_date`.
    """
    api = ynab.CategoriesApi(_get_client())
    wrapper = ynab.PostCategoryWrapper(
        category=ynab.NewCategory(name=name, category_group_id=category_group_id, **fields)
    )
    response = api.create_category(_budget_id(budget_id), wrapper)
    return response.data.category


def update_category(category_id: str, data: dict, budget_id: str | None = None):
    """Update a category's `ExistingCategory` fields from `data`; returns the category."""
    api = ynab.CategoriesApi(_get_client())
    wrapper = ynab.PatchCategoryWrapper(category=ynab.ExistingCategory(**data))
    response = api.update_category(_budget_id(budget_id), category_id, wrapper)
    return response.data.category


def update_month_category(month: str, category_id: str, budgeted: int, budget_id: str | None = None):
    """Set a category's assigned amount for `month` (YYYY-MM-01) to `budgeted` milliunits.

    The amount replaces what is assigned; it is not added to it. Returns the category.
    """
    api = ynab.CategoriesApi(_get_client())
    wrapper = ynab.PatchMonthCategoryWrapper(category=ynab.SaveMonthCategory(budgeted=budgeted))
    response = api.update_month_category(_budget_id(budget_id), month, category_id, wrapper)
    return response.data.category


# ── Payees ───────────────────────────────────────────────────────────────────


def list_payees(budget_id: str | None = None) -> list:
    """List all payees."""
    api = ynab.PayeesApi(_get_client())
    response = api.get_payees(_budget_id(budget_id))
    return response.data.payees


# ── Transactions ─────────────────────────────────────────────────────────────


def list_transactions(
    budget_id: str | None = None,
    since_date: str | None = None,
    type: str | None = None,
) -> list:
    """List transactions with optional filters."""
    api = ynab.TransactionsApi(_get_client())
    kwargs = {}
    if since_date:
        kwargs["since_date"] = since_date
    if type:
        kwargs["type"] = type
    response = api.get_transactions(_budget_id(budget_id), **kwargs)
    return response.data.transactions


def list_transactions_by_account(
    account_id: str,
    budget_id: str | None = None,
    since_date: str | None = None,
    type: str | None = None,
) -> list:
    """List transactions for a specific account."""
    api = ynab.TransactionsApi(_get_client())
    kwargs = {}
    if since_date:
        kwargs["since_date"] = since_date
    if type:
        kwargs["type"] = type
    response = api.get_transactions_by_account(_budget_id(budget_id), account_id, **kwargs)
    return response.data.transactions


def list_transactions_by_category(
    category_id: str,
    budget_id: str | None = None,
    since_date: str | None = None,
) -> list:
    """List transactions for a specific category."""
    api = ynab.TransactionsApi(_get_client())
    kwargs = {}
    if since_date:
        kwargs["since_date"] = since_date
    response = api.get_transactions_by_category(_budget_id(budget_id), category_id, **kwargs)
    return response.data.transactions


def list_transactions_by_payee(
    payee_id: str,
    budget_id: str | None = None,
    since_date: str | None = None,
) -> list:
    """List transactions for a specific payee."""
    api = ynab.TransactionsApi(_get_client())
    kwargs = {}
    if since_date:
        kwargs["since_date"] = since_date
    response = api.get_transactions_by_payee(_budget_id(budget_id), payee_id, **kwargs)
    return response.data.transactions


def get_transaction(transaction_id: str, budget_id: str | None = None):
    """Get a single transaction by ID."""
    api = ynab.TransactionsApi(_get_client())
    response = api.get_transaction_by_id(_budget_id(budget_id), transaction_id)
    return response.data.transaction


def update_transaction(transaction_id: str, data: dict, budget_id: str | None = None):
    """Update a single transaction."""
    api = ynab.TransactionsApi(_get_client())
    wrapper = ynab.PutTransactionWrapper(transaction=ynab.ExistingTransaction(**data))
    response = api.update_transaction(_budget_id(budget_id), transaction_id, wrapper)
    return response.data.transaction


def update_transactions_batch(transactions: list[dict], budget_id: str | None = None):
    """Update multiple transactions in a single API call."""
    api = ynab.TransactionsApi(_get_client())
    wrapper = ynab.PatchTransactionsWrapper(
        transactions=[ynab.SaveTransactionWithIdOrImportId(**t) for t in transactions]
    )
    return api.update_transactions(_budget_id(budget_id), wrapper)


def approve_transaction(transaction_id: str, budget_id: str | None = None):
    """Approve a single transaction."""
    return update_transaction(transaction_id, {"approved": True}, budget_id)


def approve_transactions_batch(transaction_ids: list[str], budget_id: str | None = None):
    """Approve multiple transactions in a single API call."""
    transactions = [{"id": tid, "approved": True} for tid in transaction_ids]
    return update_transactions_batch(transactions, budget_id)


def categorize_transaction(transaction_id: str, category_id: str, budget_id: str | None = None):
    """Set category on a transaction."""
    return update_transaction(transaction_id, {"category_id": category_id}, budget_id)


# ── Months ──────────────────────────────────────────────────────────────────


def list_months(budget_id: str | None = None) -> list:
    """List all budget months with summary data."""
    api = ynab.MonthsApi(_get_client())
    response = api.get_plan_months(_budget_id(budget_id))
    return response.data.months


def get_month(month: str, budget_id: str | None = None):
    """Get a specific month's data (format: YYYY-MM-01)."""
    api = ynab.MonthsApi(_get_client())
    response = api.get_plan_month(_budget_id(budget_id), month)
    return response.data.month


def get_month_history(month: str, count: int, budget_id: str | None = None) -> list:
    """The `count` full months before `month` (YYYY-MM-01), most recent first.

    One request per month, so `count` requests in all.
    """
    current = date.fromisoformat(month)
    months = []
    for _ in range(count):
        current = (current - timedelta(days=1)).replace(day=1)
        months.append(get_month(current.isoformat(), budget_id=budget_id))
    return months


# ── Scheduled Transactions ──────────────────────────────────────────────────


def list_scheduled_transactions(budget_id: str | None = None) -> list:
    """List all scheduled/recurring transactions."""
    api = ynab.ScheduledTransactionsApi(_get_client())
    response = api.get_scheduled_transactions(_budget_id(budget_id))
    return response.data.scheduled_transactions
