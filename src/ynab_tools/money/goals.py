"""Sizing and creating the safety-net and goal categories (R1-R4, KTD7, KTD11, KTD12).

The sizing functions are pure: they take SDK-shaped objects and return
milliunits. `goals_answer` reads the budget, sizes every goal and, when told
to apply, creates or updates the goal categories.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from ..api import client
from ..utils.formatting import format_amount
from .answer import Figure, build_answer
from .quality import DataWarning, check_quality
from .snapshot import Snapshot, load_snapshot

# Full months of history the essential average and the school estimate use.
HISTORY_MONTHS = 12
# How far out a goal's date is set when the owner has not given one.
DEFAULT_GOAL_MONTHS = 12
DEFAULT_GOAL_GROUP = "Goals"
# The goals R1 requires, and the category name each gets when the profile names none.
STANDARD_GOALS = {"safety_net": "Safety Net", "car": "Next Car", "school": "School"}
# Goal types whose remaining amount YNAB reports itself; any other type is
# finished in the web app (KTD7).
FINISHED_GOAL_TYPES = ("TB", "TBD")
# Group-name keywords for the proposed essential set: needs, not wants (R3).
ESSENTIAL_KEYWORDS = (
    "housing", "mortgage", "rent", "utilit", "insurance", "grocer",
    "loan", "health", "medical", "transport",
)

SAFETY_NET = "safety_net"
ESSENTIAL_MONTHLY = "essential_monthly"


def goal_keys(profile) -> list[str]:
    """The standard goal keys, then any other goal the profile names."""
    return [*STANDARD_GOALS, *(k for k in profile.goals if k not in STANDARD_GOALS)]


def goal_category(key: str, profile) -> str:
    """The category name holding goal `key`: the profile's, else the standard default."""
    cfg = profile.goals.get(key)
    return (cfg.category if cfg else "") or STANDARD_GOALS.get(key, key)


def target_figure(key: str) -> str:
    """The figure name for one goal's target, e.g. `car_target`."""
    return f"{key}_target"


@dataclass
class EssentialSet:
    """The categories whose spending sizes the safety net.

    `estimate` is True when the set was proposed from keywords rather than
    taken from the profile. `unmatched` holds profile names matching nothing.
    """

    categories: list
    estimate: bool
    unmatched: list[str] = field(default_factory=list)


@dataclass
class GoalTarget:
    """One goal's sized target. `amount` is None when nothing could size it.

    `estimate` is True when the tool filled the amount or the date (R4);
    `basis` holds one rule line per filled value. `formula` says where the
    amount came from.
    """

    key: str
    category: str
    amount: int | None
    by: date
    estimate: bool
    basis: list[str] = field(default_factory=list)
    formula: str = ""


def match_names(names: list[str], categories: list) -> tuple[list, list[str]]:
    """Categories whose name or group name equals one of `names`, case-insensitively.

    Returns (matched categories in `categories` order, names that matched
    nothing). Deleted categories never match.
    """
    wanted = {n.casefold(): n for n in names}
    hit: set[str] = set()
    matched = []
    for c in categories:
        if c.deleted:
            continue
        keys = {c.name.casefold(), (c.category_group_name or "").casefold()} & wanted.keys()
        if keys:
            hit |= keys
            matched.append(c)
    return matched, [n for k, n in wanted.items() if k not in hit]


def propose_essentials(categories: list, college_names: list[str]) -> list:
    """The essential set proposed when the profile has none (KTD11).

    Every visible category in a group whose name starts a word with one of
    `ESSENTIAL_KEYWORDS`, plus the categories `college_names` match (R3).
    """
    pattern = re.compile(r"\b(" + "|".join(ESSENTIAL_KEYWORDS) + ")", re.IGNORECASE)
    college, _ = match_names(college_names, categories)
    college_ids = {c.id for c in college}
    return [
        c for c in categories
        if not c.deleted and (
            c.id in college_ids
            or (not c.hidden and pattern.search(c.category_group_name or ""))
        )
    ]


