"""JSON file storage management."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
SYNC_STATE_FILE = DATA_DIR / "sync_state.json"


def ensure_dirs() -> None:
    """Create data directories if they don't exist."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def get_budget_dir(budget_id: str) -> Path:
    """Get the directory for a specific budget."""
    budget_dir = RAW_DIR / f"budget_{budget_id[:8]}"
    budget_dir.mkdir(parents=True, exist_ok=True)
    return budget_dir


def save_budget(
    budget_id: str,
    data: dict,
    is_delta: bool = False,
) -> Path:
    """
    Save budget data to a timestamped JSON file.

    Args:
        budget_id: The budget ID
        data: The budget data dict
        is_delta: If True, save as delta file; otherwise as full

    Returns:
        Path to the saved file
    """
    budget_dir = get_budget_dir(budget_id)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = "delta" if is_delta else "full"
    filename = budget_dir / f"{prefix}_{timestamp}.json"

    with open(filename, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return filename


def save_merged(budget_id: str, data: dict) -> Path:
    """Save the merged/current state of a budget."""
    budget_dir = get_budget_dir(budget_id)
    filename = budget_dir / "merged.json"

    with open(filename, "w") as f:
        json.dump(data, f, indent=2, default=str)

    return filename


def load_merged(budget_id: str) -> Optional[dict]:
    """Load the merged state of a budget, if it exists."""
    budget_dir = get_budget_dir(budget_id)
    filename = budget_dir / "merged.json"

    if not filename.exists():
        return None

    with open(filename) as f:
        return json.load(f)


def load_sync_state() -> dict:
    """Load the sync state file."""
    if not SYNC_STATE_FILE.exists():
        return {}

    with open(SYNC_STATE_FILE) as f:
        return json.load(f)


def save_sync_state(state: dict) -> None:
    """Save the sync state file."""
    ensure_dirs()
    with open(SYNC_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_server_knowledge(budget_id: str) -> Optional[int]:
    """Get the last server knowledge for a budget."""
    state = load_sync_state()
    if budget_id in state:
        return state[budget_id].get("server_knowledge")
    return None


def update_sync_state(budget_id: str, name: str, server_knowledge: int) -> None:
    """Update sync state for a budget."""
    state = load_sync_state()
    state[budget_id] = {
        "name": name,
        "server_knowledge": server_knowledge,
        "last_sync": datetime.now().isoformat(),
    }
    save_sync_state(state)
