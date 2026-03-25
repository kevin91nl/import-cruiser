"""CLI entry point for import-cruiser."""
# pylint: disable=duplicate-code

from __future__ import annotations

import sys
import re
from pathlib import Path
from typing import Optional, cast

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
    prune_orphan_init_modules,
)
from import_cruiser.validator import Validator, Violation

DEFAULT_NOISE_PATH_PATTERNS: tuple[str, ...] = (
    r"__init__\.py$",
    r"/\.venv/",
    r"/site-packages/",
    r"/tests/",
    r"/scripts/",
    r"/migrations/",
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
        "(tests/scripts/migrations) from graph generation."
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
) -> None:
    """Analyze Python import dependencies in PATH and output results.

    PATH defaults to the current working directory.
    """
    effective_exclude_paths = _effective_exclude_paths(
        exclude_path,
        exclude_common_noise_paths,
    )
    graph = Analyzer(
        path,
        normalize_hyphens=normalize_hyphens,
        include_paths=list(include_path),
        exclude_paths=effective_exclude_paths,
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
        "(tests/scripts/migrations) from graph generation."
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
    graph = Analyzer(
        path,
        normalize_hyphens=normalize_hyphens,
        include_paths=list(include_path),
        exclude_paths=effective_exclude_paths,
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
        )
    _write_output(result, output)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_output(content: str, output: Optional[str]) -> None:
    if content and not content.endswith("\n"):
        content += "\n"
    if output:
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"Output written to {output}", err=True)
    else:
        click.echo(content)


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
