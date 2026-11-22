"""Configuration loading from file and environment variables."""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path.home() / ".config" / "ynab-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / ".env"
PROFILE_FILE = CONFIG_DIR / "profile.json"
RULES_FILE = CONFIG_DIR / "rules.json"
SNAPSHOT_DIR = CONFIG_DIR / "snapshots"


@dataclass
class Config:
    api_key: str = ""
    default_budget_id: str = ""
    default_budget_name: str = ""


class ProfileError(ValueError):
    """A profile value has the wrong type or format; the message names its key."""


@dataclass
class Goal:
    """A savings goal: the category that holds it, and optionally what and when.

    `amount` is in milliunits (written in dollars in profile.json). Without an
    amount and date, commands estimate the target from the budget's records.
    """

    category: str
    amount: int | None = None
    by: date | None = None


@dataclass
class Statement:
    """A balance taken from a bank statement, in milliunits, and its date."""

    balance: int
    on: date


@dataclass
class Profile:
    """Facts about one person's budget that change what the tool should do.

    Kept outside the repository: these describe a specific budget, so they are
    neither shareable nor meaningful to anyone else. Every field is optional —
    commands fall back to their built-in behaviour when the profile is absent.
    """

    # Last four digits of a card -> the YNAB account name it belongs to. Bank
    # autopay descriptions often identify the card only by these digits, and
    # sometimes not at all.
    card_last4: dict[str, str] = field(default_factory=dict)
    # Category group names, highest priority first. Used to decide what gets
    # funded when Ready to Assign cannot cover everything.
    funding_priority: list[str] = field(default_factory=list)
    # Category or group names whose spending counts toward the safety net.
    # Empty means the tool proposes a set and labels it an estimate.
    essential_categories: list[str] = field(default_factory=list)
    # Category or group names holding college costs: part of the proposed
    # essential set, and the basis of the school goal's estimate.
    college_categories: list[str] = field(default_factory=list)
    # Category groups whose money is held in savings rather than checking.
    savings_held_groups: list[str] = field(default_factory=list)
    # Goal key (e.g. "safety_net", "car", "school") -> its category and target.
    goals: dict[str, Goal] = field(default_factory=dict)
    # Category group where goal categories are created.
    goal_group: str = ""
    safety_net_months: float = 6
    checking_cushion_months: float = 0.5
    # A statement older than this, with no reconcile since, leaves the account
    # unchecked against the bank.
    statement_max_age_days: int = 45
    # Account name -> its most recent statement balance.
    statements: dict[str, Statement] = field(default_factory=dict)
    # Superseded by the measured safety-net target; kept only to report as unused.
    emergency_fund_target: float | None = None


def _load_env() -> None:
    """Load .env from ~/.config/ynab-cli/.env (does NOT override existing shell env vars)."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)


def load_config() -> Config:
    """Load config from file, then overlay environment variables.

    Priority (highest wins):
    1. Shell environment variables
    2. ~/.config/ynab-cli/.env file
    3. ~/.config/ynab-cli/config.json defaults
    """
    _load_env()

    file_config = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            file_config = json.load(f)

    return Config(
        api_key=os.environ.get("YNAB_API_KEY", ""),
        default_budget_id=file_config.get("default_budget_id", ""),
        default_budget_name=file_config.get("default_budget_name", ""),
    )


def ensure_config_dir() -> None:
    """Create config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def save_config(**kwargs) -> None:
    """Update config file with given key-value pairs."""
    ensure_config_dir()

    existing = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            existing = json.load(f)

    existing.update(kwargs)

    with open(CONFIG_FILE, "w") as f:
        json.dump(existing, f, indent=2)



def load_profile() -> Profile:
    """Load the budget profile, or an empty one if it does not exist."""
    if not PROFILE_FILE.exists():
        return Profile()
    with open(PROFILE_FILE) as f:
        data = json.load(f)
    defaults = Profile()
    return Profile(
        card_last4=data.get("card_last4", {}),
        funding_priority=data.get("funding_priority", []),
        essential_categories=data.get("essential_categories", []),
        college_categories=data.get("college_categories", []),
        savings_held_groups=data.get("savings_held_groups", []),
        goals={
            name: Goal(
                category=g.get("category", ""),
                amount=_milliunits(g, "amount", f"goals.{name}.amount", required=False),
                by=_iso_date(g, "by", f"goals.{name}.by", required=False),
            )
            for name, g in data.get("goals", {}).items()
        },
        goal_group=data.get("goal_group", ""),
        safety_net_months=_number(data, "safety_net_months", defaults.safety_net_months),
        checking_cushion_months=_number(data, "checking_cushion_months", defaults.checking_cushion_months),
        statement_max_age_days=_number(data, "statement_max_age_days", defaults.statement_max_age_days),
        statements={
            account: Statement(
                balance=_milliunits(st, "balance", f"statements.{account}.balance"),
                on=_iso_date(st, "on", f"statements.{account}.on"),
            )
            for account, st in data.get("statements", {}).items()
        },
        emergency_fund_target=data.get("emergency_fund_target"),
    )


