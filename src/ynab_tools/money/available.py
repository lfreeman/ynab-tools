"""How much money is really free (R5, R6, KTD5).

`available` is pure over a snapshot: it starts from YNAB's Ready to Assign and
subtracts, each as its own line, what is already committed. Every figure's
`formula` is a Python expression over its own `inputs`, so it can be
recomputed from the answer alone.
"""

from datetime import date

from ..utils.formatting import format_amount
from .answer import Figure, build_answer
from .goals import FINISHED_GOAL_TYPES, goal_category, goal_keys
from .quality import CARD_OWED, FREE, account_balance, check_quality
from .snapshot import Snapshot, load_snapshot

CARD_PAYMENTS_GROUP = "credit card payments"
UNCATEGORIZED = "uncategorized"
# YNAB's own Ready to Assign category: its balance is Ready to Assign itself.
INFLOW = "inflow: ready to assign"

READY_TO_ASSIGN = "ready_to_assign"
OVERSPENDING = "overspending"
UNCATEGORIZED_FIGURE = "uncategorized"
CARD_SHORTFALL = "card_shortfall"
STILL_NEEDED = "still_needed"


def available_answer(budget_id: str | None = None, today: date | None = None) -> dict:
    """Read the budget once (3 requests) and return the KTD1 available-money answer.

    Makes no write. Raises what `load_snapshot` raises.
    """
    return available(load_snapshot(budget_id=budget_id, today=today))


def available(snapshot: Snapshot) -> dict:
    """The available-money answer for `snapshot`, with its data-quality warnings.

    Figures, in milliunits: `ready_to_assign` (YNAB's `to_be_budgeted`),
    `overspending`, `uncategorized`, `card_shortfall`, `still_needed`, and
    `free` = the first minus the other four, negative when short (never
    clamped). Tracking accounts are listed under `excluded` and feed no figure
    (R6). A goal whose target type is not TB or TBD is sized as target minus
    balance, flagged an estimate, and gets a `ui_actions` entry (R2).
    Makes no API requests.
    """
    warnings = check_quality(snapshot)
    categories = [c for c in snapshot.month_detail.categories if not c.deleted]
    open_accounts = [a for a in snapshot.accounts if not a.closed]
    on_budget = [a for a in open_accounts if a.on_budget]
    cards = [a for a in on_budget if a.type == "creditCard"]

    rta = snapshot.month_detail.to_be_budgeted or 0
    figures: dict[str, Figure] = {
        READY_TO_ASSIGN: Figure(
            value=rta,
            formula="to_be_budgeted",
            inputs={"to_be_budgeted": rta},
            depends_on=[account_balance(a.id) for a in on_budget],
        ),
    }

    overspent = {
        _label(c): -c.balance for c in categories
        if (c.balance or 0) < 0
        and (c.category_group_name or "").lower() != CARD_PAYMENTS_GROUP
        and (c.name or "").lower() not in (UNCATEGORIZED, INFLOW)
    }
    figures[OVERSPENDING] = Figure(
        value=sum(overspent.values()),
        formula="sum(overspent.values())",
        inputs={"overspent": overspent},
    )

    uncategorized_balance = sum(
        c.balance or 0 for c in categories if (c.name or "").lower() == UNCATEGORIZED
    )
    figures[UNCATEGORIZED_FIGURE] = Figure(
        value=max(0, -uncategorized_balance),
        formula="max(0, -uncategorized_balance)",
        inputs={"uncategorized_balance": uncategorized_balance},
    )

    payment_categories = {
        (c.name or "").casefold(): c for c in categories
        if (c.category_group_name or "").lower() == CARD_PAYMENTS_GROUP
    }
    owed: dict[str, int] = {}
    funded: dict[str, int] = {}
    for card in cards:
        category = payment_categories.get((card.name or "").casefold())
        if category is None:
            continue
        # A card balance is negative when money is owed; a card in credit owes zero.
        owed[card.name] = max(0, -(card.balance or 0))
        funded[card.name] = category.balance or 0
    figures[CARD_SHORTFALL] = Figure(
        value=sum(max(0, owed[c] - funded[c]) for c in owed),
        formula="sum(max(0, owed[c] - available[c]) for c in owed)",
        inputs={"owed": owed, "available": funded},
        depends_on=[CARD_OWED, *(account_balance(a.id) for a in cards)],
    )

    still, ui_actions, missing = _still_needed(snapshot, categories)
    figures[STILL_NEEDED] = still

    lines = (READY_TO_ASSIGN, OVERSPENDING, UNCATEGORIZED_FIGURE, CARD_SHORTFALL, STILL_NEEDED)
    line_inputs = {name: figures[name].value for name in lines}
    figures[FREE] = Figure(
        value=rta - sum(line_inputs[n] for n in lines[1:]),
        formula="ready_to_assign - overspending - uncategorized - card_shortfall - still_needed",
        inputs=line_inputs,
        estimate=still.estimate,
        depends_on=sorted({d for n in lines for d in figures[n].depends_on}),
    )

    excluded = tracking_accounts(snapshot.accounts)

    rules = [
        "Free = Ready to Assign - overspending - uncategorized outflows - card payment shortfalls"
        " - still needed for the safety net and goals; negative means short",
        "Overspending = negative balances of categories outside Credit Card Payments;"
        " the Uncategorized balance is its own line",
        "Card shortfall = what each card owes minus its payment category's available, when positive;"
        " a card in credit owes zero",
        "Still needed = goal_overall_left for TB/TBD goals, else target minus balance (estimate)",
        "Only on-budget accounts feed Ready to Assign; tracking accounts are excluded",
    ]

    next_actions = []
    if figures[OVERSPENDING].value or figures[CARD_SHORTFALL].value:
        next_actions.append("Preview covering overspending and card shortfalls: ynab budget assign")
    if figures[UNCATEGORIZED_FIGURE].value:
        next_actions.append("Categorize the uncategorized outflows: ynab tx list --uncategorized")
    if missing:
        next_actions.append(
            "No category for " + ", ".join(missing) + ": size and create them with ynab money goals"
        )
    if ui_actions:
        next_actions.append(
            "Make each ui_actions change in the YNAB web app through Claude in Chrome,"
            " after the owner confirms the extension is on and approves each change"
        )

    return build_answer(
        "money available",
        snapshot.month,
        figures,
        warnings,
        rules_applied=rules,
        excluded=excluded,
        ui_actions=ui_actions,
        next_actions=next_actions,
    )


