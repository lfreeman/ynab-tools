"""Configuration loading from file and environment variables."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

CONFIG_DIR = Path.home() / ".config" / "ynab-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / ".env"


@dataclass
class Config:
    api_key: str = ""
    default_budget_id: str = ""
    default_budget_name: str = ""


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