def resolve_essentials(profile, categories: list) -> EssentialSet:
    """Resolve the profile's essential names against the current month's categories.

    With no `essential_categories` in the profile, proposes a set and marks it
    an estimate.
    """
    if not profile.essential_categories:
        return EssentialSet(propose_essentials(categories, profile.college_categories), estimate=True)
    matched, unmatched = match_names(profile.essential_categories, categories)
    return EssentialSet(matched, estimate=False, unmatched=unmatched)


def total_spending(months: list, category_ids: list) -> int:
    """Outflow across `months` in the given categories, in milliunits (positive = spent).

    Sums the month endpoint's category `activity`, so refunds reduce it and
    split transactions count.
    """
    ids = {str(i) for i in category_ids}
    return -sum(c.activity for m in months for c in m.categories if str(c.id) in ids)


def average_monthly_spending(months: list, category_ids: list) -> int:
    """`total_spending` divided by the number of months, rounded; 0 for no months."""
    if not months:
        return 0
    return round(total_spending(months, category_ids) / len(months))


def safety_net_target(monthly: int, months_of_cover: float) -> int:
    """The safety-net target: `months_of_cover` times monthly essential spending (R3)."""
    return round(monthly * months_of_cover)


def months_out(day: date, months: int) -> date:
    """`day` moved `months` later, clamped to the end of a shorter month."""
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")


def starting_balance(transactions: list) -> tuple[date, int] | None:
    """The date and amount of an account's earliest "Starting Balance" transaction, or None."""
    starts = [
        t for t in transactions
        if not t.deleted and (t.payee_name or "") == "Starting Balance"
    ]
    if not starts:
        return None
    first = min(starts, key=lambda t: _as_date(t.var_date))
    return _as_date(first.var_date), first.amount


def car_estimate(loan_openings: list[tuple[str, date, int]]) -> tuple[int, str] | None:
    """The car estimate from (account name, opened on, opening amount) per auto loan (KTD12).

    Uses the most recently opened loan's opening balance, as a positive amount.
    Returns (amount, basis line), or None with no auto loans.
    """
    if not loan_openings:
        return None
    name, opened, amount = max(loan_openings, key=lambda o: o[1])
    return abs(amount), (
        f"Car target estimated as the opening balance of auto-loan account '{name}'"
        f" ({format_amount(abs(amount))}, opened {opened})"
    )


def size_goals(
    snapshot: Snapshot,
    essential_monthly: int | None,
    essential_estimate: bool,
    school_total: int | None,
    car: tuple[int, str] | None,
    existing_dates: dict[str, date] | None = None,
) -> list[GoalTarget]:
    """Size every goal: the three standard ones and any others the profile names.

    A configured amount and date win. Otherwise the safety net is measured
    (R3), the car and school are estimated (KTD12), and a missing date is the
    one already on the goal's category (`existing_dates`, by goal key) or else
    12 months out, so a later run never moves it; each filled value is
    flagged as an estimate. `essential_monthly` and `school_total` are None
    when no category feeds them.
    """
    profile = snapshot.profile
    default_by = months_out(snapshot.today, DEFAULT_GOAL_MONTHS)
    targets = []
    for key in goal_keys(profile):
        cfg = profile.goals.get(key)
        category = goal_category(key, profile)
        basis: list[str] = []
        estimate = False
        formula = ""
        if key == SAFETY_NET:
            amount = None
            formula = "safety_net_months x essential_monthly"
            if essential_monthly is not None:
                amount = safety_net_target(essential_monthly, profile.safety_net_months)
                estimate = essential_estimate
        elif cfg and cfg.amount is not None:
            amount = cfg.amount
            formula = "configured amount"
        elif key == "car" and car:
            amount, line = car
            estimate = True
            formula = "opening balance of the most recent auto loan"
            basis.append(line)
        elif key == "school" and school_total:
            amount = school_total
            estimate = True
            formula = f"college category outflow over the last {HISTORY_MONTHS} full months"
            basis.append(
                f"School target estimated as {HISTORY_MONTHS} months of spending in the"
                f" college categories ({format_amount(school_total)})"
            )
        else:
            amount = None
        kept = (existing_dates or {}).get(key)
        by = (cfg.by if cfg and cfg.by else None) or kept or default_by
        if not (cfg and cfg.by) and amount is not None:
            estimate = True
            basis.append(
                f"{category} date kept from its category ({by})" if kept
                else f"{category} date defaults to {DEFAULT_GOAL_MONTHS} months out ({by})"
            )
        targets.append(GoalTarget(key, category, amount, by, estimate, basis, formula))
    return targets


