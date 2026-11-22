# YNAB AI Assistant

Personal finance AI assistant that connects to YNAB (You Need A Budget) API.

## Project Overview

Learning project for AI/ML engineering - building an intelligent assistant for personal finance analysis using local LLMs.

## Reference

- Audit procedure and decision rules: `skills/ynab/SKILL.md`
- Per-budget private data (card map, priorities, payee rules) lives in
  `~/.config/ynab-cli/`, never in this repo — see README

## Do not use Claude Code memory for this project

Nothing durable belongs in `~/.claude/.../memory/`. It is tied to one machine,
cannot be moved with the project, and splits knowledge across places that drift
apart. Everything has a home that travels:

| Kind of thing | Goes to |
|---|---|
| A payee always categorized a certain way | `~/.config/ynab-cli/rules.json` |
| A card number, funding priority, account fact | `~/.config/ynab-cli/profile.json` |
| A procedure or rule for working on a budget | `skills/ynab/SKILL.md` |
| An API quirk or SDK gotcha | this file, under YNAB API Notes |

Prefer whichever the code can act on. A payee rule in `rules.json` is applied
automatically; the same rule written as prose has to be remembered and can be
forgotten.

The one exception is a transient session handoff — where work stopped, what is
still pending. That is deliberately throwaway, auto-loads next session, and
should be deleted once acted on. Nothing in it should be worth keeping.

## Tech Stack

- **Python 3.12** with `uv` for package management
- **YNAB SDK** (`ynab` package) for API access
- **Ollama** for local LLM inference (Phase 1+)
- **ChromaDB** for vector storage (Phase 2+)
- **FastAPI** for API layer (Phase 3+)

## CLI Commands

```
# Daily workflow
ynab routine                     # Daily health check — shows everything needing attention
ynab routine --monthly           # Monthly deep check — targets, scheduled txs, reconciliation

# Budget management
ynab budget list / use           # List budgets, set default
ynab budget plan                 # Plan view — assigned, activity, available by category
ynab budget check [--month]      # Health check — overspent, CC mismatches, AoM, goals
ynab budget assign               # Preview RTA assignment; --apply --plan FILE writes a reviewed plan

# Money questions (agent-first: run with --json)
ynab money goals [--apply]       # Size and create safety-net, car and school goal categories
ynab money available             # How much money is really free
ynab money split                 # Keep in checking / move to savings / invest

# Scheduled transactions
ynab scheduled list              # View all recurring scheduled transactions
ynab scheduled check             # Compare scheduled amounts vs recent actuals
ynab scheduled prune / delete    # Remove schedules that generate ghosts / one schedule

# Snapshots (files in ~/.config/ynab-cli/snapshots/)
ynab snapshot save [--months N] [--label TEXT]  # Save months, categories, account balances
ynab snapshot list               # Saved snapshots, newest first
ynab snapshot diff OLD [NEW]     # Changes; OLD/NEW = name prefix, latest, previous; no NEW = live

# Accounts & categories
ynab status                      # Connection check
ynab acct list                   # Account balances
ynab cat list                    # Category budgets
ynab payee list                  # Payee lookup

# Transaction management
ynab tx list                     # List transactions (--uncategorized, --unapproved, --group-by-payee, --original)
ynab tx show / approve           # Show details, approve (--all for bulk)
ynab tx categorize / categorize-payee  # Single or batch categorization by payee
ynab tx move / update            # Move between accounts, update fields
ynab tx audit                    # Data integrity: duplicates, orphaned transfers, stale uncleared

# Financial reports
ynab report spending             # Spending by category/group
ynab report income               # Income analysis
ynab report payees               # Top payees by spend
ynab report trends               # Month-over-month trends
ynab report subscriptions        # Recurring charge detection
```

## Daily & Monthly Routine

**Daily (1-2 min):** Run `ynab routine`. Fix anything flagged:
1. Categorize uncategorized transactions
2. Approve unapproved transactions
3. Cover overspending
4. Check budget before purchases (YNAB mobile app)

**Monthly (15-30 min):** Run `ynab routine --monthly`. Then:
1. Reconcile accounts in YNAB UI, including investment tracking accounts
   (Robinhood, Vanguard) with the broker's current value — YNAB never imports them
