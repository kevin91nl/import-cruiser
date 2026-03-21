"""Load and validate import_cruiser configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeAlias


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONDict: TypeAlias = dict[str, JSONValue]

DEFAULT_CONFIG: JSONDict = {
    "rules": [],
    "options": {
        "include_external": False,
    },
}

# JSON Schema (subset) for a single rule
RULE_REQUIRED_FIELDS = {"name", "severity", "from", "to"}
VALID_SEVERITIES = {"error", "warn", "info"}


class ConfigError(ValueError):
    """Raised when the configuration is invalid."""


def load_config(path: str | Path) -> JSONDict:
    """Load a JSON configuration file and return the parsed dict."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")
    try:
        with config_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in configuration file: {exc}") from exc

    validate_config(raw)
    return raw


def validate_config(config: JSONDict) -> None:
    """Raise ConfigError if *config* does not conform to the expected schema."""
    if not isinstance(config, dict):
        raise ConfigError("Configuration must be a JSON object.")

    rules = config.get("rules", [])
    if not isinstance(rules, list):
        raise ConfigError("'rules' must be a list.")

    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ConfigError(f"Rule #{i} must be an object.")
        missing = RULE_REQUIRED_FIELDS - rule.keys()
        if missing:
            raise ConfigError(f"Rule #{i} is missing required fields: {missing}")
        if rule["severity"] not in VALID_SEVERITIES:
            raise ConfigError(
                f"Rule #{i} has invalid severity '{rule['severity']}'. "
                f"Must be one of {VALID_SEVERITIES}."
            )
        for field in ("from", "to"):
            if not isinstance(rule[field], dict):
                raise ConfigError(f"Rule #{i} '{field}' must be an object.")


def default_config() -> JSONDict:
    """Return a deep copy of the default configuration."""
    import copy

    return copy.deepcopy(DEFAULT_CONFIG)