def goals_answer(budget_id: str | None = None, apply: bool = False, today: date | None = None) -> dict:
    """Size the goals and, with `apply`, create or update their categories.

    Reads the snapshot (3 requests), 12 history months, the category groups,
    and one transaction list per auto-loan account when the car needs an
    estimate. Without `apply` it makes no write. With `apply` it creates the
    goal group and missing goal categories, sets each target and date that
    differs, then re-reads the categories to report each goal's type. Goals
    without a target are never written. Returns the KTD1 answer.
    """
    snapshot = load_snapshot(budget_id=budget_id, today=today)
    warnings = check_quality(snapshot)
    profile = snapshot.profile
    current = list(snapshot.month_detail.categories)
    history = client.get_month_history(snapshot.month.isoformat(), HISTORY_MONTHS, budget_id=budget_id)

    essentials = resolve_essentials(profile, current)
    for name in essentials.unmatched:
        warnings.append(DataWarning(
            code="unmatched_essential",
            message=f"Essential name '{name}' matches no category or group and is left out",
            bound=None,
            affects=[ESSENTIAL_MONTHLY, target_figure(SAFETY_NET)],
            fix="Correct the name in essential_categories in profile.json",
        ))
    essential_ids = [c.id for c in essentials.categories]
    essential_monthly = average_monthly_spending(history, essential_ids) if essential_ids else None

    college, college_unmatched = match_names(profile.college_categories, current)
    for name in college_unmatched:
        warnings.append(DataWarning(
            code="unmatched_college",
            message=f"College name '{name}' matches no category or group and is left out",
            bound=None,
            affects=[target_figure("school")],
            fix="Correct the name in college_categories in profile.json",
        ))
    school_total = total_spending(history, [c.id for c in college]) if college else None

    car = None
    car_cfg = profile.goals.get("car")
    if not (car_cfg and car_cfg.amount is not None):
        car = car_estimate(_loan_openings(snapshot.accounts, budget_id))

    groups = client.list_categories(budget_id=budget_id)
    existing_dates = {}
    for key in goal_keys(profile):
        found = _find_category(groups, goal_category(key, profile))
        if found and found.goal_target_date:
            existing_dates[key] = _as_date(found.goal_target_date)
    targets = size_goals(snapshot, essential_monthly, essentials.estimate, school_total, car, existing_dates)

    group_name = profile.goal_group or DEFAULT_GOAL_GROUP
    actions = {t.key: _action(t, _find_category(groups, t.category)) for t in targets}
    if apply:
        _write(targets, actions, groups, group_name, budget_id)
        groups = client.list_categories(budget_id=budget_id)
        actions = {k: {"create": "created", "update": "updated"}.get(a, a) for k, a in actions.items()}

    figures: dict[str, Figure] = {}
    rules = []
    if essential_monthly is not None:
        figures[ESSENTIAL_MONTHLY] = Figure(
            value=essential_monthly,
            formula=f"essential category outflow over the last {HISTORY_MONTHS} full months / {HISTORY_MONTHS}",
            inputs={
                "categories": [c.name for c in essentials.categories],
                "months": len(history),
                "total": total_spending(history, essential_ids),
            },
            estimate=essentials.estimate,
        )
        rules.append(
            f"Safety-net target = {profile.safety_net_months:g} months x average monthly essential"
            f" spending over the last {HISTORY_MONTHS} full months (category activity)"
        )
    if essentials.estimate:
        rules.append(
            "Essential set proposed from group-name keywords and the college categories: "
            + ", ".join(c.name for c in essentials.categories)
            + "; set essential_categories in profile.json to change it"
        )

    ui_actions = []
    next_actions = []
    for t in targets:
        category = _find_category(groups, t.category)
        goal_type = category.goal_type if category else None
        if t.amount is None:
            next_actions.append(
                f"No target for {t.key}: set goals.{t.key}.amount and goals.{t.key}.by in profile.json"
                + (" or essential_categories" if t.key == SAFETY_NET else "")
            )
            continue
        inputs = {
            "category": t.category,
            "by": t.by.isoformat(),
            "action": actions[t.key],
            "goal_type": goal_type,
        }
        if t.key == SAFETY_NET:
            inputs.update(essential_monthly=essential_monthly, months=profile.safety_net_months)
        figures[target_figure(t.key)] = Figure(
            value=t.amount,
            formula=t.formula,
            inputs=inputs,
            estimate=t.estimate,
            depends_on=[ESSENTIAL_MONTHLY] if t.key == SAFETY_NET else [],
        )
        rules.extend(t.basis)
        if category and goal_type not in FINISHED_GOAL_TYPES:
            ui_actions.append({
                "category": t.category,
                "current_goal_type": goal_type,
                "set": {"goal_type": "TBD", "goal_target": t.amount, "goal_target_date": t.by.isoformat()},
                "change": (
                    f"Set the target type of '{t.category}' to Target Category Balance by Date (TBD):"
                    f" {format_amount(t.amount)} by {t.by}"
                ),
                "where": "YNAB web app, through the Claude in Chrome extension",
                "why": "The API sets a target amount and date but cannot set the target type",
            })

    excluded = []
    if profile.emergency_fund_target is not None:
        excluded.append({
            "item": "emergency_fund_target",
            "reason": "Superseded by the measured safety-net target; unused",
        })
    sn_cfg = profile.goals.get(SAFETY_NET)
    if sn_cfg and sn_cfg.amount is not None:
        excluded.append({
            "item": "goals.safety_net.amount",
            "reason": "The safety-net target is measured from essential spending; unused",
        })

    pending = [k for k, a in actions.items() if a in ("create", "update")]
    if pending and not apply:
        next_actions.insert(0, "Review the targets, then write them with: ynab money goals --apply")
    if ui_actions:
        next_actions.append(
            "Make each ui_actions change in the YNAB web app through Claude in Chrome,"
            " after the owner confirms the extension is on and approves each change"
        )

    return build_answer(
        "money goals",
        snapshot.month,
        figures,
        warnings,
        rules_applied=rules,
        excluded=excluded,
        ui_actions=ui_actions,
        next_actions=next_actions,
    )