def _number(data: dict, key: str, default: float) -> float:
    """Return `data[key]` if it is a number, `default` if absent; raise ProfileError otherwise."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"profile.json: {key} must be a number, got {value!r}")
    return value


def _milliunits(data: dict, key: str, path: str, required: bool = True) -> int | None:
    """Read a dollar amount at `data[key]` as milliunits; `path` names it in errors."""
    if key not in data and not required:
        return None
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError(f"profile.json: {path} must be a dollar amount, got {value!r}")
    return round(value * 1000)


def _iso_date(data: dict, key: str, path: str, required: bool = True) -> date | None:
    """Read a YYYY-MM-DD date at `data[key]`; `path` names it in errors."""
    if key not in data and not required:
        return None
    value = data.get(key)
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ProfileError(f"profile.json: {path} must be a YYYY-MM-DD date, got {value!r}") from None


def save_profile(**kwargs) -> None:
    """Update the profile file with given key-value pairs."""
    ensure_config_dir()

    existing = {}
    if PROFILE_FILE.exists():
        with open(PROFILE_FILE) as f:
            existing = json.load(f)

    existing.update(kwargs)

    with open(PROFILE_FILE, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


def save_statement(account: str, balance: int, on: date) -> None:
    """Record `account`'s statement balance (milliunits) as of `on`.

    Replaces any earlier statement for that account; other accounts' statements
    and every other profile key are kept.
    """
    statements = {}
    if PROFILE_FILE.exists():
        with open(PROFILE_FILE) as f:
            statements = json.load(f).get("statements", {})
    statements[account] = {"balance": balance / 1000, "on": on.isoformat()}
    save_profile(statements=statements)


@dataclass
class Rule:
    """One payee pattern and the category it assigns.

    `match` and `exclude` are regular expressions tested against the payee name,
    case-insensitively. Rules are evaluated in order and the first match wins,
    so a specific rule placed above a broad one acts as an override.
    """

    match: str
    category: str
    exclude: str = ""


def validate_rule_pattern(pattern: str) -> str:
    """Return a complaint about `pattern`, or an empty string if it is sound.

    Catches the two ways a pattern silently stops matching: a control character
    left behind when an escape like \\b was written outside a raw string, and a
    regex that does not compile.
    """
    if any(ord(c) < 32 for c in pattern):
        bad = next(repr(c) for c in pattern if ord(c) < 32)
        return (
            f"contains the control character {bad} — a regex escape was probably"
            " written outside a raw string, so this will never match"
        )
    try:
        re.compile(pattern)
    except re.error as e:
        return f"is not a valid regular expression: {e}"
    return ""


def load_rules() -> list[Rule]:
    """Load categorization rules, or an empty list if none are defined.

    Unsound patterns are skipped with a warning rather than raising: one bad
    rule should not stop the rest from being applied.
    """
    if not RULES_FILE.exists():
        return []
    with open(RULES_FILE) as f:
        data = json.load(f)

    rules = []
    for r in data.get("rules", []):
        skip = False
        for fieldname in ("match", "exclude"):
            pattern = r.get(fieldname, "")
            if not pattern:
                continue
            complaint = validate_rule_pattern(pattern)
            if complaint:
                print(f"warning: rule {fieldname} {pattern!r} {complaint}; skipping", file=sys.stderr)
                skip = True
        if not skip:
            rules.append(Rule(
                match=r["match"],
                category=r["category"],
                exclude=r.get("exclude", ""),
            ))
    return rules


def save_rules(rules: list[dict]) -> None:
    """Write categorization rules, replacing any existing set."""
    ensure_config_dir()
    with open(RULES_FILE, "w") as f:
        json.dump({"rules": rules}, f, indent=2, ensure_ascii=False)


def save_env_file(credentials: dict[str, str]) -> None:
    """Write credentials to ~/.config/ynab-cli/.env."""
    ensure_config_dir()
    lines = []
    # Preserve existing entries not being overwritten
    if ENV_FILE.exists():
        existing = ENV_FILE.read_text().splitlines()
        for line in existing:
            key = line.split("=", 1)[0] if "=" in line else ""
            if key not in credentials:
                lines.append(line)
    for key, value in credentials.items():
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
