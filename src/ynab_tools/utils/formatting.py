"""Formatting utilities for YNAB data."""


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
