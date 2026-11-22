"""Formatting utilities for YNAB data."""

import json
import sys
from typing import Any

from rich.console import Console

# Progress lines and warnings, kept off stdout so `--json` output stays parseable.
err_console = Console(stderr=True)


def millis_to_dollars(milliunits: int) -> float:
    """Convert milliunits to dollars."""
    return milliunits / 1000


def format_amount(milliunits: int) -> str:
    """Format milliunits as dollar string."""
    dollars = milliunits / 1000
    if dollars < 0:
        return f"-${abs(dollars):,.2f}"
    return f"${dollars:,.2f}"


def format_amount_colored(milliunits: int) -> str:
    """Format amount with Rich color markup."""
    dollars = milliunits / 1000
    if dollars < 0:
        return f"[red]-${abs(dollars):,.2f}[/red]"
    return f"[green]${dollars:,.2f}[/green]"


def short_id(uuid_val) -> str:
    """Abbreviate a UUID to first 8 characters."""
    return str(uuid_val)[:8] if uuid_val else "—"


def print_json(data: Any) -> None:
    """Write `data` to stdout as indented JSON, bypassing Rich.

    Rich's console wraps long lines and interprets `[...]` as markup, both of
    which corrupt machine-readable output. Values JSON cannot encode natively
    (dates, UUIDs) are written as strings.
    """
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")
