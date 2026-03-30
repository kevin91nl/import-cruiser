"""CLI entry point for import-cruiser."""
# pylint: disable=duplicate-code

from __future__ import annotations

import os
import shlex
import re
import sys
from pathlib import Path
from typing import Optional, cast

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

import click

from import_cruiser import __version__
from import_cruiser.analyzer import Analyzer
from import_cruiser.config import JSONDict, ConfigError, default_config, load_config
from import_cruiser.exporter import (
    ViolationLike,
    export_dot,
    export_html,
    export_json,
    export_svg,
)
from import_cruiser.graph import (
    DependencyGraph,
    aggregate_by_path,
    collapse_graph,
    detect_cycles,
    filter_graph,
    prune_isolated_modules,
    prune_orphan_init_modules,
)
from import_cruiser.validator import Validator, Violation

DEFAULT_NOISE_PATH_PATTERNS: tuple[str, ...] = (
    r"__init__\.py$",
    r"/\.venv/",
    r"/site-packages/",
    r"/tests/",
    r"/scripts/",
    r"/examples/",
    r"/stress/",
    r"/migrations/",
)

DB_CONNECTOR_IMPORT_PATTERNS: tuple[str, ...] = (
    r"(^|\.)sqlalchemy(\.|$)",
    r"(^|\.)sqlmodel(\.|$)",
    r"(^|\.)alembic(\.|$)",
    r"(^|\.)psycopg(2)?(\.|$)",
    r"(^|\.)asyncpg(\.|$)",
    r"(^|\.)pg8000(\.|$)",
    r"(^|\.)aiopg(\.|$)",
    r"(^|\.)databases(\.|$)",
    r"(^|\.)postgres(\.|$)",
)


