"""Proposing how to assign Ready to Assign, and applying only a reviewed plan (R9, KTD9, KTD10).

`propose` is pure over a snapshot and returns the KTD1 answer with an `items`
list. That answer, saved to a file and possibly edited by the owner, is the
plan `apply_plan` takes. Each item records the category's assigned amount at
preview (`from`) and the absolute amount it should become (`to`).
"""

from dataclasses import replace
from datetime import date

from ..api import client
from ..utils.formatting import format_amount
from .answer import Figure, build_answer
from .available import CARD_PAYMENTS_GROUP
from .goals import FINISHED_GOAL_TYPES, goal_category, goal_keys
from .quality import check_quality
from .snapshot import Snapshot, load_snapshot

COMMAND = "budget assign"
# YNAB's internal categories reject budget updates, and letting them count as
# overspent also suppresses the credit-card step.
INTERNAL = {"uncategorized", "inflow: ready to assign", "deferred income"}
# KTD10's order; a reclaim from an overfunded card payment sits with the card step.
STEP_RANK = {"overspent": 0, "cc_mismatch": 1, "cc_overfunded": 1, "underfunded": 2, "goal": 3}

READY_TO_ASSIGN = "ready_to_assign"
TOTAL = "total"
REMAINING = "remaining"
SHORTFALL = "shortfall"
PENDING = "pending"


class PlanError(ValueError):
    """A plan file is not shaped like a `budget assign` preview; the message says where."""


def assign_answer(budget_id: str | None = None, month: date | None = None, today: date | None = None) -> dict:
    """Read the budget and return the preview answer for `month` (first day; default current).

    Makes no write. Reads the snapshot (3 requests), plus that month when it
    is not the current one. Raises what `load_snapshot` raises.
    """
    snapshot = load_snapshot(budget_id=budget_id, today=today)
    if month and month != snapshot.month:
        snapshot = replace(snapshot, month=month,
                           month_detail=client.get_month(month.isoformat(), budget_id=budget_id))
    return propose(snapshot)


