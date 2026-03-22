"""Integration tests for the CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from import_cruiser.cli import main


@pytest.fixture()
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal sample project for CLI testing."""
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import mypkg.b\n")
    (pkg / "b.py").write_text("import mypkg.c\n")
    (pkg / "c.py").write_text("x = 1\n")
    return tmp_path


@pytest.fixture()
def cyclic_project(tmp_path: Path) -> Path:
    """Create a project with a circular dependency."""
    (tmp_path / "x.py").write_text("import y\n")
    (tmp_path / "y.py").write_text("import x\n")
    return tmp_path


class TestAnalyzeCommand:
    def test_default_json_output(self, sample_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", str(sample_project)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "modules" in data
        assert "dependencies" in data
        assert data["summary"]["modules"] >= 3

    def test_dot_output(self, sample_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, ["analyze", str(sample_project), "--format", "dot"]
        )
        assert result.exit_code == 0, result.output
        assert "digraph" in result.output

    def test_output_to_file(self, sample_project: Path, tmp_path: Path) -> None:
        out_file = tmp_path / "out.json"
        runner = CliRunner()
        result = runner.invoke(
            main, ["analyze", str(sample_project), "--output", str(out_file)]
        )
        assert result.exit_code == 0, result.output
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "modules" in data

    def test_cycle_detected(self, cyclic_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", str(cyclic_project)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["cycles"] >= 1


class TestValidateCommand:
    def test_no_config(self, sample_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(sample_project)])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "violations" in data
        assert data["violations"] == []

    def test_with_config_no_violations(
        self, sample_project: Path, tmp_path: Path
    ) -> None:
        config = {
            "rules": [
                {
                    "name": "allow-all",
                    "severity": "error",
                    "from": {},
                    "to": {},
                    "allow": True,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main, ["validate", str(sample_project), "--config", str(cfg_file)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["violations"] == []

    def test_with_config_violations(self, sample_project: Path, tmp_path: Path) -> None:
        config = {
            "rules": [
                {
                    "name": "no-b-to-c",
                    "severity": "error",
                    "from": {"path": "b$"},
                    "to": {"path": "c$"},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main, ["validate", str(sample_project), "--config", str(cfg_file)]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data["violations"]) >= 1

    def test_strict_flag_exits_nonzero_on_violation(
        self, sample_project: Path, tmp_path: Path
    ) -> None:
        config = {
            "rules": [
                {
                    "name": "no-deps",
                    "severity": "error",
                    "from": {},
                    "to": {},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["validate", str(sample_project), "--config", str(cfg_file), "--strict"],
        )
        assert result.exit_code != 0

    def test_strict_flag_ok_without_violations(self, sample_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["validate", str(sample_project), "--strict"])
        assert result.exit_code == 0

    def test_invalid_config_file(self, sample_project: Path, tmp_path: Path) -> None:
        bad_cfg = tmp_path / "bad.json"
        bad_cfg.write_text("{not valid json")
        runner = CliRunner()
        result = runner.invoke(
            main, ["validate", str(sample_project), "--config", str(bad_cfg)]
        )
        assert result.exit_code != 0

    def test_lint_output_flake8_format(
        self, sample_project: Path, tmp_path: Path
    ) -> None:
        config = {
            "rules": [
                {
                    "name": "no-b-to-c",
                    "severity": "error",
                    "from": {"path": "b$"},
                    "to": {"path": "c$"},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "validate",
                str(sample_project),
                "--config",
                str(cfg_file),
                "--output-format",
                "flake8",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "mypkg/b.py:1:1: IC001" in result.output

    def test_lint_output_pylint_format(
        self, sample_project: Path, tmp_path: Path
    ) -> None:
        config = {
            "rules": [
                {
                    "name": "no-b-to-c",
                    "severity": "warn",
                    "from": {"path": "b$"},
                    "to": {"path": "c$"},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "validate",
                str(sample_project),
                "--config",
                str(cfg_file),
                "--output-format",
                "pylint",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "mypkg/b.py:1: [IC002]" in result.output

    def test_lint_output_github_format(
        self, sample_project: Path, tmp_path: Path
    ) -> None:
        config = {
            "rules": [
                {
                    "name": "no-b-to-c",
                    "severity": "info",
                    "from": {"path": "b$"},
                    "to": {"path": "c$"},
                    "allow": False,
                }
            ]
        }
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(config))
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "validate",
                str(sample_project),
                "--config",
                str(cfg_file),
                "--output-format",
                "github",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "::notice file=mypkg/b.py,line=1,col=1::" in result.output


class TestExportCommand:
    def test_export_dot(self, sample_project: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["export", str(sample_project)])
        assert result.exit_code == 0, result.output
        assert "digraph" in result.output

    def test_export_to_file(self, sample_project: Path, tmp_path: Path) -> None:
        out_file = tmp_path / "graph.dot"
        runner = CliRunner()
        result = runner.invoke(
            main, ["export", str(sample_project), "--output", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()
        assert "digraph" in out_file.read_text()

    def test_export_cruiser_auto_uses_node_edges(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "mypkg"
        (pkg / "app").mkdir(parents=True)
        (pkg / "domain").mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "app" / "__init__.py").write_text("")
        (pkg / "domain" / "__init__.py").write_text("")
        (pkg / "app" / "api.py").write_text("import mypkg.domain.model\n")
        (pkg / "domain" / "model.py").write_text("x = 1\n")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export",
                str(tmp_path),
                "--format",
                "dot",
                "--style",
                "cruiser",
                "--edge-mode",
                "auto",
                "--exclude-path",
                "__init__\\.py$",
                "--cluster-depth",
                "3",
            ],
        )
        assert result.exit_code == 0, result.output
        assert '"mypkg.app.api" -> "mypkg.domain.model";' in result.output
        assert "ltail=" not in result.output
        assert "lhead=" not in result.output

    def test_export_default_style_is_cruiser_with_deeper_clusters(
        self, tmp_path: Path
    ) -> None:
        pkg = tmp_path / "src" / "mypkg" / "modules" / "company_events"
        pkg.mkdir(parents=True)
        shared = tmp_path / "src" / "mypkg" / "shared"
        shared.mkdir(parents=True)
        (tmp_path / "src" / "mypkg" / "__init__.py").parent.mkdir(
            parents=True, exist_ok=True
        )
        (tmp_path / "src" / "mypkg" / "__init__.py").write_text("")
        (tmp_path / "src" / "mypkg" / "modules" / "__init__.py").write_text("")
        (
            tmp_path / "src" / "mypkg" / "modules" / "company_events" / "__init__.py"
        ).write_text("")
        (tmp_path / "src" / "mypkg" / "shared" / "__init__.py").write_text("")
        (
            tmp_path / "src" / "mypkg" / "modules" / "company_events" / "api.py"
        ).write_text(
            "import mypkg.modules.company_events.model\nimport mypkg.shared.types\n"
        )
        (
            tmp_path / "src" / "mypkg" / "modules" / "company_events" / "model.py"
        ).write_text("x = 1\n")
        (tmp_path / "src" / "mypkg" / "shared" / "types.py").write_text("x = 1\n")

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export",
                str(tmp_path),
                "--format",
                "dot",
                "--exclude-path",
                "__init__\\.py$",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "rankdir=TB" in result.output
        assert "cluster_src_mypkg_modules_company_events" in result.output

    def test_export_default_style_auto_fallback_for_default_style(
        self, sample_project: Path
    ) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "export",
                str(sample_project),
                "--format",
                "dot",
                "--style",
                "default",
                "--edge-mode",
                "auto",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "ltail=" not in result.output
        assert "lhead=" not in result.output


class TestVersionCommand:
    def test_version_flag(self) -> None:
        from import_cruiser import __version__

        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output
