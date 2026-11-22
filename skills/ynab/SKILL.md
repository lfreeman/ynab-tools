---
name: ynab
description: Manage and repair a YNAB budget with the `ynab` CLI — the daily and monthly routine, categorizing and approving transactions, assigning money to categories, and finding duplicates the bank never confirmed, card payments linked to the wrong card, or gaps against a real bank balance. Use when asked to check, audit, reconcile, categorize, fund, clean up or catch up a YNAB budget, or when an account or card balance "looks wrong". Also use when asked how much money is really free, how much to keep in checking versus move to savings or invest, or what to assign Ready to Assign to. Do NOT use for writing code in this repo (that is ordinary development) or for advice on which investments to buy.
---

# YNAB

Working on a real budget with the `ynab` CLI. Every change moves money in
someone's records, so the order below exists to stop a wrong fix from being
built on top of another.

## Rules that override convenience

These were learned by getting them wrong. They are not style preferences.

1. **Never delete a bank-imported transaction.** `import_id` means the bank
   reported it; that is the source of truth. Only YNAB-generated entries (no
   `import_id`) may be deleted.

2. **Match, never delete, when merging a duplicate pair.** In the YNAB UI,
   select both rows and press **Match** — it merges them losslessly. Deleting
   one instead discards bank data, and for a transfer it deletes *both* sides.

3. **Deleting one side of a transfer deletes the other.** To break a link first,
   change the payee to a regular (non-transfer) payee: that keeps the edited
   transaction and removes the counterpart.

4. **Verify against the receiving account's own feed before repointing a
   payment.** The card that actually got paid will have imported the money
   itself. That import is the evidence — not the payment's description.

5. **Ask before bulk re-categorizing.** Show the list and let the user exclude
   rows first. Rule previews exist for this reason.

6. **Never reconcile to a figure you cannot account for.** Reconciling books the
   difference permanently and marks transactions reconciled, which blocks later
   edits. Explain the gap first.

7. **Always report accounts that need re-authentication.** Check at the start
   of every session and after each step (`ynab routine` lists them), and name
   them to the user — a disconnected account silently stops importing, so
   every later number is incomplete until it is reconnected in the YNAB UI.

8. **What the API cannot do, do in the YNAB web app through the Claude in
   Chrome extension — but tell the user first.** UI-only operations include
   matching a duplicate pair, reconciling, renaming an account, and choosing a
   target's type. Before the first browser action, tell the user what you are
   about to do and ask them to confirm the extension is on; wait for that
   confirmation. Show each change before making it, as with any other write.

## Order of work

Each step changes the numbers the next one reads, so do not reorder.

1. **Structural errors** — `ynab tx transfers`, then `ynab scheduled prune`,
   then `ynab tx ghosts`. Prune must run first: it finds the offending
   schedules through their ghosts, so once the ghosts are deleted it reports
   nothing. Balances are meaningless until these are right.
2. **Categorize** — `ynab tx categorize-rules` (preview, then `--apply`).
3. **Fund** — `ynab budget assign --month YYYY-MM --json`, oldest month
   first; apply each reviewed plan as described under Money questions.
4. **Approve** — only once the above is settled.
5. **Reconcile** — `ynab acct verify NAME --balance N` first; reconcile in the
   UI only when it reports nothing unexplained.

## Snapshots

Before any batch of budget fixes, run `ynab snapshot save --label "before
<change>"`. After it, run `ynab snapshot diff latest` (compares that snapshot
with the budget now) and attribute every change in Ready to Assign to a fix you
made. A change you cannot attribute is a finding, not noise.

## Commands

```bash
ynab routine                        # daily; --monthly for the deeper pass
ynab tx ghosts [--delete]           # entries the bank never confirmed
ynab tx transfers                   # payments linked to the wrong card, or never confirmed
ynab scheduled prune [--delete]     # the schedules generating those ghosts
ynab scheduled delete PAYEE|ID      # remove one schedule
ynab budget check --month YYYY-MM   # is a past month correct?
ynab acct verify NAME --balance N [--date D --save]  # explain a YNAB-vs-bank gap; --save keeps the statement
ynab tx categorize-rules [--apply]  # rule-based categorization
ynab budget assign [--month] [--json]           # preview what to assign
ynab budget assign --apply --plan FILE          # write a reviewed plan
ynab money goals [--apply]          # size and create the safety-net and goal categories
ynab money available                # how much money is really free
ynab money split                    # keep in checking / move to savings / invest
ynab snapshot save [--label TEXT]   # save months, categories and balances before changing things
ynab snapshot diff OLD [NEW]        # what changed since; OLD/NEW: name prefix, latest, previous
```