def propose(snapshot: Snapshot) -> dict:
    """The assignment preview for `snapshot.month`, as the KTD1 answer plus `items`.

    Items run in KTD10 order: overspending, card payment shortfalls (and
    reclaims from overfunded card payments), this month's `goal_under_funded`,
    then the safety net and goals up to `goal_overall_left`. Each item is
    `{category_id, category, group, step, from, to}` with `to` absolute. When
    the items need more than Ready to Assign, they are kept in step order and,
    within a step, `funding_priority` group order while they fit; the rest go
    to `excluded`. Goals whose type is not TB/TBD are excluded (KTD7). A
    negative Ready to Assign gives no items and a `shortfall` figure. Makes no
    API requests.
    """
    warnings = check_quality(snapshot)
    detail = snapshot.month_detail
    profile = snapshot.profile
    rta = detail.to_be_budgeted or 0
    figures: dict[str, Figure] = {
        READY_TO_ASSIGN: Figure(value=rta, formula="to_be_budgeted", inputs={"to_be_budgeted": rta}),
    }
    rules = [
        "Order: cover overspending, then card payment shortfalls, then this month's target needs,"
        " then the safety net and goals up to their full remaining amount",
        "Each item's `to` is the absolute assigned amount; applying writes it only where the"
        " category still holds `from`",
    ]

    if rta < 0:
        figures[SHORTFALL] = Figure(value=-rta, formula="-ready_to_assign", inputs={"ready_to_assign": rta})
        answer = build_answer(
            COMMAND, snapshot.month, figures, warnings, rules_applied=rules,
            next_actions=["Ready to Assign is negative: nothing can be assigned until money is moved"
                          " out of categories or income arrives"],
        )
        answer["items"] = []
        return answer

    cats = [
        c for c in detail.categories
        if not c.deleted and not c.hidden and (c.name or "").lower() not in INTERNAL
    ]
    items: list[dict] = []
    excluded: list[dict] = []
    proposed: set[str] = set()

    def add(c, step: str, amount: int) -> None:
        budgeted = c.budgeted or 0
        items.append({
            "category_id": str(c.id),
            "category": c.name,
            "group": c.category_group_name or "Other",
            "step": step,
            "from": budgeted,
            "to": budgeted + amount,
        })
        proposed.add(str(c.id))

    for c in cats:
        if (c.balance or 0) < 0 and (c.category_group_name or "").lower() != "hidden categories":
            add(c, "overspent", -c.balance)

    # Only valid for the current month: the accounts carry today's card
    # balances, which say nothing about what a past month's payment category
    # should hold.
    if snapshot.month == snapshot.today.replace(day=1):
        # Covering overspending on a card moves that money to the card's
        # payment category, so a shortfall waits for the next preview.
        has_overspent = bool(items)
        if has_overspent:
            rules.append("Card payment shortfalls wait while overspending is covered: covering credit"
                         " overspending funds the card's payment category; preview again afterwards")
        # A card balance is negative when money is owed and positive when the
        # card is in credit; only the owed side needs funding.
        owed = {
            (a.name or "").casefold(): max(0, -(a.balance or 0))
            for a in snapshot.accounts if not a.closed and a.type == "creditCard"
        }
        for c in cats:
            if (c.category_group_name or "").lower() != CARD_PAYMENTS_GROUP:
                continue
            card_owed = owed.get((c.name or "").casefold())
            if card_owed is None or str(c.id) in proposed:
                continue
            gap = card_owed - (c.balance or 0)
            if gap > 0 and not has_overspent:
                add(c, "cc_mismatch", gap)
            elif gap < 0:
                add(c, "cc_overfunded", gap)
    else:
        rules.append("Card payment steps are skipped for a month other than the current one")

    goal_names = [goal_category(k, profile) for k in goal_keys(profile)]
    goal_folded = {n.casefold() for n in goal_names}
    for c in cats:
        under = c.goal_under_funded or 0
        if under > 0 and str(c.id) not in proposed and (c.name or "").casefold() not in goal_folded:
            add(c, "underfunded", under)

    by_name = {(c.name or "").casefold(): c for c in cats}
    for name in goal_names:
        c = by_name.get(name.casefold())
        if c is None:
            excluded.append({"item": name, "reason": "No such category; size and create it with: ynab money goals"})
        elif c.goal_type not in FINISHED_GOAL_TYPES:
            excluded.append({
                "item": c.name,
                "reason": (
                    f"Target type is {c.goal_type or 'not set'}, not TB or TBD, so its remaining amount"
                    " is only an estimate; switch the type in the YNAB app first"
                ),
            })
        elif (c.goal_overall_left or 0) > 0 and str(c.id) not in proposed:
            add(c, "goal", c.goal_overall_left)

    total = sum(i["to"] - i["from"] for i in items)
    if total > rta:
        rank = {g.lower(): n for n, g in enumerate(profile.funding_priority)}
        items.sort(key=lambda i: (STEP_RANK[i["step"]], rank.get(i["group"].lower(), len(rank))))
        kept, left = [], rta
        for i in items:
            amount = i["to"] - i["from"]
            if amount <= 0 or amount <= left:
                kept.append(i)
                left -= max(0, amount)
            else:
                excluded.append({
                    "item": i["category"],
                    "reason": f"Ready to Assign ran out: it needs {format_amount(amount)} ({i['step']})",
                })
        items = kept
        rules.append("Ready to Assign covers only part of the plan: items are kept in step order, then"
                     " by funding_priority group order, while they fit")

    changes = {f"{i['group']}: {i['category']}": i["to"] - i["from"] for i in items}
    figures[TOTAL] = Figure(value=sum(changes.values()), formula="sum(changes.values())",
                            inputs={"changes": changes})
    figures[REMAINING] = Figure(
        value=rta - figures[TOTAL].value,
        formula="ready_to_assign - total",
        inputs={"ready_to_assign": rta, "total": figures[TOTAL].value},
    )

    next_actions = []
    if items:
        next_actions.append(
            "Save this JSON as the plan file; the owner may remove items or change `to` amounts."
            " After the owner approves it, apply it with: ynab budget assign --apply --plan FILE"
        )
    answer = build_answer(COMMAND, snapshot.month, figures, warnings, rules_applied=rules,
                          excluded=excluded, next_actions=next_actions)
    answer["items"] = items
    return answer


