"""Tests for the config module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydepend.config import ConfigError, load_config, validate_config, default_config


class TestValidateConfig:
    def test_valid_config(self) -> None:
        config = {
            "rules": [
                {
                    "name": "rule1",
                    "severity": "error",
                    "from": {"path": "a"},
                    "to": {"path": "b"},
                }
            ]
        }
        validate_config(config)  # should not raise

    def test_invalid_top_level(self) -> None:
        with pytest.raises(ConfigError, match="JSON object"):
            validate_config("not a dict")  # type: ignore[arg-type]

    def test_rules_not_list(self) -> None:
        with pytest.raises(ConfigError, match="list"):
            validate_config({"rules": "not a list"})

    def test_rule_missing_fields(self) -> None:
        with pytest.raises(ConfigError, match="missing required fields"):
            validate_config({"rules": [{"name": "r"}]})

    def test_invalid_severity(self) -> None:
        with pytest.raises(ConfigError, match="invalid severity"):
            validate_config(
                {
                    "rules": [
                        {
                            "name": "r",
                            "severity": "critical",
                            "from": {},
                            "to": {},
                        }
                    ]
                }
            )

    def test_from_not_object(self) -> None:
        with pytest.raises(ConfigError):
            validate_config(
                {
                    "rules": [
                        {
                            "name": "r",
                            "severity": "error",
                            "from": "string",
                            "to": {},
                        }
                    ]
                }
            )


class TestLoadConfig:
    def test_load_valid_file(self, tmp_path: Path) -> None:
        config = {
            "rules": [
                {
                    "name": "no-ui-to-data",
                    "severity": "error",
                    "from": {"path": "ui"},
                    "to": {"path": "data"},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "pydepend.json"
        cfg_file.write_text(json.dumps(config))
        loaded = load_config(cfg_file)
        assert loaded["rules"][0]["name"] == "no-ui-to-data"

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid}")
        with pytest.raises(ConfigError, match="Invalid JSON"):
            load_config(bad)


class TestDefaultConfig:
    def test_returns_copy(self) -> None:
        c1 = default_config()
        c2 = default_config()
        c1["rules"].append("something")
        assert c2["rules"] == []
