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
ynab budget check --month 2026-03  # Check a past month: its balances and only its transactions
ynab budget assign               # Preview assigning RTA: overspent -> cards -> needs -> goals
ynab budget assign --json > plan.json            # Save the plan to review or edit
ynab budget assign --apply --plan plan.json      # Write the reviewed plan
```

### Money

```bash
ynab money goals                 # Size the safety net, car and school goals (preview)
ynab money goals --apply         # Create or update their categories and targets
ynab money available             # How much money is really free
ynab money split                 # Keep in checking / move to savings / invest
```

Each answer lists the data problems that could make it wrong and by how much.

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
ynab tx ghosts                   # Find scheduled entries the bank never confirmed
ynab tx ghosts --delete          # Remove them (refuses while a schedule still produces them)
ynab tx transfers                # Card payments linked to the wrong card, or never confirmed
ynab tx categorize-rules         # Preview rule-based categorization
ynab tx categorize-rules --apply # Apply it
```

### Scheduled Transactions

```bash
ynab scheduled list              # View all recurring scheduled transactions
ynab scheduled check             # Compare scheduled amounts vs recent actuals (flags >10% diff)
ynab scheduled prune             # Find schedules that duplicate an imported bill
ynab scheduled prune --delete    # Remove them — run before tx ghosts --delete
ynab scheduled delete <payee|id> # Delete one schedule (refuses if ambiguous)
```

### Snapshots

```bash
ynab snapshot save               # Save this month + 12 before it, categories and account balances
ynab snapshot save --months 3 --label "before fixes"
ynab snapshot list               # Saved snapshots, newest first
ynab snapshot diff latest        # Changes since the latest snapshot (reads the budget now)
ynab snapshot diff previous latest  # Changes between two saved snapshots (name prefix also works)
```

Snapshots are JSON files (amounts in milliunits, mode 600) in
`~/.config/ynab-cli/snapshots/`, named by the time taken.

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
ynab acct verify NAME --balance N  # Explain the gap between YNAB and your bank
ynab acct verify NAME --balance N --date D --save  # Also keep it as the account's statement
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

Then in YNAB UI: reconcile accounts, review/adjust targets. Reconcile investment
tracking accounts with the broker's current value too — YNAB never imports them,
and `ynab routine --monthly` lists any not updated in 30 days.

## Duplicate detection

Scheduled transactions hold one fixed amount. When the real bill varies, YNAB
cannot match the entry it auto-creates to the charge that imports, leaving an
unconfirmed duplicate that inflates the category every month.

`tx ghosts` finds these by signature — uncleared, no `import_id`, no
`matched_transaction_id`, sitting beside a real charge from the same payee.
Amounts are deliberately not compared, since the mismatch is the whole cause.
Entries with no real charge nearby are reported separately and never deleted:
those may be a payment that genuinely stopped.

`scheduled prune` addresses the source, listing the schedules responsible. It
finds them through their ghosts, so run it before `tx ghosts --delete` — which
refuses to run while such a schedule remains, unless given `--keep-schedules`.

## Configuration

| File | Holds |
|------|-------|
| `~/.config/ynab-cli/.env` | API key |
| `~/.config/ynab-cli/config.json` | Default budget |
| `~/.config/ynab-cli/profile.json` | Card last-4 map, funding priority, essentials, goals, statements |
| `~/.config/ynab-cli/rules.json` | Payee categorization rules |

Everything describing a specific budget lives outside the repository. All of it
is optional — commands fall back to built-in behaviour when a file is absent.

`profile.json`:

```json
{
  "card_last4": { "1234": "My Credit Card" },
  "funding_priority": ["Housing", "Transportation", "Food & Dining"],
  "essential_categories": ["Rent", "Utilities", "Groceries", "Insurance"],
  "college_categories": ["Tuition"],
  "savings_held_groups": ["Savings Goals"],
  "goals": {
    "safety_net": { "category": "Safety Net" },
    "car": { "category": "Next Car", "amount": 20000, "by": "2027-06-01" },
    "school": { "category": "Tuition" }
  },
  "goal_group": "Savings Goals",
  "safety_net_months": 6,
  "checking_cushion_months": 0.5,
  "statement_max_age_days": 45,
  "statements": {
    "My Checking": { "balance": 1000.00, "on": "2026-01-31" }
  }
}
```

`funding_priority` decides what gets funded first when Ready to Assign cannot
cover everything; `budget assign` lists what it left out.
`college_categories` are the categories holding college costs: they join the
proposed essential set and are the basis of the school goal's estimate.

Amounts are in dollars and dates are `YYYY-MM-DD`. A goal without an amount and
date gets an estimate from the budget's own records. `safety_net_months`,
`checking_cushion_months` and `statement_max_age_days` default to the values
shown. `statements` holds the latest bank-statement balance per account name; a
value of the wrong type or format stops the command with an error naming the key.

`rules.json`:

```json
{
  "rules": [
    { "match": "fred astaire", "category": "Dancing" },
    { "match": "cafe|coffee", "category": "Eating Out", "exclude": "supply co" }
  ]
}
```

Rules are regular expressions tested against the payee name, first match wins —
so a specific rule above a broad one overrides it. `categorize-rules` previews
by default, which matters: a rule for airlines also matches a dance studio
named Astaire.

## Moving to another machine

Everything lives in two places: this repository, and one config directory.
Nothing depends on the machine it was set up on.

**1. Clone and install**

```bash
git clone git@github.com:<you>/ynab-tools.git
cd ynab-tools
uv sync
```

This brings the code, the tests, and the `ynab` skill in `skills/` — the
working procedures travel with the repository.

**Link the skill** so Claude Code can find it from any directory:

```bash
ln -sfn "$PWD/skills/ynab" ~/.claude/skills/ynab
```

**2. Copy your config directory**

```bash
# on the old machine
tar czf ynab-cli-config.tgz -C ~/.config ynab-cli

# on the new one
tar xzf ynab-cli-config.tgz -C ~/.config
chmod 600 ~/.config/ynab-cli/.env ~/.config/ynab-cli/profile.json ~/.config/ynab-cli/rules.json
```

That directory holds the API key, default budget, card map, funding priority and
payee rules — everything specific to one person's budget. It is the only piece
not in version control, deliberately: none of it should be published.

**Transfer it over a trusted channel.** `.env` contains a live API token. If in
doubt, copy `config.json`, `profile.json` and `rules.json`, and issue a fresh
token from YNAB's Developer Settings on the new machine.

**3. Verify**

```bash
ynab status          # API reachable, token valid
ynab budget list     # budgets visible
ynab routine         # the daily check runs
```

Starting fresh instead, with no config to copy:

```bash
mkdir -p ~/.config/ynab-cli
echo "YNAB_API_KEY=<token>" > ~/.config/ynab-cli/.env
chmod 600 ~/.config/ynab-cli/.env
ynab budget list && ynab budget use <budget-id>
```

`profile.json` and `rules.json` are optional — commands fall back to built-in
behaviour without them.

## Rate limiting

YNAB allows 200 requests per hour. The client throttles below that ceiling and
retries on 429, so a bulk operation cannot stop halfway and leave the budget
partly changed.

## Project Structure

```
skills/ynab/                 # Audit procedure and rules, travels with the repo
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