def tracking_accounts(accounts: list) -> list[dict]:
    """`excluded` entries for open tracking accounts, with their balances as context only (R6)."""
    return [
        {
            "item": a.name,
            "reason": (
                f"Tracking account ({getattr(a.type, 'value', a.type)}):"
                " never counts as available money"
            ),
            "balance": a.balance or 0,
        }
        for a in accounts if not a.closed and not a.on_budget
    ]


def _still_needed(snapshot: Snapshot, categories: list) -> tuple[Figure, list[dict], list[str]]:
    """The still-needed figure, the ui_actions for unfinished goals, and goal categories not found.

    TB/TBD goals contribute `goal_overall_left`; any other goal contributes
    `max(0, goal_target - balance)` and makes the figure an estimate. A goal
    category without a target contributes nothing.
    """
    by_name = {(c.name or "").casefold(): c for c in categories}
    left: dict[str, int] = {}
    target: dict[str, int] = {}
    balance: dict[str, int] = {}
    ui_actions: list[dict] = []
    missing: list[str] = []
    for key in goal_keys(snapshot.profile):
        name = goal_category(key, snapshot.profile)
        category = by_name.get(name.casefold())
        if category is None:
            missing.append(name)
            continue
        if category.goal_type in FINISHED_GOAL_TYPES:
            left[category.name] = category.goal_overall_left or 0
            continue
        if category.goal_target is None:
            continue
        target[category.name] = category.goal_target
        balance[category.name] = category.balance or 0
        ui_actions.append({
            "category": category.name,
            "current_goal_type": category.goal_type,
            "set": {
                "goal_type": "TBD",
                "goal_target": category.goal_target,
                "goal_target_date": str(category.goal_target_date) if category.goal_target_date else None,
            },
            "change": (
                f"Set the target type of '{category.name}' to Target Category Balance by Date (TBD):"
                f" {format_amount(category.goal_target)}"
                + (f" by {category.goal_target_date}" if category.goal_target_date else "")
            ),
            "where": "YNAB web app, through the Claude in Chrome extension",
            "why": "Until then its remaining amount is estimated as target minus balance",
        })
    figure = Figure(
        value=sum(left.values()) + sum(max(0, target[c] - balance[c]) for c in target),
        formula="sum(goal_overall_left.values()) + sum(max(0, target[c] - balance[c]) for c in target)",
        inputs={"goal_overall_left": left, "target": target, "balance": balance},
        estimate=bool(target),
    )
    return figure, ui_actions, missing


def _label(category) -> str:
    """`Group: Name`, unique enough to key a category in a figure's inputs."""
    return f"{category.category_group_name}: {category.name}"
