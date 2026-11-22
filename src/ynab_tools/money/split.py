"""How much to keep in checking, move to savings, or invest (R7, R8, KTD6).

`split` is pure over a snapshot and three full months of month detail. Every
figure's `formula` is a Python expression over its own `inputs`, so it can be
recomputed from the answer alone. The answer only gives amounts: the owner
makes every transfer.
"""

from datetime import date, timedelta

from ..api import client
from ..commands.report import _EXCLUDED_GROUPS
from ..utils.formatting import format_amount
from .answer import Figure, build_answer
from .available import tracking_accounts
from .goals import average_monthly_spending, goal_category, goal_keys, months_out, total_spending
from .quality import CARD_OWED, CHECKING_RECOMMENDATION, account_balance, check_quality
from .snapshot import HISTORY_DAYS, Snapshot, load_snapshot

# Full months that bills and spending are averaged over.
HISTORY_MONTHS = 3
# Transfers to these account types move money between the owner's own cash,
# pay a card (counted once, in its balance) or invest (which the split itself
# decides); they are not bills.
NOT_BILL_TRANSFER_TYPES = ("checking", "savings", "cash", "creditCard", "otherAsset")


def split_answer(budget_id: str | None = None, today: date | None = None) -> dict:
    """Read the budget and three full months, and return the KTD1 split answer.

    The snapshot reaches back at least to the first of the three full months,
    so their checking outflows are all read. Makes 3 + 3 requests and no
    write. Raises what `load_snapshot` raises.
    """
    today = today or date.today()
    start = months_out(today.replace(day=1), -HISTORY_MONTHS)
    snapshot = load_snapshot(
        budget_id=budget_id, today=today, history_days=max(HISTORY_DAYS, (today - start).days),
    )
    history = client.get_month_history(snapshot.month.isoformat(), HISTORY_MONTHS, budget_id=budget_id)
    return split(snapshot, history)