@click.group()
@click.version_option(__version__, prog_name="import-cruiser")
def main() -> None:
    """import-cruiser – Analyze, validate, and visualize Python import dependencies."""


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
    type=click.Choice(["json", "dot", "svg", "html"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--normalize-hyphens/--no-normalize-hyphens",
    default=True,
    show_default=True,
    help="Normalize '-' to '_' in module names.",
)
@click.option(
    "--layout",
    type=click.Choice(["dot", "sfdp", "neato", "fdp"], case_sensitive=False),
    default="dot",
    show_default=True,
    help="Graphviz layout engine for svg/html output.",
)
@click.option(
    "--style",
    type=click.Choice(
        ["default", "archi", "cruiser", "navigator", "depcruise"],
        case_sensitive=False,
    ),
    default="depcruise",
    show_default=True,
    help="Graph styling preset.",
)
@click.option(
    "--rankdir",
    type=click.Choice(["LR", "TB"], case_sensitive=False),
    default="LR",
    show_default=True,
    help="Graph direction for dot layouts.",
)
@click.option(
    "--include",
    multiple=True,
    help="Regex to include module names (repeatable).",
)
@click.option(
    "--exclude",
    multiple=True,
    help="Regex to exclude module names (repeatable).",
)
@click.option(
    "--include-path",
    multiple=True,
    help="Regex to include file paths (repeatable).",
)
@click.option(
    "--exclude-path",
    multiple=True,
    help="Regex to exclude file paths (repeatable).",
)
@click.option(
    "--exclude-common-noise-paths/--no-exclude-common-noise-paths",
    default=False,
    show_default=True,
    help=(
        "Exclude common noise paths "
        "(tests/scripts/examples/stress/migrations) from graph generation."
    ),
)
@click.option(
    "--focus",
    multiple=True,
    help="Regex to focus on module names and neighbors (repeatable).",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    show_default=True,
    help="Neighbor depth for focus selection.",
)
@click.option(
    "--collapse-depth",
    type=int,
    default=0,
    show_default=True,
    help="Collapse modules to a package depth (0 disables).",
)
@click.option(
    "--cluster-depth",
    type=int,
    default=5,
    show_default=True,
    help="Cluster modules by package depth (0 disables).",
)
@click.option(
    "--cluster-mode",
    type=click.Choice(["path", "module"], case_sensitive=False),
    default="path",
    show_default=True,
    help="Cluster by filesystem path or module name.",
)
@click.option(
    "--aggregate-depth",
    type=int,
    default=8,
    show_default=True,
    help="Aggregate modules by path depth (0 disables).",
)
@click.option(
    "--leaf-pattern",
    multiple=True,
    help="Regex for leaf filenames to keep as nodes (repeatable).",
)
@click.option(
    "--edge-mode",
    type=click.Choice(["node", "cluster", "auto"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Edge rendering mode for graphs.",
)
@click.option(
    "--prune-isolated/--keep-isolated",
    default=False,
    show_default=True,
    help="Drop modules without incoming/outgoing edges after filtering.",
)
@click.option(
    "--include-db-connectors/--no-include-db-connectors",
    default=False,
    show_default=True,
    help="Include DB connector imports as external nodes.",
)
@click.option(
    "--include-http-hosts/--no-include-http-hosts",
    default=False,
    show_default=True,
    help="Include HTTP request hosts as external nodes.",
)
@click.option(
    "--include-external-deps/--no-include-external-deps",
    default=False,
    show_default=True,
    help="Include non-dev external dependencies as grouped nodes.",
)
@click.option(
    "--external-deps-include",
    multiple=True,
    help="Regex to include external dependency roots (repeatable).",
)
@click.option(
    "--external-deps-exclude",
    multiple=True,
    help="Regex to exclude external dependency roots (repeatable).",
)
@click.option(
    "--show-loc/--no-show-loc",
    default=False,
    show_default=True,
    help="Show LOC in node/cluster labels for dot/svg/html exports.",
)
def cmd_analyze(
    path: str,
    output: Optional[str],
    fmt: str,
    normalize_hyphens: bool,
    layout: str,
    style: str,
    rankdir: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    include_path: tuple[str, ...],
    exclude_path: tuple[str, ...],
    exclude_common_noise_paths: bool,
    focus: tuple[str, ...],
    focus_depth: int,
    collapse_depth: int,
    cluster_depth: int,
    cluster_mode: str,
    aggregate_depth: int,
    leaf_pattern: tuple[str, ...],
    edge_mode: str,
    prune_isolated: bool,
    include_db_connectors: bool,
    include_http_hosts: bool,
    include_external_deps: bool,
    external_deps_include: tuple[str, ...],
    external_deps_exclude: tuple[str, ...],
    show_loc: bool,
) -> None:
    """Analyze Python import dependencies in PATH and output results.

    PATH defaults to the current working directory.
    """
    effective_exclude_paths = _effective_exclude_paths(
        exclude_path,
        exclude_common_noise_paths,
    )
    external_patterns, external_package_roots = _external_dependency_info(
        include_db_connectors,
        include_external_deps,
        path,
        include_paths=list(include_path),
        include_roots=list(external_deps_include),
        exclude_roots=list(external_deps_exclude),
    )
    graph = Analyzer(
        path,
        normalize_hyphens=normalize_hyphens,
        include_paths=list(include_path),
        exclude_paths=effective_exclude_paths,
        include_external_patterns=external_patterns,
        include_http_hosts=include_http_hosts,
    ).analyze()
    graph, layout, rankdir, cluster_depth, cluster_mode, style, edge_mode = (
        _apply_graph_options(
            graph,
            include=list(include),
            exclude=list(exclude),
            include_paths=list(include_path),
            exclude_paths=effective_exclude_paths,
            focus=list(focus),
            focus_depth=focus_depth,
            collapse_depth=collapse_depth,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            aggregate_depth=aggregate_depth,
            leaf_patterns=list(leaf_pattern),
            layout=layout,
            rankdir=rankdir,
            style=style,
            edge_mode=edge_mode,
            prune_isolated=prune_isolated,
        )
    )

    if fmt == "dot":
        result = export_dot(
            graph,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
        )
    elif fmt == "svg":
        result = _export_svg(
            graph,
            layout=layout,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
        )
    elif fmt == "html":
        result = export_html(
            graph,
            engine=layout,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
            generation_command=_invocation_command(),
        )
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
@click.option(
    "--output-format",
    "output_format",
    type=click.Choice(["json", "flake8", "pylint", "github"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format for validation results (use linter formats for CI/editor integration).",
)
def cmd_validate(
    path: str,
    config_path: Optional[str],
    output: Optional[str],
    strict: bool,
    output_format: str,
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
    rules = _extract_rules(config)
    violations = Validator(rules).validate(graph)

    if output_format == "json":
        result = export_json(
            graph, violations=cast(list[ViolationLike], violations), cycles=cycles
        )
    else:
        result = _format_lint_output(
            violations,
            graph,
            root_path=Path(path).resolve(),
            output_format=output_format,
        )
    _write_output(result, output)

    if strict and violations:
        sys.exit(1)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@main.command("export")
@click.argument("path", default=".", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--normalize-hyphens/--no-normalize-hyphens",
    default=True,
    show_default=True,
    help="Normalize '-' to '_' in module names.",
)
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to a JSON configuration file for highlighting violations.",
)
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(["dot", "svg", "html"], case_sensitive=False),
    default="dot",
    show_default=True,
    help="Export format.",
)
@click.option(
    "--layout",
    type=click.Choice(["dot", "sfdp", "neato", "fdp"], case_sensitive=False),
    default="dot",
    show_default=True,
    help="Graphviz layout engine for svg/html output.",
)
@click.option(
    "--style",
    type=click.Choice(
        ["default", "archi", "cruiser", "navigator", "depcruise"],
        case_sensitive=False,
    ),
    default="depcruise",
    show_default=True,
    help="Graph styling preset.",
)
@click.option(
    "--rankdir",
    type=click.Choice(["LR", "TB"], case_sensitive=False),
    default="LR",
    show_default=True,
    help="Graph direction for dot layouts.",
)
@click.option(
    "--include",
    multiple=True,
    help="Regex to include module names (repeatable).",
)
@click.option(
    "--exclude",
    multiple=True,
    help="Regex to exclude module names (repeatable).",
)
@click.option(
    "--include-path",
    multiple=True,
    help="Regex to include file paths (repeatable).",
)
@click.option(
    "--exclude-path",
    multiple=True,
    help="Regex to exclude file paths (repeatable).",
)
@click.option(
    "--exclude-common-noise-paths/--no-exclude-common-noise-paths",
    default=False,
    show_default=True,
    help=(
        "Exclude common noise paths "
        "(tests/scripts/examples/stress/migrations) from graph generation."
    ),
)
@click.option(
    "--focus",
    multiple=True,
    help="Regex to focus on module names and neighbors (repeatable).",
)
@click.option(
    "--focus-depth",
    type=int,
    default=1,
    show_default=True,
    help="Neighbor depth for focus selection.",
)
@click.option(
    "--collapse-depth",
    type=int,
    default=0,
    show_default=True,
    help="Collapse modules to a package depth (0 disables).",
)
@click.option(
    "--cluster-depth",
    type=int,
    default=5,
    show_default=True,
    help="Cluster modules by package depth (0 disables).",
)
@click.option(
    "--cluster-mode",
    type=click.Choice(["path", "module"], case_sensitive=False),
    default="path",
    show_default=True,
    help="Cluster by filesystem path or module name.",
)
@click.option(
    "--aggregate-depth",
    type=int,
    default=8,
    show_default=True,
    help="Aggregate modules by path depth (0 disables).",
)
@click.option(
    "--leaf-pattern",
    multiple=True,
    help="Regex for leaf filenames to keep as nodes (repeatable).",
)
@click.option(
    "--edge-mode",
    type=click.Choice(["node", "cluster", "auto"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="Edge rendering mode for graphs.",
)
@click.option(
    "--prune-isolated/--keep-isolated",
    default=False,
    show_default=True,
    help="Drop modules without incoming/outgoing edges after filtering.",
)
@click.option(
    "--include-db-connectors/--no-include-db-connectors",
    default=False,
    show_default=True,
    help="Include DB connector imports as external nodes.",
)
@click.option(
    "--include-http-hosts/--no-include-http-hosts",
    default=False,
    show_default=True,
    help="Include HTTP request hosts as external nodes.",
)
@click.option(
    "--include-external-deps/--no-include-external-deps",
    default=False,
    show_default=True,
    help="Include non-dev external dependencies as grouped nodes.",
)
@click.option(
    "--external-deps-include",
    multiple=True,
    help="Regex to include external dependency roots (repeatable).",
)
@click.option(
    "--external-deps-exclude",
    multiple=True,
    help="Regex to exclude external dependency roots (repeatable).",
)
@click.option(
    "--show-loc/--no-show-loc",
    default=False,
    show_default=True,
    help="Show LOC in node/cluster labels for dot/svg/html exports.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write output to a file instead of stdout.",
)
def cmd_export(
    path: str,
    fmt: str,
    normalize_hyphens: bool,
    config_path: Optional[str],
    layout: str,
    style: str,
    rankdir: str,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    include_path: tuple[str, ...],
    exclude_path: tuple[str, ...],
    exclude_common_noise_paths: bool,
    focus: tuple[str, ...],
    focus_depth: int,
    collapse_depth: int,
    cluster_depth: int,
    cluster_mode: str,
    aggregate_depth: int,
    leaf_pattern: tuple[str, ...],
    edge_mode: str,
    prune_isolated: bool,
    include_db_connectors: bool,
    include_http_hosts: bool,
    include_external_deps: bool,
    external_deps_include: tuple[str, ...],
    external_deps_exclude: tuple[str, ...],
    show_loc: bool,
    output: Optional[str],
) -> None:
    """Export the dependency graph of PATH to the specified format.

    PATH defaults to the current working directory.  Use ``analyze --format dot``
    for quick one-step output; this command is for explicit graph exports.
    """
    effective_exclude_paths = _effective_exclude_paths(
        exclude_path,
        exclude_common_noise_paths,
    )
    external_patterns, external_package_roots = _external_dependency_info(
        include_db_connectors,
        include_external_deps,
        path,
        include_paths=list(include_path),
        include_roots=list(external_deps_include),
        exclude_roots=list(external_deps_exclude),
    )
    graph = Analyzer(
        path,
        normalize_hyphens=normalize_hyphens,
        include_paths=list(include_path),
        exclude_paths=effective_exclude_paths,
        include_external_patterns=external_patterns,
        include_http_hosts=include_http_hosts,
    ).analyze()
    graph, layout, rankdir, cluster_depth, cluster_mode, style, edge_mode = (
        _apply_graph_options(
            graph,
            include=list(include),
            exclude=list(exclude),
            include_paths=list(include_path),
            exclude_paths=effective_exclude_paths,
            focus=list(focus),
            focus_depth=focus_depth,
            collapse_depth=collapse_depth,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            aggregate_depth=aggregate_depth,
            leaf_patterns=list(leaf_pattern),
            layout=layout,
            rankdir=rankdir,
            style=style,
            edge_mode=edge_mode,
            prune_isolated=prune_isolated,
        )
    )
    violations = _load_violations(config_path, graph)
    if fmt == "dot":
        result = export_dot(
            graph,
            violations=cast(list[ViolationLike], violations),
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
        )
    elif fmt == "svg":
        result = _export_svg(
            graph,
            cast(list[ViolationLike], violations),
            layout=layout,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
        )
    else:
        result = export_html(
            graph,
            violations=cast(list[ViolationLike], violations),
            engine=layout,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
            generation_command=_invocation_command(),
        )
    _write_output(result, output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_output(content: str, output: Optional[str]) -> None:
    if content and not content.endswith("\n"):
        content += "\n"
    if output:
        output_path = Path(output).resolve()
        output_path.write_text(content, encoding="utf-8")
        click.echo(f"Output written to {output_path}", err=True)
    else:
        click.echo(content)


def _invocation_command() -> str:
    argv = getattr(sys, "orig_argv", None)
    parts = argv if isinstance(argv, list) and argv else sys.argv
    cwd = Path.cwd().resolve()
    normalized = [
        _normalize_command_part(part, index, cwd) for index, part in enumerate(parts)
    ]
    command = " ".join(shlex.quote(part) for part in normalized)
    pythonpath = _normalized_pythonpath(cwd)
    if pythonpath:
        return f"PYTHONPATH={shlex.quote(pythonpath)} {command}"
    return command


def _normalize_command_part(part: str, index: int, cwd: Path) -> str:
    value = str(part)
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    if index == 0:
        executable = candidate.name or value
        if executable.lower() == "python":
            return "python3"
        return executable
    try:
        rel = candidate.resolve().relative_to(cwd)
        return str(rel).replace("\\", "/") or "."
    except ValueError:
        return value


def _normalized_pythonpath(cwd: Path) -> str:
    raw_pythonpath = os.environ.get("PYTHONPATH", "").strip()
    if not raw_pythonpath:
        return ""
    normalized_entries: list[str] = []
    for entry in raw_pythonpath.split(os.pathsep):
        value = entry.strip()
        if not value:
            continue
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                relative = candidate.resolve().relative_to(cwd)
                value = str(relative).replace("\\", "/") or "."
            except ValueError:
                value = str(candidate).replace("\\", "/")
        normalized_entries.append(value)
    return os.pathsep.join(normalized_entries)


def _effective_exclude_paths(
    exclude_path: tuple[str, ...],
    exclude_common_noise_paths: bool,
) -> list[str]:
    effective = list(exclude_path)
    if not exclude_common_noise_paths:
        return effective
    for pattern in DEFAULT_NOISE_PATH_PATTERNS:
        if pattern not in effective:
            effective.append(pattern)
    return effective


def _external_patterns_for_db(
    include_db_connectors: bool,
) -> list[str]:
    if not include_db_connectors:
        return []
    return list(DB_CONNECTOR_IMPORT_PATTERNS)


def _external_dependency_info(
    include_db_connectors: bool,
    include_external_deps: bool,
    project_path: str,
    include_paths: list[str] | None = None,
    include_roots: list[str] | None = None,
    exclude_roots: list[str] | None = None,
) -> tuple[list[str], set[str]]:
    patterns = _external_patterns_for_db(include_db_connectors)
    roots: set[str] = set()
    if not include_external_deps:
        return patterns, roots
    dependency_roots = _non_dev_dependency_roots(
        project_path,
        include_paths=include_paths or [],
    )
    if not dependency_roots:
        return patterns, roots
    dependency_roots = _filter_dependency_roots(
        dependency_roots,
        include_roots=include_roots or [],
        exclude_roots=exclude_roots or [],
    )
    if not dependency_roots:
        return patterns, roots
    roots.update(dependency_roots)
    patterns.extend(
        rf"(^|\.){re.escape(root)}(\.|$)" for root in sorted(dependency_roots)
    )
    return patterns, roots


def _filter_dependency_roots(
    roots: set[str],
    include_roots: list[str],
    exclude_roots: list[str],
) -> set[str]:
    filtered = set(roots)
    if include_roots:
        filtered = {
            root
            for root in filtered
            if any(re.search(pattern, root) for pattern in include_roots)
        }
    if exclude_roots:
        filtered = {
            root
            for root in filtered
            if not any(re.search(pattern, root) for pattern in exclude_roots)
        }
    return filtered


def _non_dev_dependency_roots(
    project_path: str,
    include_paths: list[str] | None = None,
) -> set[str]:
    candidate = Path(project_path)
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = candidate.absolute()

    pyproject_paths: set[Path] = set()
    root_pyproject = _find_pyproject(candidate)
    if root_pyproject is not None:
        pyproject_paths.add(root_pyproject)

    include_patterns = include_paths or []
    if include_patterns:
        pyproject_paths.update(
            _find_pyprojects_for_include_paths(candidate, include_patterns)
        )

    if not pyproject_paths:
        return set()

    roots: set[str] = set()
    for pyproject_path in pyproject_paths:
        roots.update(_non_dev_dependency_roots_from_pyproject(pyproject_path))
    return roots


def _find_pyprojects_for_include_paths(
    project_root: Path,
    include_paths: list[str],
) -> set[Path]:
    compiled: list[re.Pattern[str]] = []
    for pattern in include_paths:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            continue
    if not compiled:
        return set()

    pyproject_paths: set[Path] = set()
    for py_file in project_root.rglob("*.py"):
        rel_posix = py_file.relative_to(project_root).as_posix()
        abs_posix = py_file.as_posix()
        if not any(
            regex.search(rel_posix) or regex.search(abs_posix) for regex in compiled
        ):
            continue
        pyproject_path = _find_pyproject(py_file.parent)
        if pyproject_path is not None:
            pyproject_paths.add(pyproject_path)
    return pyproject_paths


def _non_dev_dependency_roots_from_pyproject(pyproject_path: Path) -> set[str]:
    try:
        with pyproject_path.open("rb") as fo:
            data = tomllib.load(fo)
    except (OSError, tomllib.TOMLDecodeError):
        return set()

    roots: set[str] = set()
    roots.update(_poetry_dependency_roots(data))
    roots.update(_project_dependency_roots(data))
    return roots


def _poetry_dependency_roots(data: dict[str, object]) -> set[str]:
    tool = data.get("tool")
    if not isinstance(tool, dict):
        return set()
    poetry = tool.get("poetry")
    if not isinstance(poetry, dict):
        return set()
    dependencies = poetry.get("dependencies")
    if not isinstance(dependencies, dict):
        return set()

    roots: set[str] = set()
    for name in dependencies.keys():
        if not isinstance(name, str) or name == "python":
            continue
        roots.add(name.replace("-", "_"))
    return roots


def _project_dependency_roots(data: dict[str, object]) -> set[str]:
    project = data.get("project")
    if not isinstance(project, dict):
        return set()

    dependencies = project.get("dependencies")
    if not isinstance(dependencies, list):
        return set()

    roots: set[str] = set()
    for item in dependencies:
        if not isinstance(item, str):
            continue
        root = _project_dependency_name(item)
        if root:
            roots.add(root)
    return roots


def _project_dependency_name(requirement: str) -> str | None:
    candidate = requirement.strip().split(";", maxsplit=1)[0].strip()
    if not candidate:
        return None
    if "[" in candidate:
        candidate = candidate.split("[", maxsplit=1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)", candidate)
    if not match:
        return None
    return match.group(1).replace("-", "_")


def _find_pyproject(start: Path) -> Path | None:
    current = start
    while True:
        candidate = current / "pyproject.toml"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _apply_graph_options(
    graph,
    include: list[str],
    exclude: list[str],
    include_paths: list[str],
    exclude_paths: list[str],
    focus: list[str],
    focus_depth: int,
    collapse_depth: int,
    cluster_depth: int,
    cluster_mode: str,
    aggregate_depth: int,
    leaf_patterns: list[str],
    layout: str,
    rankdir: str,
    style: str,
    edge_mode: str,
    prune_isolated: bool,
):
    filtered = filter_graph(
        graph,
        include=include or None,
        exclude=exclude or None,
        include_paths=include_paths or None,
        exclude_paths=exclude_paths or None,
        focus=focus or None,
        focus_depth=focus_depth,
    )
    filtered = _drop_dangling_init_modules(filtered)
    if style in {"archi", "navigator"}:
        layout = "dot"
        rankdir = "TB"
        cluster_mode = "path"
        if aggregate_depth == 0:
            aggregate_depth = 20
        if not leaf_patterns:
            leaf_patterns = [
                r"__init__\.py",
                r"models?\.py",
                r"schema\.py",
                r"types?\.py",
                r"dto\.py",
                r"api\.py",
                r"service\.py",
                r"repository\.py",
                r"client\.py",
                r"config\.py",
                r"settings?\.py",
            ]
        if cluster_depth == 0:
            cluster_depth = aggregate_depth
        compiled_leaf = [re.compile(p) for p in leaf_patterns]
        aggregated = aggregate_by_path(
            filtered,
            aggregate_depth,
            leaf_patterns=compiled_leaf,
        )
        if len(aggregated.modules) < 4:
            expanded = leaf_patterns + [r"\.py$"]
            aggregated = aggregate_by_path(
                filtered,
                aggregate_depth,
                leaf_patterns=[re.compile(p) for p in expanded],
            )
        if aggregated.modules:
            filtered = aggregated
        collapse_depth = 0
        if edge_mode == "auto":
            edge_mode = "cluster"
    if style == "cruiser":
        layout = "dot"
        rankdir = "TB"
        cluster_mode = "path"
        if cluster_depth == 3:
            cluster_depth = 5
        if edge_mode == "auto":
            edge_mode = "node"
    if edge_mode == "auto":
        edge_mode = "node"

    collapsed = collapse_graph(filtered, collapse_depth)
    pruned = prune_orphan_init_modules(collapsed)
    if prune_isolated:
        pruned = prune_isolated_modules(pruned)
    return pruned, layout, rankdir, cluster_depth, cluster_mode, style, edge_mode


def _drop_dangling_init_modules(graph: DependencyGraph) -> DependencyGraph:
    connected: set[str] = set()
    for dep in graph.dependencies:
        connected.add(dep.source)
        connected.add(dep.target)

    dangling_init = {
        module.name
        for module in graph.modules
        if module.path.endswith("__init__.py") and module.name not in connected
    }
    if not dangling_init:
        return graph

    trimmed = DependencyGraph()
    for module in graph.modules:
        if module.name in dangling_init:
            continue
        trimmed.add_module(module)

    keep_names = {module.name for module in trimmed.modules}
    for dep in graph.dependencies:
        if dep.source in keep_names and dep.target in keep_names:
            trimmed.add_dependency(dep)
    return trimmed


def _export_svg(
    graph: DependencyGraph,
    violations: list[ViolationLike] | None = None,
    layout: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 3,
    cluster_mode: str = "path",
    style: str = "depcruise",
    edge_mode: str = "node",
    show_loc: bool = False,
    external_package_roots: set[str] | None = None,
) -> str:
    if violations is None:
        violations = []
    try:
        return export_svg(
            graph,
            violations=violations,
            engine=layout,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode=edge_mode,
            show_loc=show_loc,
            external_package_roots=external_package_roots,
        )
    except RuntimeError as exc:
        click.echo(f"Graphviz error: {exc}", err=True)
        sys.exit(2)


def _load_violations(
    config_path: Optional[str], graph: DependencyGraph
) -> list[Violation]:
    if not config_path:
        return []
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)
    rules = _extract_rules(config)
    return Validator(rules).validate(graph)


def _extract_rules(config: JSONDict) -> list[JSONDict]:
    rules_raw = config.get("rules", [])
    if not isinstance(rules_raw, list):
        return []
    return [rule for rule in rules_raw if isinstance(rule, dict)]


def _format_lint_output(
    violations: list[Violation],
    graph: DependencyGraph,
    root_path: Path,
    output_format: str,
) -> str:
    if not violations:
        return ""

    dep_lines = {(d.source, d.target): d.line for d in graph.dependencies}
    lines: list[str] = []

    for violation in violations:
        module = graph.get_module(violation.source)
        raw_path = module.path if module else violation.source.replace(".", "/") + ".py"
        rel_path = _to_display_path(raw_path, root_path)
        line_no = dep_lines.get((violation.source, violation.target), 1) or 1
        col_no = 1
        code = _lint_code(violation.severity)
        message = f"{violation.message} [rule: {violation.rule_name}]"

        if output_format == "flake8":
            lines.append(f"{rel_path}:{line_no}:{col_no}: {code} {message}")
        elif output_format == "pylint":
            lines.append(f"{rel_path}:{line_no}: [{code}] {message}")
        else:
            level = _github_level(violation.severity)
            escaped = (
                message.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
            )
            lines.append(
                f"::{level} file={rel_path},line={line_no},col={col_no}::{escaped}"
            )

    return "\n".join(lines)


def _to_display_path(path_value: str, root_path: Path) -> str:
    raw = Path(path_value)
    try:
        return str(raw.resolve().relative_to(root_path)).replace("\\", "/")
    except ValueError:
        return str(raw).replace("\\", "/")


def _lint_code(severity: str) -> str:
    return {
        "error": "IC001",
        "warn": "IC002",
        "info": "IC003",
    }.get(severity, "IC001")


def _github_level(severity: str) -> str:
    return {
        "error": "error",
        "warn": "warning",
        "info": "notice",
    }.get(severity, "error")


if __name__ == "__main__":
    main()
