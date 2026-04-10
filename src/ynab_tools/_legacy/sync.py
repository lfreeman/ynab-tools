"""Budget sync orchestration."""

from typing import Optional

from . import api, storage


# Entity types that contain lists of items with IDs
ENTITY_TYPES = [
    "accounts",
    "categories",
    "category_groups",
    "payees",
    "payee_locations",
    "transactions",
    "subtransactions",
    "scheduled_transactions",
    "scheduled_subtransactions",
    "months",
]


def merge_budget_data(existing: dict, delta: dict) -> dict:
    """
    Merge delta changes into existing budget data.

    Args:
        existing: The current full budget data
        delta: The delta containing only changed entities

    Returns:
        Merged budget data
    """
    for entity_type in ENTITY_TYPES:
        delta_items = delta.get(entity_type)
        if not delta_items:
            continue

        # Build lookup by ID for existing entities
        existing_list = existing.get(entity_type) or []
        by_id = {item["id"]: item for item in existing_list}

        # Update/insert from delta
        for item in delta_items:
            if item.get("deleted"):
                # Remove deleted entities
                by_id.pop(item["id"], None)
            else:
                # Update or insert
                by_id[item["id"]] = item

        # Replace list with merged data
        existing[entity_type] = list(by_id.values())

    # Update metadata fields
    for field in ["last_modified_on", "first_month", "last_month"]:
        if delta.get(field):
            existing[field] = delta[field]

    return existing


def sync_budget(budget_id: str, name: str, client=None) -> dict:
    """
    Sync a single budget.

    Args:
        budget_id: The budget ID
        name: The budget name
        client: Optional YNAB API client

    Returns:
        dict with sync results
    """
    # Check for existing sync state
    last_knowledge = storage.get_server_knowledge(budget_id)
    is_delta = last_knowledge is not None

    # Fetch from API
    budget_data, server_knowledge = api.get_budget(
        budget_id,
        last_knowledge_of_server=last_knowledge,
        client=client,
    )

    # Save raw file (full or delta)
    raw_file = storage.save_budget(budget_id, budget_data, is_delta=is_delta)

    # Merge if delta
    if is_delta:
        existing = storage.load_merged(budget_id)
        if existing:
            merged = merge_budget_data(existing, budget_data)
        else:
            # No existing merged file, treat as full
            merged = budget_data
    else:
        merged = budget_data

    # Save merged state
    merged_file = storage.save_merged(budget_id, merged)

    # Update sync state
    storage.update_sync_state(budget_id, name, server_knowledge)

    return {
        "budget_id": budget_id,
        "name": name,
        "is_delta": is_delta,
        "server_knowledge": server_knowledge,
        "raw_file": str(raw_file),
        "merged_file": str(merged_file),
        "transactions": len(merged.get("transactions") or []),
    }


def sync_all(client=None) -> list[dict]:
    """
    Sync all budgets.

    Returns:
        List of sync results for each budget
    """
    if client is None:
        client = api.get_client()

    budgets = api.list_budgets(client)
    results = []

    for budget in budgets:
        result = sync_budget(budget.id, budget.name, client)
        results.append(result)

    return results


def rebuild_merged(budget_id: str) -> Optional[dict]:
    """
    Rebuild merged.json from full + delta files.

    TODO: Implement this to replay all files in order.
    """
    raise NotImplementedError("rebuild_merged not yet implemented")
