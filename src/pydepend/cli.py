"""CLI entry point for pydepend."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from pydepend import __version__
from pydepend.analyzer import Analyzer
from pydepend.config import ConfigError, default_config, load_config
from pydepend.detector import detect_cycles
from pydepend.exporter import export_dot, export_json
from pydepend.validator import Validator


@click.group()
@click.version_option(__version__, prog_name="pydepend")
def main() -> None:
    """pydepend – Analyze, validate, and visualize Python import dependencies."""


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@main.command("analyze")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write output to a file instead of stdout.",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["json", "dot"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format.",
)
def cmd_analyze(path: str, output: Optional[str], fmt: str) -> None:
    """Analyze Python import dependencies in PATH and output results.

    PATH defaults to the current working directory.
    """
    graph = Analyzer(path).analyze()

    if fmt == "dot":
        result = export_dot(graph)
    else:
        cycles = detect_cycles(graph)
        result = export_json(graph, cycles=cycles)

    _write_output(result, output)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@main.command("validate")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a JSON configuration file.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write output to a file instead of stdout.",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit with non-zero status if there are any violations.",
)
def cmd_validate(
    path: str,
    config_path: Optional[str],
    output: Optional[str],
    strict: bool,
) -> None:
    """Validate Python import dependencies in PATH against configured rules.

    PATH defaults to the current working directory.
    """
    try:
        config = load_config(config_path) if config_path else default_config()
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)

    graph = Analyzer(path).analyze()
    cycles = detect_cycles(graph)
    rules = config.get("rules", [])
    violations = Validator(rules).validate(graph)

    result = export_json(graph, violations=violations, cycles=cycles)
    _write_output(result, output)

    if strict and violations:
        sys.exit(1)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command("export")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["dot"], case_sensitive=False),
    default="dot",
    show_default=True,
    help="Export format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write output to a file instead of stdout.",
)
def cmd_export(path: str, fmt: str, output: Optional[str]) -> None:
    """Export the dependency graph of PATH to the specified format.

    PATH defaults to the current working directory.  Use ``analyze --format dot``
    for quick one-step output; this command is for explicit graph exports.
    """
    graph = Analyzer(path).analyze()
    result = export_dot(graph)
    _write_output(result, output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_output(content: str, output: Optional[str]) -> None:
    if output:
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"Output written to {output}", err=True)
    else:
        click.echo(content)
