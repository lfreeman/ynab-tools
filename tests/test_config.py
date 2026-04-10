"""Tests for configuration management."""

import json

from ynab_tools.config import Config, load_config, save_config


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