def read_plan(plan: dict) -> tuple[str, list[dict]]:
    """The month (YYYY-MM-DD) and items of a plan, checked for shape. Raises `PlanError`.

    Each item needs a string `category_id` and integer `from` and `to`.
    """
    if not isinstance(plan, dict):
        raise PlanError("The plan must be the JSON object a `budget assign --json` preview printed")
    month = plan.get("month")
    try:
        date.fromisoformat(str(month))
    except ValueError:
        raise PlanError(f"The plan's month is not a date: {month!r}") from None
    items = plan.get("items")
    if not isinstance(items, list):
        raise PlanError("The plan has no `items` list")
    for n, item in enumerate(items):
        if not (isinstance(item, dict) and isinstance(item.get("category_id"), str)
                and isinstance(item.get("from"), int) and isinstance(item.get("to"), int)):
            raise PlanError(f"Item {n} needs a string category_id and integer from and to (milliunits)")
    return str(month), items


def apply_plan(plan: dict, budget_id: str | None = None) -> dict:
    """Apply a reviewed plan (KTD9) and return the answer, with `outcome` and per-item outcomes.

    Reads the plan's month once and checks every item before writing: a
    category already at `to` is `already_applied`; one still at `from` is
    pending; any other value makes the whole plan `stale` and nothing is
    written (`status` and `outcome` "stale_plan"). If the pending changes
    need more than the current Ready to Assign, nothing is written
    (`outcome` "refused"). Otherwise each pending item is written as its
    absolute `to`, in plan order; the first failed write stops the run
    (`outcome` "partial"; items marked `written`, `failed` with `error`, or
    `not_written`). Else `outcome` is "applied". Raises `PlanError` for a
    malformed plan and what `client.get_month` raises.
    """
    month, items = read_plan(plan)
    detail = client.get_month(month, budget_id=budget_id)
    current = {str(c.id): c.budgeted or 0 for c in detail.categories if not c.deleted}
    rta = detail.to_be_budgeted or 0

    results = []
    for item in items:
        now = current.get(item["category_id"])
        result = {**item}
        if now == item["to"]:
            result["outcome"] = "already_applied"
        elif now == item["from"]:
            result["outcome"] = "pending"
        else:
            result.update(outcome="stale", current=now)
        results.append(result)

    pending = [r for r in results if r["outcome"] == "pending"]
    changes = {r.get("category", r["category_id"]): r["to"] - r["from"] for r in pending}
    figures = {
        READY_TO_ASSIGN: Figure(value=rta, formula="to_be_budgeted", inputs={"to_be_budgeted": rta}),
        PENDING: Figure(value=sum(changes.values()), formula="sum(changes.values())",
                        inputs={"changes": changes}),
    }
    next_actions = []

    if any(r["outcome"] == "stale" for r in results):
        outcome = "stale_plan"
        next_actions.append("The plan is out of date: a category changed since the preview. Nothing was"
                            " written. Preview again with ynab budget assign and have the owner review it")
    elif figures[PENDING].value > rta:
        outcome = "refused"
        next_actions.append(
            f"The plan needs {format_amount(figures[PENDING].value)} but Ready to Assign is"
            f" {format_amount(rta)}. Nothing was written. Remove items or lower `to` amounts, or preview again"
        )
    else:
        outcome = "applied"
        for r in pending:
            if outcome == "partial":
                r["outcome"] = "not_written"
                continue
            try:
                client.update_month_category(month, r["category_id"], r["to"], budget_id=budget_id)
                r["outcome"] = "written"
            except Exception as e:  # noqa: BLE001 - any API failure stops the run and is reported
                r.update(outcome="failed", error=str(e))
                outcome = "partial"
        if outcome == "partial":
            next_actions.append("Some items were not written. Applying the same plan again skips the"
                                " written items and retries the rest")
    for r in results:
        if r["outcome"] == "pending":
            r["outcome"] = "not_written"

    answer = build_answer(COMMAND, date.fromisoformat(month), figures, [], next_actions=next_actions)
    if outcome == "stale_plan":
        answer["status"] = "stale_plan"
    answer["outcome"] = outcome
    answer["items"] = results
    return answer