Every command takes `--budget`/`-b` to override the default budget. Every
command except `routine` and `budget check` reads `--json`; under `--json`,
stdout holds only the JSON.

## Money questions

Each question has one command. Run it with `--json` and explain its answer;
do not recompute the figures yourself.

| Question | Command |
|---|---|
| How much money do I really have free? | `ynab money available --json` |
| How much should stay in checking, and what should move to savings or be invested? | `ynab money split --json` |
| What should Ready to Assign go to? | `ynab budget assign --json`, then apply |
| How big should the safety net and goals be? Set them up. | `ynab money goals --json`, then `--apply` |

Reading an answer (amounts are milliunits; divide by 1000):

- **`status`** — `exact` only when no data problem was found. On `approximate`,
  say so before giving the number.
- **`warnings`** — name each one with its `bound` (the most it could move the
  answer) and its `fix`. A null bound means the effect is unknown; say that the
  figure cannot be bounded until it is fixed.
- **`figures`** — each carries its `formula`, `inputs` and `uncertainty`.
  Quote the formula when the owner asks why. Say "estimate" for any figure with
  `estimate: true`.
- **`ui_actions`** — changes only the YNAB app can make, such as switching a
  goal's target type to "Have a balance of … by date". Follow rule 8.
- **`next_actions`** — transfers for the owner to make at the bank. The tool
  never moves money; present the amounts and let the owner act.

Assigning Ready to Assign is a preview, approve, apply loop:

1. Run `ynab budget assign --json` and show the items to the owner.
2. Save that JSON to a file. Apply the owner's edits there: change an item's
   `to`, or remove it.
3. Run `ynab budget assign --apply --plan FILE` only after the owner approves.
4. On `stale_plan`, the budget changed since the preview: preview again. A
   rerun of an applied plan writes nothing.

## Diagnosing a wrong balance

Work from the signature, not the amount.

**A card balance that is positive** (or far off) usually means payments were
credited to it that it never received. Run `ynab tx transfers`. The giveaway is
a low-activity card accumulating large payments — compare each card's real
spending against the payments landing on it.

**A category spending twice what it should** usually means a ghost. A scheduled
transaction holds one fixed amount; when the real bill varies, YNAB cannot match
them and leaves a duplicate. Run `ynab scheduled prune --delete` to stop it
recurring, then `ynab tx ghosts --delete`.

**An account that will not reconcile**: run `ynab acct verify`. It separates
ghosts from genuinely missing data. If something is still unexplained, the bank
holds activity YNAB never received — find it before reconciling.

## Things that mislead

- **`import_id` absent does not prove a ghost.** Matching an import to a
  scheduled entry *clears* `import_id` and sets `matched_transaction_id`. Check
  both fields.
- **Matching duplicates on amount fails.** The scheduled amount and the real
  charge differ — that mismatch is the whole cause. Match on payee and date.
- **Substring payee rules over-match.** A rule for airlines catches a payee
  containing "air"; truncating payee names collides unrelated bills that share a
  prefix like "Online Payment to". Always read the preview.
- **A positive credit-card balance is credit, not debt.** Do not take its
  absolute value.
- **`budget assign` needs Ready to Assign above zero.** A past month at zero
  cannot be fixed by assigning; money must be moved between that month's
  categories.
- **A payment to a card YNAB does not track looks like a misdirected one.**
  When checking pays a card that has no YNAB account, the payment shows up as
  an unconfirmed payment to whichever tracked card it was linked to. Check every
  card at that issuer before repointing it.
- **A linked investment account is not being updated.** YNAB never imports
  transactions or value changes from investment or retirement accounts
  (brokerage, IRA, HSA, crypto); a link can show as connected while the balance
  stays at its starting value. Keep these accounts unlinked and Reconcile each
  with the broker's current value monthly — `ynab routine --monthly` lists the
  ones not updated in 30 days. The stale-feed check skips them on purpose: it
  only covers on-budget accounts.

## Private data

Nothing budget-specific belongs in this repo. Card maps, funding priority,
goals, the essential-category set, statement balances, thresholds and payee
rules live in `~/.config/ynab-cli/` — see the README's configuration and
machine-transfer sections.