def _loan_openings(accounts: list, budget_id: str | None) -> list[tuple[str, date, int]]:
    """(name, opened on, opening amount) for each auto-loan account; one request each."""
    openings = []
    for a in accounts:
        if a.deleted or a.type != "autoLoan":
            continue
        start = starting_balance(client.list_transactions_by_account(str(a.id), budget_id=budget_id))
        if start:
            openings.append((a.name, *start))
    return openings


def _find_category(groups: list, name: str):
    """The non-deleted category named `name` (case-insensitive) in any group, or None."""
    for g in groups:
        if g.deleted:
            continue
        for c in g.categories:
            if not c.deleted and c.name.casefold() == name.casefold():
                return c
    return None


def _action(target: GoalTarget, category) -> str:
    """What applying would do: create, update, unchanged, or no_target."""
    if target.amount is None:
        return "no_target"
    if category is None:
        return "create"
    if category.goal_target == target.amount and category.goal_target_date == target.by:
        return "unchanged"
    return "update"


def _write(targets: list[GoalTarget], actions: dict[str, str], groups: list, group_name: str,
           budget_id: str | None) -> None:
    """Create the goal group if needed, then create or update each pending goal category."""
    group_id = None
    for t in targets:
        action = actions[t.key]
        fields = {"goal_target": t.amount, "goal_target_date": t.by}
        if action == "update":
            client.update_category(str(_find_category(groups, t.category).id), fields, budget_id=budget_id)
        elif action == "create":
            if group_id is None:
                group_id = _find_group_id(groups, group_name)
            if group_id is None:
                group_id = str(client.create_category_group(group_name, budget_id=budget_id).id)
            client.create_category(t.category, group_id, budget_id=budget_id, **fields)


def _find_group_id(groups: list, name: str) -> str | None:
    for g in groups:
        if not g.deleted and g.name.casefold() == name.casefold():
            return str(g.id)
    return None


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))
