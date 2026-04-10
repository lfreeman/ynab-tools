# YNAB AI Assistant

Personal finance AI assistant that connects to YNAB (You Need A Budget) API.

## Project Overview

Learning project for AI/ML engineering - building an intelligent assistant for personal finance analysis using local LLMs.

See `YNAB-AI-Assistant-Project-Plan.md` for full project plan with 6 phases.

## Reference

- YNAB knowledge base: `research/ynab-knowledge-base.md`

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
ynab budget check                # Health check — overspent, CC mismatches, AoM, goals
ynab budget assign               # Auto-assign RTA: overspent -> goals -> CC mismatches

# Scheduled transactions
ynab scheduled list              # View all recurring scheduled transactions
ynab scheduled check             # Compare scheduled amounts vs recent actuals

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
1. Reconcile accounts in YNAB UI
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
  utils/formatting.py  # Amount formatting helpers
tests/                 # Unit tests (pytest, mocked API)
research/              # YNAB knowledge base (best practices, metrics, API reference)
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

## YNAB Transfer Mismatch Problem

Chase AUTOPAY descriptions ("CHASE CREDIT CRD AUTOPAY PPD ID: XXXXXX9224") don't identify which card. YNAB guesses wrong when you have multiple Chase cards. Newer format "Payment to Chase card ending in XXXX" includes the card number. Ghost transactions (no import_id, transfer counterparts) are created on the wrong card.

Detection: `tx audit` finds mismatched transfers. Card-side ghosts found by checking for transactions without import_id that are transfer counterparts.

Fix: Use YNAB UI to relink transfers. API can detect but not safely fix (deleting one side deletes both).

## Budget Organization

Category groups ordered by priority (fund top to bottom):
1. Housing — Mortgage, HOA, Electric & Gas, Water, Internet, Home Insurance, Home Maintenance
2. Transportation — Auto Insurance, Auto Loans, Gas, E-ZPass, Parking, Car Maintenance
3. Family — Dependents (Rent/Music/Utilities), Pet Insurance
4. Health — Medical, Rigoro Gym, Fitness, Health & Wellness
5. Food & Dining — Groceries, Eating Out
6. Subscriptions — Netflix, YouTube, Spotify, ChatGPT, GitHub Copilot, LeetCode, Cellphone, etc.
7. Debt — Loans
8. Savings & Goals — Savings, Investing, Vacation, Education, Home Improvement
9. Lifestyle — Clothes, Hair, Dancing, Entertainment, Hobbies, Gifts, Travel
10. Other — Fees, Tech

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