2. `ynab budget assign` — assign RTA to categories
3. `ynab scheduled check` — verify recurring bill amounts
4. `ynab tx audit` — check data integrity
5. `ynab report spending` / `ynab report trends` — review month
6. Review/adjust targets in YNAB UI

## Project Structure

```
src/ynab_tools/
  cli.py              # Main CLI entry point (typer)
  config.py           # Config loading (.env, config.json)
  api/client.py       # YNAB API wrapper
  commands/
    budget.py          # budget list, use, plan, check, assign
    acct.py            # account list
    cat.py             # category list
    payee.py           # payee list
    tx.py              # transaction management + audit
    report.py          # spending, income, payees, trends, subscriptions
    scheduled.py       # scheduled transaction list + check
    routine.py         # daily/monthly routine checks
    money.py           # money goals, available, split
    snapshots.py       # snapshot save, list, diff
  money/               # Money answers: snapshot, data-quality pass, answer shape,
                       # goals, available, split, assign
  utils/formatting.py  # Amount formatting helpers
tests/                 # Unit tests (pytest, mocked API)
notebooks/explore.ipynb
```

## Environment

- API key stored in `~/.config/ynab-cli/.env` (never commit)
- Config stored in `~/.config/ynab-cli/config.json`

## YNAB API Notes

- Amounts are in "milliunits" - divide by 1000 for dollars
- Budget ID required for most endpoints — set default with `ynab budget use`
- Rate limit: 200 requests per hour
- Batch update (`update_transactions`) returns None due to SDK 209 status handling — don't rely on return value
- `transfer_account_id` and `transfer_transaction_id` are read-only — change transfers by changing `payee_id`
- Deleting one side of a transfer deletes BOTH sides
- Changing a transfer's payee to a regular payee: keeps the changed transaction, deletes the counterpart
- Month endpoint (`get_month`) returns categories WITH `category_group_name` — transaction endpoints do NOT
- Budget assignment uses `update_month_category` with `PatchMonthCategoryWrapper`
- Category rename uses `update_category` with `PatchCategoryWrapper` + `ExistingCategory`
- Category group create uses `create_category_group` with `PostCategoryGroupWrapper`
- Scheduled transactions use `create_scheduled_transaction` with `PostScheduledTransactionWrapper`
- Transaction `import_id` = bank-imported; no `import_id` = manually entered or YNAB-generated
- Transaction date field is `var_date` (not `date`) due to SDK naming
- Account rename is NOT supported via API — UI only
- Category hiding: move to "Hidden Categories" group via `category_group_id`
- Reconciled transactions cannot be updated — the API returns 404
- Matching an import to a scheduled entry CLEARS `import_id` and sets
  `matched_transaction_id`; check both before concluding an entry is a ghost
- Each account has a `transfer_payee_id`; setting a transaction's `payee_id` to
  it creates a transfer and auto-generates the counterpart
- The API has no "match" operation — `matched_transaction_id` is read-only, so
  merging a duplicate pair is UI-only
- A credit-card `balance` is negative when money is owed and positive when the
  card is in credit; never take its absolute value
- A goal created or updated through the API (`goal_target`) becomes a monthly
  "Needed for Spending" (NEED) target; "Have a balance of … by date" (TB/TBD)
  can only be chosen in the app, and `goal_overall_left` is only meaningful
  once it is
- Investment and retirement accounts never import through Direct Import, even
  when shown as linked; their balance changes only on a manual reconcile
- A created transaction's `payee_name` must not start with an internal payee
  name ("Transfer : ", "Starting Balance", "Manual Balance Adjustment",
  "Reconciliation Balance Adjustment") — the API returns 400; use a regular
  payee such as "Vanguard Market Value" for a balance update

## Current Phase

Phase 1: Foundation — CLI tools built, budget organized, scheduled transactions set up. Next: SQLite cache, Ollama integration, auto-categorization.

## Code Style

- Rich tables for CLI output, typer for commands
- `--json` flag on all list/report commands
- `--budget` / `-b` flag for budget ID override
- Type hints for all functions
- Tests use mocked API client
- Run `uv run ruff check src/ tests/` for linting
- Run `uv run pytest tests/ -v` for tests