def split(snapshot: Snapshot, history: list) -> dict:
    """The checking-savings split for `snapshot`, with its data-quality warnings.

    `history` holds the full months before `snapshot.month` (month detail
    objects). Figures, in milliunits:
    - `checking_recommendation` = `bills` (3-month average of non-card,
      non-own-cash checking outflows) + `card_payments` (what the cards owe
      now) + `spending_cushion` (`checking_cushion_months` of average
      category spending).
    - `checking_difference`: checking balance minus the recommendation;
      negative when checking is short.
    - `savings_must_hold`: balances of the goal categories and the
      `savings_held_groups` categories.
    - Checking's surplus goes to savings up to its shortfall
      (`move_to_savings`); the rest is `available_to_invest`. When checking
      is short, only savings' own surplus is suggested (`from_savings`).
    Tracking accounts are listed under `excluded` (R6). Makes no API requests.
    """
    warnings = check_quality(snapshot)
    profile = snapshot.profile
    open_on_budget = [a for a in snapshot.accounts if not a.closed and a.on_budget]
    checking = [a for a in open_on_budget if a.type == "checking"]
    savings = [a for a in open_on_budget if a.type == "savings"]
    cards = [a for a in open_on_budget if a.type == "creditCard"]
    card_deps = [CARD_OWED, *(account_balance(a.id) for a in cards)]
    checking_deps = [account_balance(a.id) for a in checking]
    savings_deps = [account_balance(a.id) for a in savings]

    start = months_out(snapshot.month, -HISTORY_MONTHS)
    outflows = _bill_outflows(snapshot, checking, start)
    figures: dict[str, Figure] = {}
    figures["bills"] = Figure(
        value=round(outflows / HISTORY_MONTHS),
        formula="round(outflows / months)",
        inputs={
            "outflows": outflows,
            "months": HISTORY_MONTHS,
            "window": f"{start}..{snapshot.month - timedelta(days=1)}",
            "accounts": [a.name for a in checking],
        },
    )

    card_balances = {a.name: a.balance or 0 for a in cards}
    figures["card_payments"] = Figure(
        # A card balance is negative when money is owed; a card in credit owes zero.
        value=sum(max(0, -b) for b in card_balances.values()),
        formula="sum(max(0, -b) for b in card_balances.values())",
        inputs={"card_balances": card_balances},
        depends_on=card_deps,
    )

    spending_ids = {
        str(c.id) for m in history for c in m.categories
        if not c.deleted and (c.category_group_name or "").lower() not in _EXCLUDED_GROUPS
    }
    category_outflows = total_spending(history, list(spending_ids))
    cushion_months = profile.checking_cushion_months
    figures["spending_cushion"] = Figure(
        value=round(category_outflows / HISTORY_MONTHS * cushion_months),
        formula="round(category_outflows / months * cushion_months)",
        inputs={
            "category_outflows": category_outflows,
            "months": HISTORY_MONTHS,
            "cushion_months": cushion_months,
            "monthly_average": average_monthly_spending(history, list(spending_ids)),
        },
    )

    parts = ("bills", "card_payments", "spending_cushion")
    figures[CHECKING_RECOMMENDATION] = Figure(
        value=sum(figures[p].value for p in parts),
        formula="bills + card_payments + spending_cushion",
        inputs={p: figures[p].value for p in parts},
        depends_on=card_deps,
    )

    checking_balances = {a.name: a.balance or 0 for a in checking}
    figures["checking_balance"] = Figure(
        value=sum(checking_balances.values()),
        formula="sum(balances.values())",
        inputs={"balances": checking_balances},
        depends_on=checking_deps,
    )
    difference_deps = [CHECKING_RECOMMENDATION, *card_deps, *checking_deps]
    figures["checking_difference"] = Figure(
        value=figures["checking_balance"].value - figures[CHECKING_RECOMMENDATION].value,
        formula="checking_balance - checking_recommendation",
        inputs={
            "checking_balance": figures["checking_balance"].value,
            "checking_recommendation": figures[CHECKING_RECOMMENDATION].value,
        },
        depends_on=difference_deps,
    )

    savings_balances = {a.name: a.balance or 0 for a in savings}
    figures["savings_balance"] = Figure(
        value=sum(savings_balances.values()),
        formula="sum(balances.values())",
        inputs={"balances": savings_balances},
        depends_on=savings_deps,
    )
    held = _savings_held(snapshot)
    figures["savings_must_hold"] = Figure(
        value=sum(held.values()),
        formula="sum(held.values())",
        inputs={"held": held},
    )
    balance_vs_hold = {
        "savings_balance": figures["savings_balance"].value,
        "savings_must_hold": figures["savings_must_hold"].value,
    }
    figures["savings_shortfall"] = Figure(
        value=max(0, balance_vs_hold["savings_must_hold"] - balance_vs_hold["savings_balance"]),
        formula="max(0, savings_must_hold - savings_balance)",
        inputs=balance_vs_hold,
        depends_on=savings_deps,
    )
    figures["savings_surplus"] = Figure(
        value=max(0, balance_vs_hold["savings_balance"] - balance_vs_hold["savings_must_hold"]),
        formula="max(0, savings_balance - savings_must_hold)",
        inputs=balance_vs_hold,
        depends_on=savings_deps,
    )

    difference = figures["checking_difference"].value
    all_deps = [*difference_deps, *savings_deps]
    move = min(max(0, difference), figures["savings_shortfall"].value)
    figures["move_to_savings"] = Figure(
        value=move,
        formula="min(max(0, checking_difference), savings_shortfall)",
        inputs={"checking_difference": difference, "savings_shortfall": figures["savings_shortfall"].value},
        depends_on=all_deps,
    )
    figures["available_to_invest"] = Figure(
        value=max(0, difference) - move,
        formula="max(0, checking_difference) - move_to_savings",
        inputs={"checking_difference": difference, "move_to_savings": move},
        depends_on=all_deps,
    )
    from_savings = min(max(0, -difference), figures["savings_surplus"].value)
    figures["from_savings"] = Figure(
        value=from_savings,
        formula="min(max(0, -checking_difference), savings_surplus)",
        inputs={"checking_difference": difference, "savings_surplus": figures["savings_surplus"].value},
        depends_on=all_deps,
    )

    rules = [
        "Checking recommendation = next month's bills + what the cards owe now"
        f" + {cushion_months:g} months of spending",
        f"Bills = average over the last {HISTORY_MONTHS} full months of checking outflows, excluding"
        " card payments and transfers to checking, savings or cash; loan and mortgage transfers count",
        f"Spending = average over the last {HISTORY_MONTHS} full months of category activity"
        " outside Credit Card Payments and internal categories (split transactions included)",
        "Savings must hold the balances assigned to the safety net, the goals and the"
        " savings_held_groups categories",
        "Checking's surplus goes to savings up to that amount; the rest is available to invest."
        " When checking is short, only savings' own surplus is suggested",
        "Investment and retirement accounts never count; the owner makes every transfer",
    ]

    next_actions = []
    if move:
        next_actions.append(f"Move {format_amount(move)} from checking to savings (the owner makes the transfer)")
    if figures["available_to_invest"].value:
        next_actions.append(
            f"{format_amount(figures['available_to_invest'].value)} is available to invest; the owner decides"
        )
    if from_savings:
        next_actions.append(
            f"Move {format_amount(from_savings)} from savings to checking (the owner makes the transfer)"
        )
    if difference < 0 and -difference > from_savings:
        next_actions.append(
            f"Checking stays {format_amount(-difference - from_savings)} below the recommendation"
            " with no savings surplus to cover it"
        )

    return build_answer(
        "money split",
        snapshot.month,
        figures,
        warnings,
        rules_applied=rules,
        excluded=tracking_accounts(snapshot.accounts),
        next_actions=next_actions,
    )


def _bill_outflows(snapshot: Snapshot, checking: list, start: date) -> int:
    """Total checking outflow dated in [`start`, `snapshot.month`), as a positive amount.

    Leaves out transfers to accounts of `NOT_BILL_TRANSFER_TYPES`, including
    investment accounts; transfers to any other account (loans, mortgages) count.
    """
    checking_ids = {str(a.id) for a in checking}
    types = {str(a.id): getattr(a.type, "value", a.type) for a in snapshot.accounts}
    total = 0
    for t in snapshot.transactions:
        if str(t.account_id) not in checking_ids or t.amount >= 0:
            continue
        if not start <= _as_date(t.var_date) < snapshot.month:
            continue
        if t.transfer_account_id and types.get(str(t.transfer_account_id)) in NOT_BILL_TRANSFER_TYPES:
            continue
        total -= t.amount
    return total


def _savings_held(snapshot: Snapshot) -> dict[str, int]:
    """`Group: Name` -> balance for each goal category and `savings_held_groups` category."""
    goal_names = {goal_category(k, snapshot.profile).casefold() for k in goal_keys(snapshot.profile)}
    groups = {g.casefold() for g in snapshot.profile.savings_held_groups}
    return {
        f"{c.category_group_name}: {c.name}": c.balance or 0
        for c in snapshot.month_detail.categories
        if not c.deleted and (
            (c.name or "").casefold() in goal_names
            or (c.category_group_name or "").casefold() in groups
        )
    }


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
