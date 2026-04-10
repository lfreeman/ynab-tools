"""YNAB API wrapper."""

import os
from typing import Optional

import ynab
from dotenv import load_dotenv


def get_client() -> ynab.ApiClient:
    """Create and return a configured YNAB API client."""
    load_dotenv()

    api_key = os.getenv("YNAB_API_KEY")
    if not api_key:
        raise ValueError("YNAB_API_KEY not found in environment")

    configuration = ynab.Configuration(access_token=api_key)
    return ynab.ApiClient(configuration)


def get_budgets_api(client: Optional[ynab.ApiClient] = None) -> ynab.BudgetsApi:
    """Get the Budgets API."""
    if client is None:
        client = get_client()
    return ynab.BudgetsApi(client)


def list_budgets(client: Optional[ynab.ApiClient] = None) -> list:
    """List all budgets."""
    api = get_budgets_api(client)
    response = api.get_budgets()
    return response.data.budgets


def get_budget(
    budget_id: str,
    last_knowledge_of_server: Optional[int] = None,
    client: Optional[ynab.ApiClient] = None,
) -> tuple[dict, int]:
    """
    Fetch a budget by ID.

    Returns:
        tuple: (budget_dict, server_knowledge)
    """
    api = get_budgets_api(client)
    response = api.get_budget_by_id(
        budget_id,
        last_knowledge_of_server=last_knowledge_of_server,
    )
    budget_dict = response.data.budget.to_dict()
    server_knowledge = response.data.server_knowledge
    return budget_dict, server_knowledge
