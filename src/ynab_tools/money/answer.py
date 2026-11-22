"""The one JSON answer shape every money command emits (KTD1)."""

from dataclasses import dataclass, field
from datetime import date

from .quality import DataWarning


@dataclass
class Figure:
    """One computed amount in an answer, in milliunits.

    `formula` says how it was computed and `inputs` holds the named amounts
    it was computed from. `estimate` marks a figure resting on a guess.
    `depends_on` names further figures (see `quality`, e.g.
    `account_balance(id)`) whose warnings also move this one.
    """

    value: int
    formula: str
    inputs: dict
    estimate: bool = False
    depends_on: list[str] = field(default_factory=list)


def uncertainty(name: str, figure: Figure, warnings: list[DataWarning]) -> int | None:
    """Sum of the bounds of warnings affecting `name` or any of `figure.depends_on`.

    Each warning counts once. None if any of those warnings has an unknown bound.
    """
    names = {name, *figure.depends_on}
    total = 0
    for w in warnings:
        if not names & set(w.affects):
            continue
        if w.bound is None:
            return None
        total += w.bound
    return total


def build_answer(
    command: str,
    month: date,
    figures: dict[str, Figure],
    warnings: list[DataWarning],
    rules_applied: list[str] | None = None,
    excluded: list[dict] | None = None,
    ui_actions: list[dict] | None = None,
    next_actions: list[str] | None = None,
) -> dict:
    """Assemble the KTD1 envelope as a JSON-ready dict.

    `status` is "exact" with no warnings and "approximate" otherwise (R11);
    the answer is always given. Each figure gains its `uncertainty`; its
    `depends_on` is not emitted. Amounts stay in integer milliunits.
    """
    return {
        "command": command,
        "month": month.isoformat(),
        "units": "milliunits",
        "status": "approximate" if warnings else "exact",
        "figures": {
            name: {
                "value": f.value,
                "formula": f.formula,
                "inputs": f.inputs,
                "estimate": f.estimate,
                "uncertainty": uncertainty(name, f, warnings),
            }
            for name, f in figures.items()
        },
        "rules_applied": list(rules_applied or []),
        "excluded": list(excluded or []),
        "warnings": [w.to_dict() for w in warnings],
        "ui_actions": list(ui_actions or []),
        "next_actions": list(next_actions or []),
    }
