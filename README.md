# YNAB CLI

Command-line tools for managing your YNAB (You Need A Budget) budget.

## Setup

```bash
# Install dependencies
uv sync

# Configure API key
mkdir -p ~/.config/ynab-cli
echo "YNAB_API_KEY=your-api-key-here" > ~/.config/ynab-cli/.env

# Set default budget
uv run ynab budget list
uv run ynab budget use <budget-id>
```

## Quick Start

```bash
# Daily check — shows everything needing attention
uv run ynab routine

# Monthly deep check
uv run ynab routine --monthly
```

## Commands

### Routine

```bash
ynab routine                     # Daily health check
ynab routine --monthly           # Monthly deep check (targets, scheduled txs, reconciliation)
```

### Budget

```bash
ynab budget list                 # List all budgets
ynab budget use <id>             # Set default budget
ynab budget plan                 # Plan view — assigned, activity, available by category
ynab budget check                # Health check — overspent, CC mismatches, goals, Age of Money
ynab budget assign               # Auto-assign RTA: overspent -> underfunded -> CC mismatches
ynab budget assign --dry-run     # Preview without making changes
```

### Transactions

```bash
ynab tx list                     # List recent transactions
ynab tx list --uncategorized     # Show uncategorized only
ynab tx list --unapproved        # Show unapproved only
ynab tx list --group-by-payee    # Group by payee (great for batch categorization)
ynab tx show <id>                # Transaction details
ynab tx approve <id>             # Approve a transaction
ynab tx approve --all            # Approve all unapproved
ynab tx categorize <id> <cat>    # Categorize a single transaction
ynab tx categorize-payee <payee> <cat>  # Batch categorize all transactions from a payee
ynab tx move <id> <account>      # Move transaction to different account
ynab tx audit                    # Data integrity checks (duplicates, orphans, stale)
```

### Scheduled Transactions

```bash
ynab scheduled list              # View all recurring scheduled transactions
ynab scheduled check             # Compare scheduled amounts vs recent actuals (flags >10% diff)
```

### Reports

```bash
ynab report spending             # Spending by category/group
ynab report income               # Income analysis
ynab report payees               # Top payees by total spend
ynab report trends               # Month-over-month spending trends
ynab report subscriptions        # Detect recurring charges

# Options available on all reports:
#   --months N       Number of months to analyze (default: 3)
#   --json           Output as JSON
#   --budget ID      Budget ID override
```

### Other

```bash
ynab status                      # API connection check
ynab acct list                   # Account balances
ynab cat list                    # Category budgets
ynab payee list                  # Payee lookup
```

## Daily & Monthly Workflow

**Daily (1-2 min):**

```bash
ynab routine          # Shows: uncategorized, unapproved, overspending, RTA, Age of Money
```

Fix anything flagged. Check YNAB mobile app before purchases.

**Monthly (15-30 min):**

```bash
ynab routine --monthly           # Deep check
ynab budget assign               # Assign unallocated money
ynab scheduled check             # Verify recurring bill amounts
ynab tx audit                    # Check data integrity
ynab report spending --months 1  # Review the month
ynab report trends               # Check patterns
```

Then in YNAB UI: reconcile accounts, review/adjust targets.

## Project Structure

```
src/ynab_tools/
  cli.py              # Main CLI entry point (typer)
  config.py           # Config loading
  api/client.py       # YNAB API wrapper
  commands/
    budget.py          # Budget management + auto-assign
    tx.py              # Transaction management + audit
    report.py          # Financial reports
    scheduled.py       # Scheduled transaction management
    routine.py         # Daily/monthly routine checks
    acct.py            # Account list
    cat.py             # Category list
    payee.py           # Payee list
  utils/formatting.py  # Amount formatting helpers
tests/                 # Unit tests (pytest, mocked API)
research/              # YNAB knowledge base
```

## Development

```bash
uv run ruff check src/ tests/    # Lint
uv run pytest tests/ -v          # Test
```

## Tech Stack

- Python 3.12, uv
- YNAB API via `ynab` SDK
- typer (CLI), Rich (tables/formatting)
