"""Tests for configuration management."""

import json
from datetime import date

import pytest

from ynab_tools.config import (
    Config,
    Goal,
    ProfileError,
    Statement,
    load_config,
    load_profile,
    load_rules,
    save_config,
    save_profile,
    save_statement,
    validate_rule_pattern,
)


def test_config_defaults():
    config = Config()
    assert config.api_key == ""
    assert config.default_budget_id == ""
    assert config.default_budget_name == ""


def test_load_config_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("YNAB_API_KEY", "test-key-123")
    monkeypatch.setattr("ynab_tools.config.CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("ynab_tools.config.ENV_FILE", tmp_path / ".env")

    config = load_config()
    assert config.api_key == "test-key-123"


def test_load_config_from_file(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "default_budget_id": "abc-123",
                "default_budget_name": "My Budget",
            }
        )
    )

    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    monkeypatch.setattr("ynab_tools.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("ynab_tools.config.ENV_FILE", tmp_path / ".env")

    config = load_config()
    assert config.default_budget_id == "abc-123"
    assert config.default_budget_name == "My Budget"


def test_save_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr("ynab_tools.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("ynab_tools.config.CONFIG_DIR", tmp_path)

    save_config(default_budget_id="xyz-789", default_budget_name="Test")

    data = json.loads(config_file.read_text())
    assert data["default_budget_id"] == "xyz-789"
    assert data["default_budget_name"] == "Test"


def test_save_config_preserves_existing(monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"default_budget_id": "old-id", "extra": "keep"}))
    monkeypatch.setattr("ynab_tools.config.CONFIG_FILE", config_file)
    monkeypatch.setattr("ynab_tools.config.CONFIG_DIR", tmp_path)

    save_config(default_budget_name="New Name")

    data = json.loads(config_file.read_text())
    assert data["default_budget_id"] == "old-id"
    assert data["default_budget_name"] == "New Name"
    assert data["extra"] == "keep"


def test_load_profile_missing_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr("ynab_tools.config.PROFILE_FILE", tmp_path / "profile.json")

    profile = load_profile()

    assert profile.card_last4 == {}
    assert profile.funding_priority == []
    assert profile.essential_categories == []
    assert profile.college_categories == []
    assert profile.savings_held_groups == []
    assert profile.goals == {}
    assert profile.goal_group == ""
    assert profile.safety_net_months == 6
    assert profile.checking_cushion_months == 0.5
    assert profile.statement_max_age_days == 45
    assert profile.statements == {}
    assert profile.emergency_fund_target is None


def test_load_profile_from_file(monkeypatch, tmp_path):
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(
        json.dumps(
            {
                "card_last4": {"1234": "Some Card"},
                "funding_priority": ["Housing", "Food"],
            }
        )
    )
    monkeypatch.setattr("ynab_tools.config.PROFILE_FILE", profile_file)

    profile = load_profile()

    assert profile.card_last4 == {"1234": "Some Card"}
    assert profile.funding_priority == ["Housing", "Food"]


def test_save_profile_preserves_existing(monkeypatch, tmp_path):
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps({"card_last4": {"1234": "Some Card"}}))
    monkeypatch.setattr("ynab_tools.config.PROFILE_FILE", profile_file)
    monkeypatch.setattr("ynab_tools.config.CONFIG_DIR", tmp_path)

    save_profile(funding_priority=["Housing"])

    profile = load_profile()
    assert profile.card_last4 == {"1234": "Some Card"}
    assert profile.funding_priority == ["Housing"]


def _write_profile(monkeypatch, tmp_path, data):
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(data))
    monkeypatch.setattr("ynab_tools.config.PROFILE_FILE", profile_file)
    monkeypatch.setattr("ynab_tools.config.CONFIG_DIR", tmp_path)
    return profile_file


def test_load_profile_reads_every_money_setting(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {
        "essential_categories": ["Rent", "Groceries"],
        "college_categories": ["Tuition"],
        "savings_held_groups": ["Savings Goals"],
        "goals": {
            "safety_net": {"category": "Safety Net"},
            "car": {"category": "Next Car", "amount": 15000, "by": "2027-09-01"},
        },
        "goal_group": "Goals",
        "safety_net_months": 4,
        "checking_cushion_months": 1,
        "statement_max_age_days": 30,
        "statements": {"Bank Account": {"balance": 1234.56, "on": "2026-09-30"}},
    })

    profile = load_profile()

    assert profile.essential_categories == ["Rent", "Groceries"]
    assert profile.college_categories == ["Tuition"]
    assert profile.savings_held_groups == ["Savings Goals"]
    assert profile.goals == {
        "safety_net": Goal(category="Safety Net"),
        "car": Goal(category="Next Car", amount=15_000_000, by=date(2027, 9, 1)),
    }
    assert profile.goal_group == "Goals"
    assert profile.safety_net_months == 4
    assert profile.checking_cushion_months == 1
    assert profile.statement_max_age_days == 30
    assert profile.statements == {"Bank Account": Statement(balance=1_234_560, on=date(2026, 9, 30))}


def test_load_profile_exposes_the_superseded_emergency_fund_target(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {"emergency_fund_target": 10000})

    assert load_profile().emergency_fund_target == 10000


def test_save_statement_keeps_other_statements_and_keys(monkeypatch, tmp_path):
    _write_profile(monkeypatch, tmp_path, {
        "card_last4": {"1234": "Some Card"},
        "statements": {"Bank Account": {"balance": 100, "on": "2026-08-31"}},
    })

    save_statement("Savings Account", 2_500_000, date(2026, 9, 30))

    profile = load_profile()
    assert profile.card_last4 == {"1234": "Some Card"}
    assert profile.statements == {
        "Bank Account": Statement(balance=100_000, on=date(2026, 8, 31)),
        "Savings Account": Statement(balance=2_500_000, on=date(2026, 9, 30)),
    }


@pytest.mark.parametrize(("data", "key"), [
    ({"goals": {"car": {"category": "Next Car", "amount": "lots"}}}, "goals.car.amount"),
    ({"goals": {"car": {"category": "Next Car", "by": "next year"}}}, "goals.car.by"),
    ({"statements": {"Bank Account": {"balance": "1,000", "on": "2026-09-30"}}}, "statements.Bank Account.balance"),
    ({"statements": {"Bank Account": {"balance": 100, "on": "30/09/2026"}}}, "statements.Bank Account.on"),
])
def test_malformed_profile_value_names_the_key(monkeypatch, tmp_path, data, key):
    _write_profile(monkeypatch, tmp_path, data)

    with pytest.raises(ProfileError, match=key):
        load_profile()


def test_validate_rule_pattern_accepts_a_sound_regex():
    assert validate_rule_pattern(r"hotel|\binn\b|lodge") == ""


def test_validate_rule_pattern_rejects_a_control_character():
    """A \\b written outside a raw string becomes a backspace, never matching."""
    complaint = validate_rule_pattern("hotel|inn\b|lodge")

    assert "control character" in complaint


def test_validate_rule_pattern_rejects_an_uncompilable_regex():
    assert "not a valid regular expression" in validate_rule_pattern("hotel[")


def test_load_rules_skips_a_broken_rule_and_keeps_the_rest(monkeypatch, tmp_path, capsys):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"rules": [
        {"match": "inn\b", "category": "Travel"},
        {"match": "cafe", "category": "Eating Out"},
    ]}))
    monkeypatch.setattr("ynab_tools.config.RULES_FILE", rules_file)

    rules = load_rules()

    assert [r.category for r in rules] == ["Eating Out"]
    captured = capsys.readouterr()
    assert "skipping" in captured.err
    assert captured.out == ""
