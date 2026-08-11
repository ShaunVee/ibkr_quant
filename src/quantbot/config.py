"""Configuration loading: non-secret settings from config.yaml, secrets from env.

`load_config()` returns a Config object combining both. Secrets are read lazily via
the Secrets accessor so a missing key only errors when a component actually needs it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]


class MissingSecret(RuntimeError):
    """Raised when a required environment secret is absent."""


@dataclass(slots=True)
class Secrets:
    """Lazy accessor over environment variables (loaded from .env)."""

    def get(self, name: str, required: bool = True, default: str | None = None) -> str | None:
        val = os.environ.get(name, default)
        if required and not val:
            raise MissingSecret(
                f"Environment variable {name!r} is required but not set. "
                f"Copy .env.example to .env and fill it in."
            )
        return val


@dataclass(slots=True)
class Config:
    raw: dict[str, Any]
    secrets: Secrets

    # --- convenience typed accessors over the yaml tree ---
    @property
    def base_currency(self) -> str:
        return self.raw.get("report", {}).get("base_currency", "USD")

    @property
    def timezone(self) -> str:
        return self.raw.get("report", {}).get("timezone", "UTC")

    @property
    def history_days(self) -> int:
        return int(self.raw.get("history_days", 400))

    @property
    def providers(self) -> dict[str, Any]:
        return self.raw.get("providers", {})

    @property
    def macro_series(self) -> dict[str, str]:
        return self.raw.get("macro_series", {})

    @property
    def risk(self) -> dict[str, Any]:
        return self.raw.get("risk", {})

    @property
    def narrative(self) -> dict[str, Any]:
        return self.raw.get("narrative", {})

    def risk_param(self, key: str, default: Any = None) -> Any:
        return self.risk.get(key, default)


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config.yaml + .env into a Config object."""
    load_dotenv(_REPO_ROOT / ".env")

    path = Path(config_path) if config_path else _REPO_ROOT / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    return Config(raw=raw, secrets=Secrets())
