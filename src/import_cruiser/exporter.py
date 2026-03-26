"""Export a DependencyGraph to JSON or DOT (Graphviz) format."""
# pylint: disable=duplicate-code

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404
from pathlib import Path
from typing import Protocol, TypedDict, cast

from import_cruiser.graph import Dependency, DependencyGraph, Module, detect_cycles

JSONDict = dict[str, object]

DB_EXTERNAL_MODULES: set[str] = {
    "sqlalchemy",
    "sqlmodel",
    "alembic",
    "psycopg",
    "psycopg2",
    "asyncpg",
    "pg8000",
    "aiopg",
    "databases",
    "postgres",
}


class ViolationLike(Protocol):
    source: str
    target: str
    severity: str
    message: str
    rule_name: str

    def to_dict(self) -> dict[str, object]: ...


class Cluster(TypedDict):
    id: str
    label: str
    parent: str | None
    modules: list[Module]


def export_json(
    graph: DependencyGraph,
    violations: list[ViolationLike] | None = None,
    cycles: list[list[str]] | None = None,
) -> str:
    """Serialize *graph* (and optional analysis results) to a JSON string."""
    if violations is None:
        violations = []
    if cycles is None:
        cycles = detect_cycles(graph)

    data = cast(
        JSONDict,
        {
            "summary": {
                "modules": len(graph.modules),
                "dependencies": len(graph.dependencies),
                "cycles": len(cycles),
                "violations": len(violations),
            },
            "modules": [
                {
                    "name": m.name,
                    "path": m.path,
                    "imports": graph.dependencies_of(m.name),
                }
                for m in sorted(graph.modules, key=lambda m: m.name)
            ],
            "dependencies": [
                {"source": d.source, "target": d.target, "line": d.line}
                for d in sorted(graph.dependencies, key=lambda d: (d.source, d.target))
            ],
            "cycles": [cycle for cycle in cycles],
            "violations": [v.to_dict() for v in violations],
        },
    )
    return json.dumps(data, indent=2)


def export_dot(
    graph: DependencyGraph,
    graph_name: str = "import_cruiser",
    violations: list[ViolationLike] | None = None,
    rankdir: str = "LR",
    cluster_depth: int = 2,
    cluster_mode: str = "path",
    style: str = "depcruise",
    edge_mode: str = "node",
) -> str:
    """Return a DOT-format string representing the dependency graph."""
    if violations is None:
        violations = []
    cycles = detect_cycles(graph)
    cycle_edges = _edges_in_cycles(cycles)
    cycle_nodes = {name for cycle in cycles for name in cycle}
    violation_edges = _edges_from_violations(violations)

    graph_attrs, node_attrs, edge_attrs = _style_attrs(style, rankdir)
    depcruise = style == "depcruise"
    path_root = _common_root(graph.modules) if cluster_mode == "path" else None
    http_nodes = {
        module.name
        for module in graph.modules
        if _is_http_external_node(module.name, module.path)
    }
    external_anchor_parts = _external_anchor_parts(graph, path_root)
    node_id_map: dict[str, str] = {}
    lines = _init_dot_lines(
        graph_name=graph_name,
        graph_attrs=graph_attrs,
        node_attrs=node_attrs,
        edge_attrs=edge_attrs,
        depcruise=depcruise,
    )

    node_to_cluster: dict[str, str] = {}
    cluster_index: dict[str, list[str]] = {}
    if depcruise:
        _append_depcruise_nodes(
            lines=lines,
            modules=graph.modules,
            node_id_map=node_id_map,
            path_root=path_root,
            external_anchor_parts=external_anchor_parts,
        )
    else:
        node_to_cluster, cluster_index = _append_standard_nodes(
            lines=lines,
            modules=graph.modules,
            cycle_nodes=cycle_nodes,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            path_root=path_root,
        )

    if not depcruise and edge_mode == "cluster" and cluster_depth > 0:
        edge_mode = _append_cluster_edges(
            lines=lines,
            dependencies=graph.dependencies,
            node_to_cluster=node_to_cluster,
            cluster_index=cluster_index,
            edge_mode=edge_mode,
        )

    if edge_mode != "cluster" or depcruise:
        _append_dependency_edges(
            lines=lines,
            dependencies=graph.dependencies,
            node_id_map=node_id_map,
            violation_edges=violation_edges,
            cycle_edges=cycle_edges,
            depcruise=depcruise,
            http_nodes=http_nodes,
        )

    lines.append("}")
    return "\n".join(lines)


def _init_dot_lines(
    graph_name: str,
    graph_attrs: str,
    node_attrs: str,
    edge_attrs: str,
    depcruise: bool,
) -> list[str]:
    if depcruise:
        return [
            f'strict digraph "{graph_name}"{{',
            f"    {graph_attrs}",
            f"    node [{node_attrs}]",
            f"    edge [{edge_attrs}]",
            "",
        ]
    return [
        f'digraph "{graph_name}" {{',
        f"    graph [{graph_attrs}];",
        f"    node [{node_attrs}];",
        f"    edge [{edge_attrs}];",
        "",
    ]


def _append_depcruise_nodes(
    lines: list[str],
    modules: list[Module],
    node_id_map: dict[str, str],
    path_root: str | None,
    external_anchor_parts: dict[str, list[str]],
) -> None:
    for module in sorted(modules, key=lambda m: m.name):
        node_id = _depcruise_node_id(
            module,
            path_root,
            external_anchor_parts=external_anchor_parts,
        )
        node_id_map[module.name] = node_id
        lines.append(
            _depcruise_cluster_line(
                module,
                node_id,
                path_root,
                external_anchor_parts=external_anchor_parts,
            )
        )
    if modules:
        lines.append("")


def _append_standard_nodes(
    lines: list[str],
    modules: list[Module],
    cycle_nodes: set[str],
    cluster_depth: int,
    cluster_mode: str,
    style: str,
    path_root: str | None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    cluster_index: dict[str, list[str]] = {}
    node_to_cluster: dict[str, str] = {}
    root_modules: list[Module]

    if cluster_depth > 0:
        root_clusters, root_modules, flat_clusters = _build_clusters(
            modules, cluster_depth, cluster_mode
        )
        non_empty_clusters = _non_empty_clusters(flat_clusters)
        lines.extend(
            _render_cluster_tree(
                root_clusters,
                flat_clusters,
                cycle_nodes,
                indent="    ",
                mode=cluster_mode,
                allowed=non_empty_clusters,
                style=style,
            )
        )
        for module in modules:
            cluster_key = _node_cluster_key(
                module.name,
                module.path,
                cluster_mode,
                cluster_depth,
                path_root,
            )
            if cluster_key and cluster_key in non_empty_clusters:
                node_to_cluster[module.name] = cluster_key
                cluster_index.setdefault(cluster_key, []).append(module.name)
    else:
        root_modules = sorted(modules, key=lambda m: m.name)

    for module in root_modules:
        lines.extend(
            _dot_node_lines(
                module.name,
                module.path,
                module.name in cycle_nodes,
                indent="    ",
                label=_leaf_label(module, cluster_mode),
            )
        )

    if root_modules:
        lines.append("")

    return node_to_cluster, cluster_index


def _append_cluster_edges(
    lines: list[str],
    dependencies: list[Dependency],
    node_to_cluster: dict[str, str],
    cluster_index: dict[str, list[str]],
    edge_mode: str,
) -> str:
    cluster_edges: set[tuple[str, str]] = set()
    for dep in dependencies:
        src_cluster = node_to_cluster.get(dep.source)
        tgt_cluster = node_to_cluster.get(dep.target)
        if not src_cluster or not tgt_cluster or src_cluster == tgt_cluster:
            continue
        if _clusters_related(src_cluster, tgt_cluster):
            continue
        cluster_edges.add((src_cluster, tgt_cluster))

    if not cluster_edges:
        return "node"

    for src_cluster, tgt_cluster in sorted(cluster_edges):
        src_node = cluster_index[src_cluster][0]
        tgt_node = cluster_index[tgt_cluster][0]
        lines.append(
            f"    {_dot_id(src_node)} -> {_dot_id(tgt_node)} "
            f'[ltail="cluster_{_cluster_id(src_cluster)}", '
            f'lhead="cluster_{_cluster_id(tgt_cluster)}"];'
        )
    return edge_mode


def _append_dependency_edges(
    lines: list[str],
    dependencies: list[Dependency],
    node_id_map: dict[str, str],
    violation_edges: dict[tuple[str, str], ViolationLike],
    cycle_edges: set[tuple[str, str]],
    depcruise: bool,
    http_nodes: set[str],
) -> None:
    for dep in sorted(dependencies, key=lambda d: (d.source, d.target)):
        src = _dot_id(node_id_map.get(dep.source, dep.source))
        tgt = _dot_id(node_id_map.get(dep.target, dep.target))
        violation = violation_edges.get((dep.source, dep.target))
        if violation and not depcruise:
            color = _severity_color(violation.severity)
            lines.append(f'    {src} -> {tgt} [color="{color}", penwidth=2.2];')
        elif (dep.source, dep.target) in cycle_edges and not depcruise:
            lines.append(f'    {src} -> {tgt} [color="#C0392B", penwidth=1.6];')
        elif depcruise and dep.target in http_nodes:
            lines.append(
                f'    {src} -> {tgt} [color="#1D4ED8", style="dashed", '
                "penwidth=1.4, arrowsize=0.7]"
            )
        else:
            suffix = "" if depcruise else ";"
            lines.append(f"    {src} -> {tgt}{suffix}")


def export_svg(
    graph: DependencyGraph,
    graph_name: str = "import_cruiser",
    violations: list[ViolationLike] | None = None,
    engine: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 2,
    cluster_mode: str = "path",
    style: str = "depcruise",
    edge_mode: str = "node",
) -> str:
    dot = export_dot(
        graph,
        graph_name=graph_name,
        violations=violations,
        rankdir=rankdir,
        cluster_depth=cluster_depth,
        cluster_mode=cluster_mode,
        style=style,
        edge_mode=edge_mode,
    )
    svg = _render_with_edge_fallback(
        dot=dot,
        graph=graph,
        fmt="svg",
        engine=engine,
        graph_name=graph_name,
        violations=violations,
        rankdir=rankdir,
        cluster_depth=cluster_depth,
        cluster_mode=cluster_mode,
        style=style,
        edge_mode=edge_mode,
    )
    return _add_svg_padding(svg)


def export_html(
    graph: DependencyGraph,
    graph_name: str = "import_cruiser",
    violations: list[ViolationLike] | None = None,
    engine: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 2,
    cluster_mode: str = "path",
    style: str = "depcruise",
    edge_mode: str = "node",
) -> str:
    dot = export_dot(
        graph,
        graph_name=graph_name,
        violations=violations,
        rankdir=rankdir,
        cluster_depth=cluster_depth,
        cluster_mode=cluster_mode,
        style=style,
        edge_mode=edge_mode,
    )
    try:
        svg = _add_svg_padding(
            _render_with_edge_fallback(
                dot=dot,
                graph=graph,
                fmt="svg",
                engine=engine,
                graph_name=graph_name,
                violations=violations,
                rankdir=rankdir,
                cluster_depth=cluster_depth,
                cluster_mode=cluster_mode,
                style=style,
                edge_mode=edge_mode,
            )
        )
        body = _html_with_svg(svg, graph_name)
    except RuntimeError as exc:
        body = _html_with_fallback(dot, graph_name, str(exc))
    return body


def _render_with_edge_fallback(
    *,
    dot: str,
    graph: DependencyGraph,
    fmt: str,
    engine: str,
    graph_name: str,
    violations: list[ViolationLike] | None,
    rankdir: str,
    cluster_depth: int,
    cluster_mode: str,
    style: str,
    edge_mode: str,
) -> str:
    try:
        return _render_dot(dot, fmt, engine=engine)
    except RuntimeError as exc:
        if edge_mode != "cluster":
            raise
        cluster_safe_dot = export_dot(
            graph,
            graph_name=graph_name,
            violations=violations,
            rankdir=rankdir,
            cluster_depth=cluster_depth,
            cluster_mode=cluster_mode,
            style=style,
            edge_mode="node",
        )
        try:
            return _render_dot(cluster_safe_dot, fmt, engine=engine)
        except RuntimeError:
            raise exc


def _dot_id(name: str) -> str:
    """Convert a dotted module name to a valid DOT identifier."""
    return '"' + name.replace('"', '\\"') + '"'


def _depcruise_node_id(
    module: Module,
    root: str | None,
    external_anchor_parts: dict[str, list[str]] | None = None,
) -> str:
    if not module.path:
        anchor = (external_anchor_parts or {}).get(module.name, [])
        if anchor:
            return "/".join([*anchor, module.name])
        return module.name
    try:
        path = Path(module.path).resolve()
        if root:
            path = path.relative_to(root)
        return str(path).replace("\\", "/")
    except ValueError:
        return module.path.replace("\\", "/")


def _depcruise_cluster_line(
    module: Module,
    node_id: str,
    root: str | None,
    external_anchor_parts: dict[str, list[str]] | None = None,
) -> str:
    rel_path = node_id if module.path else module.name
    label = Path(module.path).name if module.path else module.name
    attrs = _depcruise_node_attrs(module, label, rel_path)
    parts: list[str] = []
    if module.path:
        try:
            path = Path(module.path).resolve()
            if root:
                path = path.relative_to(root)
            parts = list(path.parts[:-1])
        except ValueError:
            parts = list(Path(module.path).parts[:-1])
    elif external_anchor_parts:
        parts = external_anchor_parts.get(module.name, [])

    if not parts:
        return f"    {_dot_id(node_id)} [{attrs} ]"

    line = f'    subgraph "cluster_{parts[0]}" {{label="{parts[0]}" '
    prefix = parts[0]
    for part in parts[1:]:
        prefix = f"{prefix}/{part}"
        line += f'subgraph "cluster_{prefix}" {{label="{part}" '

    line += f"{_dot_id(node_id)} [{attrs} ] "
    line += "}" * len(parts)
    return line


def _depcruise_node_attrs(module: Module, label: str, rel_path: str) -> str:
    base = f'label=<{label}> tooltip="{label}" URL="{rel_path}"'
    if _is_database_external_node(module.name, module.path):
        return (
            f'{base} shape="cylinder" style="filled" '
            'fillcolor="#FFE7CC" color="#B45309" penwidth="1.4" '
            'fontcolor="#7C2D12"'
        )
    if _is_http_external_node(module.name, module.path):
        return (
            f'{base} shape="box" style="rounded,filled,dashed" '
            'fillcolor="#DBEAFE" color="#1D4ED8" penwidth="1.4" '
            'fontcolor="#1E3A8A"'
        )
    return base


def _dot_node_lines(
    name: str,
    path: str,
    in_cycle: bool,
    indent: str,
    label: str,
) -> list[str]:
    safe = _dot_id(name)
    attrs = [f'label="{label}"', f'tooltip="{path}"']
    if in_cycle:
        attrs.append('fillcolor="#FDECEA"')
        attrs.append('color="#C0392B"')
    return [f"{indent}{safe} [{', '.join(attrs)}];"]


def _edges_in_cycles(cycles: list[list[str]]) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for cycle in cycles:
        if len(cycle) < 2:
            continue
        for idx in range(len(cycle)):
            src = cycle[idx]
            tgt = cycle[(idx + 1) % len(cycle)]
            edges.add((src, tgt))
    return edges


def _build_clusters(
    modules: list[Module], depth: int, mode: str
) -> tuple[dict[str, Cluster], list[Module], dict[str, Cluster]]:
    clusters: dict[str, Cluster] = {}
    root_modules: list[Module] = []
    root_path = _common_root(modules) if mode == "path" else None

    for module in modules:
        parts = _cluster_parts(module, mode, root_path)
        if len(parts) <= 1 or depth <= 0:
            root_modules.append(module)
            continue

        max_depth = min(depth, len(parts) - (0 if mode == "path" else 1))
        parent_id = None
        for i in range(1, max_depth + 1):
            cid = ".".join(parts[:i])
            if cid not in clusters:
                clusters[cid] = {
                    "id": cid,
                    "label": parts[i - 1],
                    "parent": parent_id,
                    "modules": [],
                }
            parent_id = cid

        if parent_id is None:  # pragma: no cover
            continue
        clusters[parent_id]["modules"].append(module)

    root_clusters: dict[str, Cluster] = {}
    for cid, cluster in clusters.items():
        parent = cluster["parent"]
        if parent is None:
            root_clusters[cid] = cluster

    return root_clusters, sorted(root_modules, key=lambda m: m.name), clusters


def _render_cluster_tree(
    root_clusters: dict[str, Cluster],
    flat_clusters: dict[str, Cluster],
    cycle_nodes: set[str],
    indent: str,
    mode: str,
    allowed: set[str],
    style: str,
) -> list[str]:
    lines: list[str] = []

    def children_of(parent_id: str) -> list[Cluster]:
        return [c for c in flat_clusters.values() if c["parent"] == parent_id]

    def render_cluster(cluster: Cluster, level_indent: str) -> None:
        if cluster["id"] not in allowed:
            return
        cid = _cluster_id(cluster["id"])
        lines.append(f'{level_indent}subgraph "cluster_{cid}" {{')
        lines.append(f'{level_indent}    label="{cluster["label"]}";')
        if style != "depcruise":
            lines.append(f"{level_indent}    margin=6;")
            lines.append(f'{level_indent}    color="black";')
            lines.append(f"{level_indent}    penwidth=1.0;")
            lines.append(f'{level_indent}    style="rounded,bold";')
            lines.append(f'{level_indent}    fontname="Helvetica";')
            lines.append(f"{level_indent}    fontsize=9;")

        allowed_children = [
            child for child in children_of(cluster["id"]) if child["id"] in allowed
        ]
        if style != "depcruise" and not cluster["modules"] and allowed_children:
            anchor_id = _dot_id(f"__cluster_anchor_{cid}")
            lines.append(
                f"{level_indent}    {anchor_id} "
                '[label="", shape=point, width=0, height=0, style=invis];'
            )

        for child in sorted(allowed_children, key=lambda c: c["label"]):
            render_cluster(child, level_indent + "    ")

        for module in sorted(cluster["modules"], key=lambda m: m.name):
            lines.extend(
                _dot_node_lines(
                    module.name,
                    module.path,
                    module.name in cycle_nodes,
                    indent=level_indent + "    ",
                    label=_leaf_label(module, mode),
                )
            )

        lines.append(f"{level_indent}}}")

    for cluster in sorted(root_clusters.values(), key=lambda c: c["label"]):
        render_cluster(cluster, indent)
        lines.append("")

    return lines


def _cluster_id(group: str) -> str:
    return group.replace('"', "").replace(".", "_").replace("/", "_").replace("\\", "_")


def _non_empty_clusters(flat_clusters: dict[str, Cluster]) -> set[str]:
    children_map: dict[str, list[Cluster]] = {}
    for cluster in flat_clusters.values():
        parent = cluster["parent"]
        if parent is not None:
            children_map.setdefault(parent, []).append(cluster)

    cache: dict[str, bool] = {}

    def has_content(cluster: Cluster) -> bool:
        cid = cluster["id"]
        if cid in cache:
            return cache[cid]
        if cluster["modules"]:
            cache[cid] = True
            return True
        for child in children_map.get(cid, []):
            if has_content(child):
                cache[cid] = True
                return True
        cache[cid] = False
        return False

    return {cid for cid, cluster in flat_clusters.items() if has_content(cluster)}


def _clusters_related(a: str, b: str) -> bool:
    return a.startswith(b + ".") or b.startswith(a + ".")


def _leaf_label(module, mode: str) -> str:
    if mode == "path":
        return Path(module.path).name
    return module.name.split(".")[-1]


def _node_cluster_key(
    name: str,
    path: str,
    mode: str,
    depth: int,
    root_path: str | None,
) -> str:
    if depth <= 0:
        return ""
    if mode == "path":
        if not path or not root_path:
            return ""
        try:
            rel = Path(path).resolve().relative_to(root_path)
        except ValueError:
            return ""
        parts = list(rel.parts[:-1])
        if not parts:
            return ""
        return ".".join(parts[: min(depth, len(parts))])
    parts = name.split(".")
    if len(parts) <= 1:
        return ""
    return ".".join(parts[: min(depth, len(parts))])


def _cluster_parts(module: Module, mode: str, root_path: str | None) -> list[str]:
    if mode == "path":
        if not root_path:
            return [module.name]
        rel = Path(module.path).resolve().relative_to(root_path)
        return list(rel.parts[:-1])  # directories only
    return module.name.split(".")


def _common_root(modules: list[Module]) -> str | None:
    paths = [m.path for m in modules if m.path]
    if not paths:
        return None
    try:
        common = Path(os.path.commonpath(paths)).resolve()
        common_dir = common if common.is_dir() else common.parent
        if common_dir.name == "src":
            return str(common_dir.parent)
        if common_dir.parent.name == "src":
            return str(common_dir.parent.parent)
        return str(common_dir)
    except ValueError:
        return None


def _edges_from_violations(
    violations: list[ViolationLike],
) -> dict[tuple[str, str], ViolationLike]:
    return {(v.source, v.target): v for v in violations}


def _severity_color(severity: str) -> str:
    return {
        "error": "#C0392B",
        "warn": "#E67E22",
        "info": "#2980B9",
    }.get(severity, "#C0392B")


def _style_attrs(style: str, rankdir: str) -> tuple[str, str, str]:
    if style == "depcruise":
        graph_attrs = (
            f'rankdir="{rankdir}" splines="true" overlap="false" nodesep="0.16" '
            'ranksep="0.18" fontname="Helvetica-bold" fontsize="9" '
            'style="rounded,bold,filled" fillcolor="#ffffff" compound="true"'
        )
        node_attrs = (
            'shape="box" style="rounded, filled" height="0.2" color="black" '
            'fillcolor="#ffffcc" fontcolor="black" fontname="Helvetica" fontsize="9"'
        )
        edge_attrs = (
            'arrowhead="normal" arrowsize="0.6" penwidth="2.0" '
            'color="#0000001f" fontname="Helvetica" fontsize="9"'
        )
        return graph_attrs, node_attrs, edge_attrs

    if style == "archi":
        graph_attrs = (
            f"rankdir={rankdir}, splines=ortho, overlap=false, ranksep=0.55, "
            'nodesep=0.28, pack=true, packmode="array_u", newrank=true, '
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.18, margin=0.18'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#A0A0A0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    if style == "cruiser":
        graph_attrs = (
            f"rankdir={rankdir}, splines=curved, overlap=false, ranksep=0.5, "
            'nodesep=0.25, pack=true, packmode="clust", newrank=true, '
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.18, margin=0.18'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#B0B0B0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    if style == "navigator":
        graph_attrs = (
            f"rankdir={rankdir}, splines=curved, overlap=prism, ranksep=0.55, "
            'nodesep=0.32, pack=true, packmode="clust", newrank=true, '
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.18, margin=0.18'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#B0B0B0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    graph_attrs = (
        f"rankdir={rankdir}, splines=curved, overlap=false, nodesep=0.12, ranksep=0.12, "
        'fontname="Helvetica-bold", fontsize=9, style="rounded,bold,filled", '
        'fillcolor="#FFFFFF", compound=true, pack=true, packmode="array_u", '
        'newrank=true, ratio="compress", pad=0.18, margin=0.18'
    )
    node_attrs = (
        'shape=box, style="rounded,filled", height=0.2, color="black", '
        'fillcolor="#FFFFCC", fontcolor="black", fontname="Helvetica", fontsize=9'
    )
    edge_attrs = (
        'arrowhead="normal", arrowsize=0.6, penwidth=2.0, '
        'color="#0000001f", fontname="Helvetica", fontsize=9'
    )
    return graph_attrs, node_attrs, edge_attrs


def _is_database_external_node(name: str, path: str) -> bool:
    if path:
        return False
    return name.split(".", 1)[0] in DB_EXTERNAL_MODULES


def _is_http_external_node(name: str, path: str) -> bool:
    if path:
        return False
    if "/" in name or ":" in name:
        return False
    if "." not in name:
        return False
    root = name.split(".", 1)[0]
    return root not in DB_EXTERNAL_MODULES


def _external_anchor_parts(
    graph: DependencyGraph,
    path_root: str | None,
) -> dict[str, list[str]]:
    module_map = {module.name: module for module in graph.modules}
    source_parts_by_external: dict[str, list[list[str]]] = {}
    for dep in graph.dependencies:
        target_module = module_map.get(dep.target)
        source_module = module_map.get(dep.source)
        if target_module is None or source_module is None:
            continue
        if target_module.path:
            continue
        if not source_module.path:
            continue
        parts = _module_parent_parts(source_module.path, path_root)
        if not parts:
            continue
        source_parts_by_external.setdefault(target_module.name, []).append(parts)

    return {
        external: _common_prefix(parts_list)
        for external, parts_list in source_parts_by_external.items()
    }


def _module_parent_parts(path: str, path_root: str | None) -> list[str]:
    try:
        parent = Path(path).resolve().parent
        if path_root:
            parent = parent.relative_to(Path(path_root).resolve())
        return list(parent.parts)
    except ValueError:
        return list(Path(path).resolve().parent.parts)


def _common_prefix(parts_list: list[list[str]]) -> list[str]:
    if not parts_list:
        return []
    prefix = parts_list[0][:]
    for parts in parts_list[1:]:
        idx = 0
        max_len = min(len(prefix), len(parts))
        while idx < max_len and prefix[idx] == parts[idx]:
            idx += 1
        prefix = prefix[:idx]
        if not prefix:
            break
    return prefix


def _render_dot(dot: str, fmt: str, engine: str = "dot") -> str:
    try:
        result = subprocess.run(  # nosec B603
            [engine, f"-T{fmt}"],
            input=dot,
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Graphviz 'dot' is not installed.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or "Graphviz rendering failed.") from exc
    return result.stdout


def _add_svg_padding(svg: str, padding: int = 8) -> str:
    viewbox_match = re.search(
        r'viewBox="([\-0-9.eE]+)\s+([\-0-9.eE]+)\s+([\-0-9.eE]+)\s+([\-0-9.eE]+)"',
        svg,
    )
    if not viewbox_match:
        return svg

    min_x = float(viewbox_match.group(1))
    min_y = float(viewbox_match.group(2))
    width = float(viewbox_match.group(3))
    height = float(viewbox_match.group(4))

    new_viewbox = (
        f'viewBox="{min_x - padding:.2f} {min_y - padding:.2f} '
        f'{width + 2 * padding:.2f} {height + 2 * padding:.2f}"'
    )
    svg = svg[: viewbox_match.start()] + new_viewbox + svg[viewbox_match.end() :]

    def _bump_dimension(attr: str, text: str) -> str:
        match = re.search(rf'{attr}="([0-9.]+)([a-z%]*)"', text)
        if not match:
            return text
        old_value = float(match.group(1))
        unit = match.group(2)
        new_attr = f'{attr}="{old_value + 2 * padding:.0f}{unit}"'
        return text[: match.start()] + new_attr + text[match.end() :]

    svg = _bump_dimension("width", svg)
    svg = _bump_dimension("height", svg)
    return svg


def _html_with_svg(svg: str, title: str) -> str:
    display_title = _display_graph_title(title)
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{display_title}</title>
    <style>
        :root {{
            color-scheme: only light;
            --header-h: 44px;
            --toolbar-h: 48px;
            --footer-h: 28px;
        }}
        body {{
            margin: 0;
            font-family: Helvetica, Arial, sans-serif;
            background: #f4f4f4;
            color: #1e1e1e;
        }}
        header {{
            padding: 12px 16px;
            background: #1f2937;
            color: #f9fafb;
            font-size: 14px;
            letter-spacing: 0.02em;
            height: var(--header-h);
            box-sizing: border-box;
        }}
        .toolbar {{
            height: var(--toolbar-h);
            box-sizing: border-box;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-bottom: 1px solid #e5e7eb;
            background: #ffffff;
            overflow: hidden;
        }}
        .toolbar .spacer {{
            flex: 1;
        }}
        .toolbar input[type="search"] {{
            height: 30px;
            min-width: 280px;
            padding: 0 10px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            font-size: 12px;
        }}
        .toolbar button {{
            height: 30px;
            padding: 0 10px;
            border: 1px solid #d1d5db;
            border-radius: 6px;
            background: #ffffff;
            font-size: 12px;
            cursor: pointer;
            white-space: nowrap;
        }}
        .toolbar button:hover {{
            background: #f9fafb;
        }}
        .toolbar button.toggled {{
            border-color: #7f1d1d;
            background: #fee2e2;
            color: #7f1d1d;
        }}
        .toolbar .badge {{
            font-size: 11px;
            color: #374151;
            border: 1px solid #d1d5db;
            border-radius: 999px;
            padding: 3px 8px;
            background: #f9fafb;
            white-space: nowrap;
            max-width: 34vw;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        #inspector .node-ref {{
            border: 0;
            background: transparent;
            padding: 0;
            margin: 0;
            color: #1f2937;
            text-decoration: underline;
            cursor: pointer;
            font: inherit;
            text-align: left;
        }}
        #inspector .node-ref:hover {{
            color: #1d4ed8;
        }}
        .canvas {{
            width: 100vw;
            height: calc(100vh - var(--header-h) - var(--toolbar-h) - var(--footer-h));
            overflow: hidden;
            position: relative;
            background: radial-gradient(circle at 20% 20%, #ffffff 0%, #f4f4f4 45%, #e8e8e8 100%);
        }}
        .viewport {{
            transform-origin: 0 0;
            cursor: grab;
            transition: transform 0.03s linear;
        }}
        .viewport:active {{
            cursor: grabbing;
        }}
        #inspector {{
            position: absolute;
            top: 12px;
            right: 12px;
            width: min(360px, 40vw);
            max-height: calc(100vh - var(--header-h) - var(--toolbar-h) - 40px);
            overflow: auto;
            background: rgba(255, 255, 255, 0.95);
            border: 1px solid #d1d5db;
            border-radius: 8px;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.12);
            padding: 10px 12px;
            font-size: 12px;
            line-height: 1.4;
            z-index: 10;
        }}
        #inspector h4 {{
            margin: 2px 0 8px;
            font-size: 12px;
        }}
        #inspector .muted {{
            color: #6b7280;
        }}
        #inspector ul {{
            margin: 4px 0 8px 16px;
            padding: 0;
        }}
        .footer {{
            height: var(--footer-h);
            box-sizing: border-box;
            border-top: 1px solid #e5e7eb;
            background: #ffffff;
            padding: 6px 12px;
            font-size: 11px;
            color: #4b5563;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        svg.focus-mode g.node,
        svg.focus-mode g.edge {{
            opacity: 0.14;
            transition: opacity 0.06s linear;
        }}
        svg.focus-mode g.node.active,
        svg.focus-mode g.edge.active {{
            opacity: 1 !important;
        }}
        g.node.search-match > polygon,
        g.node.search-match > ellipse,
        g.node.search-match > rect {{
            stroke: #059669;
            stroke-width: 2.2px;
        }}
        g.node.active > polygon,
        g.node.active > ellipse,
        g.node.active > rect {{
            stroke: #2563eb;
            stroke-width: 2.2px;
        }}
        g.edge.active > path {{
            stroke: #2563eb;
            stroke-width: 3.6px;
            opacity: 1;
        }}
        g.edge.active > polygon {{
            fill: #2563eb;
            stroke: #2563eb;
            opacity: 1;
        }}
        g.edge:not(.active) > path {{
            stroke: #D9D9D9 !important;
            stroke-width: 1.4px !important;
            stroke-opacity: 1 !important;
            opacity: 1 !important;
        }}
        g.edge:not(.active) > polygon {{
            fill: #D9D9D9 !important;
            stroke: #D9D9D9 !important;
            fill-opacity: 1 !important;
            stroke-opacity: 1 !important;
            opacity: 1 !important;
        }}
        g.edge.active {{
            opacity: 1 !important;
        }}
        g.edge.active > path {{
            stroke: #2563eb !important;
            stroke-width: 4.8px !important;
            stroke-opacity: 1 !important;
            opacity: 1 !important;
        }}
        g.edge.active > polygon {{
            fill: #2563eb !important;
            stroke: #2563eb !important;
            fill-opacity: 1 !important;
            stroke-opacity: 1 !important;
            opacity: 1 !important;
        }}
        g.cluster > path,
        g.cluster > polygon {{
            fill: none !important;
        }}
        g.cluster.cluster-dim > path,
        g.cluster.cluster-dim > polygon {{
            stroke: transparent !important;
        }}
        g.cluster.cluster-dim > text {{
            fill: transparent !important;
            stroke: transparent !important;
        }}
        g.cluster.cluster-active > path,
        g.cluster.cluster-active > polygon {{
            stroke: #000000 !important;
        }}
        svg.review-mode g.node.arch-hot > polygon,
        svg.review-mode g.node.arch-hot > ellipse,
        svg.review-mode g.node.arch-hot > rect {{
            fill: #FECACA !important;
            stroke: #B91C1C !important;
            stroke-width: 2.8px !important;
        }}
        svg.review-mode g.node.arch-warm > polygon,
        svg.review-mode g.node.arch-warm > ellipse,
        svg.review-mode g.node.arch-warm > rect {{
            fill: #FEF3C7 !important;
            stroke: #B45309 !important;
            stroke-width: 2.2px !important;
        }}
        svg.review-mode g.edge.arch-hot > path {{
            stroke: #DC2626 !important;
            stroke-width: 3.2px !important;
        }}
        svg.review-mode g.edge.arch-hot > polygon {{
            fill: #DC2626 !important;
            stroke: #DC2626 !important;
        }}
        svg.review-mode g.cluster.arch-hot > path,
        svg.review-mode g.cluster.arch-hot > polygon {{
            fill: #FECACA66 !important;
            stroke: #B91C1C !important;
            stroke-width: 2px !important;
        }}
        svg.review-mode g.cluster.arch-warm > path,
        svg.review-mode g.cluster.arch-warm > polygon {{
            fill: #FEF3C74D !important;
            stroke: #B45309 !important;
        }}
    </style>
</head>
<body>
    <header>{display_title}</header>
    <div class="toolbar">
        <button id="btn-fit" title="Fit graph to viewport (F)">Fit</button>
        <button id="btn-reset-zoom" title="Reset zoom to 100% (0)">100%</button>
        <button id="btn-zoom-in" title="Zoom in (+)">+</button>
        <button id="btn-zoom-out" title="Zoom out (-)">-</button>
        <button id="btn-chain" title="Show dependency chain (C)">Chain</button>
        <button id="btn-arch-review" title="Highlight likely bottlenecks (A)">Architecture review</button>
        <button id="btn-clear-focus" title="Clear current pin/focus (Esc)">Clear focus</button>
        <input id="search" type="search" placeholder="Search module/path… (/)">
        <span class="badge" id="search-count">0 matches</span>
        <span class="spacer"></span>
        <span class="badge" id="repo-badge">Repos: detecting…</span>
    </div>
    <div class="canvas" id="canvas">
        <div class="viewport" id="viewport">{svg}</div>
        <aside id="inspector">
            <h4>Context</h4>
            <div class="muted">Click a node or edge to pin details.</div>
        </aside>
    </div>
    <div class="footer" id="footer">Ready</div>
    <script>
        const canvas = document.getElementById('canvas');
        const viewport = document.getElementById('viewport');
        const svg = viewport.querySelector('svg');
        const inspector = document.getElementById('inspector');
        const footer = document.getElementById('footer');
        const searchInput = document.getElementById('search');
        const searchCount = document.getElementById('search-count');
        const repoBadge = document.getElementById('repo-badge');
        const initialViewBox = svg?.getAttribute('viewBox') || '';
        let scale = 1;
        let originX = 0;
        let originY = 0;
        let isDragging = false;
        let startX = 0;
        let startY = 0;
        let pinned = null;
        let activeNodeName = null;
        let searchResults = [];
        let searchIndex = -1;

        const esc = (text) => String(text)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');

        const titleOf = (group) =>
            group?.querySelector('title')?.textContent?.trim() || '';
        const nodeGroups = [...svg.querySelectorAll('g.node')];
        const edgeGroups = [...svg.querySelectorAll('g.edge')];
        const clusterGroups = [...svg.querySelectorAll('g.cluster')];
        const clusterByKey = new Map();
        const nodeByName = new Map();
        const outgoing = new Map();
        const incoming = new Map();
        const edgesByNode = new Map();
        const edgesByPair = new Map();
        const nodeClusterChain = new Map();
        const nodeToDeepestClusterKey = new Map();
        const nodeClusterKeysByDepth = new Map();
        const clusterNodeNames = new Map();
        const summaryClusterMembers = new Map();
        const repos = new Set();
        const packageRoots = new Set();
        const activeNodes = new Set();
        const activeEdges = new Set();
        let hoverFrame = null;
        let inspectorHoverName = null;
        let lastFocusedNodeName = null;
        let lastClickNodeName = null;
        let lastClickAtMs = 0;
        const DOUBLE_CLICK_MS = 320;
        let reviewMode = false;
        let reviewSummary = null;

        clusterGroups.forEach((cluster) => {{
            const raw = titleOf(cluster);
            const key = raw.startsWith('cluster_') ? raw.slice(8) : raw;
            cluster.dataset.clusterKey = key;
            clusterByKey.set(key, cluster);
            clusterNodeNames.set(key, []);
        }});

        nodeGroups.forEach((node) => {{
            const name = titleOf(node);
            node.dataset.name = name;
            nodeByName.set(name, node);
            outgoing.set(name, []);
            incoming.set(name, []);
            edgesByNode.set(name, []);
            const topPackage = (name.split('.')[0] || '').trim();
            if (topPackage) packageRoots.add(topPackage);
            const path =
                node.querySelector('a')?.getAttribute('xlink:title') ||
                node.querySelector('a')?.getAttribute('title') ||
                '';
            node.dataset.path = path;
            if (path.includes('/riskstudio-worker/')) repos.add('riskstudio-worker');
            if (path.includes('/riskstudio-sdk/')) repos.add('riskstudio-sdk');

            const parts = name.split('/').filter(Boolean);
            const clusterChain = [];
            let prefix = '';
            for (const part of parts.slice(0, -1)) {{
                prefix = prefix ? `${{prefix}}/${{part}}` : part;
                const cluster = clusterByKey.get(prefix);
                if (cluster) clusterChain.push(cluster);
            }}
            nodeClusterChain.set(name, clusterChain);
            const deepest = clusterChain.length
                ? clusterChain[clusterChain.length - 1].dataset.clusterKey || ''
                : '';
            nodeToDeepestClusterKey.set(name, deepest);
            if (deepest) (clusterNodeNames.get(deepest) || []).push(name);
            const depthKeys = clusterChain.map((cluster) => cluster.dataset.clusterKey || '');
            nodeClusterKeysByDepth.set(name, depthKeys);
            depthKeys.forEach((summaryKey) => {{
                if (!summaryKey) return;
                const members = summaryClusterMembers.get(summaryKey) || [];
                members.push(name);
                summaryClusterMembers.set(summaryKey, members);
            }});
        }});

        if (repos.size) {{
            repoBadge.textContent = `Repos: ${{[...repos].sort().join(' + ')}}`;
        }} else if (packageRoots.size) {{
            repoBadge.textContent = `Packages: ${{packageRoots.size}}`;
        }} else {{
            repoBadge.textContent = 'Repos: unknown';
        }}

        edgeGroups.forEach((edge) => {{
            const raw = titleOf(edge);
            const [src, tgt] = raw.split('->').map((s) => s.trim());
            edge.dataset.src = src || '';
            edge.dataset.tgt = tgt || '';
            if (src && tgt) {{
                outgoing.get(src)?.push(tgt);
                incoming.get(tgt)?.push(src);
                edgesByNode.get(src)?.push(edge);
                edgesByNode.get(tgt)?.push(edge);
                const key = `${{src}}\u0000${{tgt}}`;
                const existing = edgesByPair.get(key) || [];
                existing.push(edge);
                edgesByPair.set(key, existing);
            }}
        }});

        const _clusterBox = (clusterKey) => {{
            const cluster = clusterByKey.get(clusterKey);
            if (!cluster) return null;
            const shape = cluster.querySelector('path, polygon');
            const box = shape ? shape.getBBox() : cluster.getBBox();
            if (box.width > 0 && box.height > 0) return box;
            const members = summaryClusterMembers.get(clusterKey) || [];
            if (!members.length) return null;
            const node = nodeByName.get(members[0]);
            if (!node) return null;
            return node.getBBox();
        }};

        const _clusterCenter = (clusterKey) => {{
            const box = _clusterBox(clusterKey);
            if (!box) return null;
            return _boxCenter(box);
        }};

        const _boxCenter = (box) => ({{
            x: box.x + box.width / 2,
            y: box.y + box.height / 2,
        }});

        const _boxBorderPoint = (box, toward) => {{
            const center = _boxCenter(box);
            let dx = toward.x - center.x;
            let dy = toward.y - center.y;
            if (Math.abs(dx) < 0.0001 && Math.abs(dy) < 0.0001) {{
                dx = 1;
                dy = 0;
            }}
            const halfW = Math.max(1, box.width / 2);
            const halfH = Math.max(1, box.height / 2);
            const tx = halfW / Math.max(0.0001, Math.abs(dx));
            const ty = halfH / Math.max(0.0001, Math.abs(dy));
            const t = Math.min(tx, ty);
            return {{
                x: center.x + dx * t,
                y: center.y + dy * t,
            }};
        }};

        const _percentileThreshold = (values, percentile, minimum) => {{
            if (!values.length) return minimum;
            const sorted = [...values].sort((a, b) => a - b);
            const index = Math.max(0, Math.min(sorted.length - 1, Math.floor(sorted.length * percentile)));
            return Math.max(minimum, sorted[index]);
        }};

        const _buildArchitectureReview = () => {{
            const nodeStats = [...nodeByName.keys()].map((name) => {{
                const inDegree = (incoming.get(name) || []).length;
                const outDegree = (outgoing.get(name) || []).length;
                const score = inDegree + outDegree + Math.sqrt(inDegree * outDegree);
                return {{ name, inDegree, outDegree, score }};
            }});
            const nodeScores = nodeStats.map((item) => item.score);
            const hotNodeThreshold = _percentileThreshold(nodeScores, 0.9, 4);
            const warmNodeThreshold = _percentileThreshold(nodeScores, 0.75, 2.5);

            nodeStats.forEach((item) => {{
                const node = nodeByName.get(item.name);
                if (!node) return;
                if (item.score >= hotNodeThreshold) {{
                    node.classList.add('arch-hot');
                }} else if (item.score >= warmNodeThreshold) {{
                    node.classList.add('arch-warm');
                }}
            }});

            const edgeStats = edgeGroups.map((edge) => {{
                const src = edge.dataset.src || '';
                const tgt = edge.dataset.tgt || '';
                const score = (outgoing.get(src) || []).length + (incoming.get(tgt) || []).length;
                return {{ edge, src, tgt, score }};
            }});
            const edgeScores = edgeStats.map((item) => item.score);
            const hotEdgeThreshold = _percentileThreshold(edgeScores, 0.9, 4);
            edgeStats.forEach((item) => {{
                if (item.score >= hotEdgeThreshold) item.edge.classList.add('arch-hot');
            }});

            const clusterScore = new Map();
            clusterGroups.forEach((cluster) => clusterScore.set(cluster, 0));
            nodeStats.forEach((item) => {{
                const chain = nodeClusterChain.get(item.name) || [];
                chain.forEach((cluster) => {{
                    clusterScore.set(cluster, (clusterScore.get(cluster) || 0) + item.score);
                }});
            }});
            const clusterValues = [...clusterScore.values()];
            const hotClusterThreshold = _percentileThreshold(clusterValues, 0.9, 6);
            const warmClusterThreshold = _percentileThreshold(clusterValues, 0.75, 3);
            clusterScore.forEach((score, cluster) => {{
                if (score >= hotClusterThreshold) {{
                    cluster.classList.add('arch-hot');
                }} else if (score >= warmClusterThreshold) {{
                    cluster.classList.add('arch-warm');
                }}
            }});

            const topNodes = [...nodeStats]
                .sort((a, b) => b.score - a.score)
                .slice(0, 6)
                .map((item) => item.name);
            const topEdges = [...edgeStats]
                .sort((a, b) => b.score - a.score)
                .slice(0, 6)
                .map((item) => `${{item.src}} -> ${{item.tgt}}`);
            const topClusters = [...clusterScore.entries()]
                .sort((a, b) => b[1] - a[1])
                .slice(0, 4)
                .map(([cluster]) => cluster.dataset.clusterKey || titleOf(cluster));
            return {{ topNodes, topEdges, topClusters, hotNodeThreshold, hotEdgeThreshold }};
        }};

        reviewSummary = _buildArchitectureReview();

        svg.querySelectorAll('a').forEach((link) => {{
            link.addEventListener('click', (event) => event.preventDefault());
        }});

        const applyTransform = () => {{
            viewport.style.transform =
                `translate(${{originX}}px, ${{originY}}px) scale(${{scale}})`;
            footer.textContent =
                `Nodes: ${{nodeGroups.length}} · ` +
                `Edges: ${{edgeGroups.length}} · ` +
                `Zoom: ${{Math.round(scale * 100)}}%`;
        }};

        const fitToView = () => {{
            if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
            const vb = svg.viewBox.baseVal;
            if (!vb.width || !vb.height) return;
            const scaleX = canvas.clientWidth / vb.width;
            const scaleY = canvas.clientHeight / vb.height;
            scale = Math.min(scaleX, scaleY) * 0.84;
            originX = (canvas.clientWidth - vb.width * scale) / 2;
            originY = (canvas.clientHeight - vb.height * scale) / 2;
            applyTransform();
        }};

        const _syncClassSet = (previous, next, className) => {{
            previous.forEach((element) => {{
                if (!next.has(element)) element.classList.remove(className);
            }});
            next.forEach((element) => {{
                if (!previous.has(element)) element.classList.add(className);
            }});
            previous.clear();
            next.forEach((element) => previous.add(element));
        }};

        const _applyFocus = (nextNodes, nextEdges) => {{
            const hasFocus = nextNodes.size > 0 || nextEdges.size > 0;
            svg.classList.toggle('focus-mode', hasFocus);
            _syncClassSet(activeNodes, nextNodes, 'active');
            _syncClassSet(activeEdges, nextEdges, 'active');
        }};

        const _clearFocusClasses = () => {{
            svg.classList.remove('focus-mode');
            _applyFocus(new Set(), new Set());
        }};

        const _scheduleHover = (callback) => {{
            if (hoverFrame !== null) cancelAnimationFrame(hoverFrame);
            hoverFrame = requestAnimationFrame(() => {{
                hoverFrame = null;
                callback();
            }});
        }};

        const _nodeRefList = (items) =>
            `<ul>${{items.map((item) =>
                `<li><button type="button" class="node-ref" data-node-ref="${{esc(item)}}">${{esc(item)}}</button></li>`
            ).join('')}}</ul>`;

        const renderArchitectureReview = () => {{
            if (!reviewSummary) return;
            const nodeList = reviewSummary.topNodes.length
                ? _nodeRefList(reviewSummary.topNodes)
                : '<div class="muted">none</div>';
            const edgeList = reviewSummary.topEdges.length
                ? `<ul>${{reviewSummary.topEdges.map((edge) => `<li>${{esc(edge)}}</li>`).join('')}}</ul>`
                : '<div class="muted">none</div>';
            const clusterList = reviewSummary.topClusters.length
                ? `<ul>${{reviewSummary.topClusters.map((cluster) => `<li>${{esc(cluster)}}</li>`).join('')}}</ul>`
                : '<div class="muted">none</div>';
            inspector.innerHTML = `
                <h4>Architecture review</h4>
                <div class="muted">Heuristics: node centrality and edge fan-in/fan-out hotspots.</div>
                <div><strong>Hot modules</strong> (red)</div>
                ${{nodeList}}
                <div><strong>Hot dependencies</strong> (red edges)</div>
                ${{edgeList}}
                <div><strong>Hot clusters</strong> (cluster tint)</div>
                ${{clusterList}}
                <div><strong>Improvement ideas</strong></div>
                <ul>
                    <li>Split top hot modules by ownership boundary to reduce coupling.</li>
                    <li>Replace high-traffic direct dependencies with ports/adapters or events.</li>
                    <li>Move heavy integration code from core clusters into edge adapters.</li>
                </ul>
            `;
        }};

        const clearFocus = (force = false) => {{
            if (pinned && !force) return;
            _clearFocusClasses();
            clusterGroups.forEach((c) => c.classList.remove('cluster-dim', 'cluster-active'));
            activeNodeName = null;
            inspectorHoverName = null;
            if (reviewMode) {{
                renderArchitectureReview();
                return;
            }}
            inspector.innerHTML =
                '<h4>Context</h4>' +
                '<div class="muted">Click a node or edge to pin details.</div>';
        }};

        const focusNodeClusters = (name) => {{
            const activeClusters = new Set(nodeClusterChain.get(name) || []);
            if (!activeClusters.size) {{
                clusterGroups.forEach((cluster) => {{
                    cluster.classList.remove('cluster-dim', 'cluster-active');
                }});
                return;
            }}
            clusterGroups.forEach((cluster) => {{
                cluster.classList.add('cluster-dim');
                cluster.classList.remove('cluster-active');
            }});
            activeClusters.forEach((cluster) => {{
                cluster.classList.remove('cluster-dim');
                cluster.classList.add('cluster-active');
            }});
        }};

        const focusNodeClustersForNames = (names) => {{
            const activeClusters = new Set();
            names.forEach((name) => {{
                (nodeClusterChain.get(name) || []).forEach((cluster) => {{
                    activeClusters.add(cluster);
                }});
            }});
            if (!activeClusters.size) {{
                clusterGroups.forEach((cluster) => {{
                    cluster.classList.remove('cluster-dim', 'cluster-active');
                }});
                return;
            }}
            clusterGroups.forEach((cluster) => {{
                cluster.classList.add('cluster-dim');
                cluster.classList.remove('cluster-active');
            }});
            activeClusters.forEach((cluster) => {{
                cluster.classList.remove('cluster-dim');
                cluster.classList.add('cluster-active');
            }});
        }};

        const _collectNodeElements = (names) => {{
            const result = new Set();
            names.forEach((name) => {{
                const node = nodeByName.get(name);
                if (node) result.add(node);
            }});
            return result;
        }};

        const focusNode = (name, isPinned = false, updateInspector = true) => {{
            activeNodeName = name;
            lastFocusedNodeName = name;
            const out = outgoing.get(name) || [];
            const inc = incoming.get(name) || [];
            const neighbors = new Set([name, ...out, ...inc]);
            const nextNodes = _collectNodeElements(neighbors);
            const nextEdges = new Set(edgesByNode.get(name) || []);
            _applyFocus(nextNodes, nextEdges);
            focusNodeClusters(name);

            if (!updateInspector) return;

            const outList = out.length
                ? _nodeRefList(out)
                : '<div class="muted">none</div>';
            const inList = inc.length
                ? _nodeRefList(inc)
                : '<div class="muted">none</div>';
            const pinBadge = isPinned
                ? '<div class="muted"><strong>Pinned</strong> · click empty space or press Esc to release</div>'
                : '';

            inspector.innerHTML = `
                <h4>${{esc(name)}}</h4>
                ${{pinBadge}}
                <div><strong>Outgoing</strong> (${{out.length}})</div>
                ${{outList}}
                <div><strong>Incoming</strong> (${{inc.length}})</div>
                ${{inList}}
            `;
        }};

        const _collectTransitive = (seed, adjacency) => {{
            const visited = new Set([seed]);
            const queue = [seed];
            while (queue.length) {{
                const node = queue.shift();
                const next = adjacency.get(node) || [];
                next.forEach((candidate) => {{
                    if (visited.has(candidate)) return;
                    visited.add(candidate);
                    queue.push(candidate);
                }});
            }}
            return visited;
        }};

        const focusNodeTransitive = (name, isPinned = false, updateInspector = true) => {{
            activeNodeName = name;
            lastFocusedNodeName = name;
            const descendants = _collectTransitive(name, outgoing);
            const ancestors = _collectTransitive(name, incoming);
            const activeNames = new Set([...descendants, ...ancestors]);
            const nextNodes = _collectNodeElements(activeNames);
            const nextEdges = new Set();
            edgeGroups.forEach((edge) => {{
                const src = edge.dataset.src;
                const tgt = edge.dataset.tgt;
                if (activeNames.has(src) && activeNames.has(tgt)) {{
                    nextEdges.add(edge);
                }}
            }});
            _applyFocus(nextNodes, nextEdges);
            focusNodeClustersForNames(activeNames);

            if (!updateInspector) return;
            const pinBadge = isPinned
                ? '<div class="muted"><strong>Pinned</strong> · transitief (parents + children, recursief)</div>'
                : '';
            inspector.innerHTML = `
                <h4>${{esc(name)}}</h4>
                ${{pinBadge}}
                <div><strong>Transitive context</strong></div>
                <div>Nodes: ${{activeNames.size}}</div>
                <div>Ancestors: ${{Math.max(0, ancestors.size - 1)}}</div>
                <div>Descendants: ${{Math.max(0, descendants.size - 1)}}</div>
            `;
        }};

        const restorePinnedView = () => {{
            if (!pinned) {{
                clearFocus(true);
                return;
            }}
            if (pinned.kind === 'node') {{
                focusNode(pinned.name, true);
                return;
            }}
            if (pinned.kind === 'node-transitive') {{
                focusNodeTransitive(pinned.name, true);
                return;
            }}
            focusEdge(pinned.src, pinned.tgt, true);
        }};

        const focusEdge = (src, tgt, isPinned = false) => {{
            activeNodeName = null;
            const nextNodes = _collectNodeElements(new Set([src, tgt]));
            const key = `${{src}}\u0000${{tgt}}`;
            const nextEdges = new Set(edgesByPair.get(key) || []);
            _applyFocus(nextNodes, nextEdges);
            clusterGroups.forEach((c) => c.classList.remove('cluster-dim', 'cluster-active'));

            const pinBadge = isPinned
                ? '<div class="muted"><strong>Pinned</strong> · click empty space or press Esc to release</div>'
                : '';

            inspector.innerHTML = `
                <h4>Dependency</h4>
                ${{pinBadge}}
                <div><strong>From:</strong> ${{esc(src)}}</div>
                <div><strong>To:</strong> ${{esc(tgt)}}</div>
            `;
        }};

        const triggerDependencyChain = () => {{
            const chainName = pinned?.kind === 'node' || pinned?.kind === 'node-transitive'
                ? pinned.name
                : (activeNodeName || lastFocusedNodeName);
            if (!chainName) return;
            pinned = {{ kind: 'node-transitive', name: chainName }};
            focusNodeTransitive(chainName, true);
        }};

        const setArchitectureReviewMode = (enabled) => {{
            reviewMode = enabled;
            svg.classList.toggle('review-mode', reviewMode);
            const reviewButton = document.getElementById('btn-arch-review');
            reviewButton.classList.toggle('toggled', reviewMode);
            if (reviewMode) {{
                renderArchitectureReview();
                return;
            }}
            restorePinnedView();
        }};

        const clearSearchStyles = () => {{
            nodeGroups.forEach((n) => n.classList.remove('search-match'));
        }};

        const runSearch = (query) => {{
            const q = query.trim().toLowerCase();
            clearSearchStyles();
            searchResults = [];
            searchIndex = -1;
            if (!q) {{
                searchCount.textContent = '0 matches';
                return;
            }}

            for (const node of nodeGroups) {{
                const name = (node.dataset.name || '').toLowerCase();
                const path = (node.dataset.path || '').toLowerCase();
                if (name.includes(q) || path.includes(q)) {{
                    node.classList.add('search-match');
                    searchResults.push(node);
                }}
            }}

            searchCount.textContent =
                `${{searchResults.length}} match${{searchResults.length === 1 ? '' : 'es'}}`;
            if (searchResults.length > 0) {{
                searchIndex = 0;
                const name = searchResults[0].dataset.name;
                focusNode(name, false);
            }}
        }};

        const stepSearch = () => {{
            if (!searchResults.length) return;
            searchIndex = (searchIndex + 1) % searchResults.length;
            const node = searchResults[searchIndex];
            const name = node.dataset.name;
            focusNode(name, false);
        }};

        nodeGroups.forEach((node) => {{
            const name = node.dataset.name;
            node.addEventListener('mouseenter', () => {{
                if (pinned) return;
                _scheduleHover(() => focusNode(name));
            }});
            node.addEventListener('mouseleave', () => _scheduleHover(() => clearFocus()));
            node.addEventListener('click', (event) => {{
                event.preventDefault();
                event.stopPropagation();
                const nowMs = Date.now();
                const isRapidSecondClick =
                    lastClickNodeName === name && (nowMs - lastClickAtMs) <= DOUBLE_CLICK_MS;
                lastClickNodeName = name;
                lastClickAtMs = nowMs;
                if (isRapidSecondClick) {{
                    pinned = {{ kind: 'node-transitive', name }};
                    focusNodeTransitive(name, true);
                    return;
                }}
                pinned = {{ kind: 'node', name }};
                focusNode(name, true);
            }});
            node.addEventListener('dblclick', (event) => {{
                event.preventDefault();
                event.stopPropagation();
                pinned = {{ kind: 'node-transitive', name }};
                focusNodeTransitive(name, true);
            }});
        }});

        edgeGroups.forEach((edge) => {{
            const src = edge.dataset.src;
            const tgt = edge.dataset.tgt;
            if (!src || !tgt) return;
            edge.addEventListener('mouseenter', () => {{
                if (pinned) return;
                _scheduleHover(() => focusEdge(src, tgt));
            }});
            edge.addEventListener('mouseleave', () => _scheduleHover(() => clearFocus()));
            edge.addEventListener('click', (event) => {{
                event.preventDefault();
                event.stopPropagation();
                pinned = {{ kind: 'edge', src, tgt }};
                focusEdge(src, tgt, true);
            }});
        }});

        inspector.addEventListener('click', (event) => {{
            const target = event.target instanceof Element ? event.target : null;
            const nodeRef = target ? target.closest('[data-node-ref]') : null;
            if (!nodeRef) return;
            event.preventDefault();
            event.stopPropagation();
            const name = nodeRef.getAttribute('data-node-ref') || '';
            if (!name) return;
            pinned = {{ kind: 'node', name }};
            focusNode(name, true);
        }});

        inspector.addEventListener('mouseover', (event) => {{
            const target = event.target instanceof Element ? event.target : null;
            const nodeRef = target ? target.closest('[data-node-ref]') : null;
            if (!nodeRef) return;
            const name = nodeRef.getAttribute('data-node-ref') || '';
            if (!name) return;
            if (name === inspectorHoverName) return;
            inspectorHoverName = name;
            _scheduleHover(() => focusNode(name, false, false));
        }});

        inspector.addEventListener('mouseout', (event) => {{
            const target = event.target instanceof Element ? event.target : null;
            const nodeRef = target ? target.closest('[data-node-ref]') : null;
            if (!nodeRef) return;
            const related = event.relatedTarget;
            if (related instanceof Node && nodeRef.contains(related)) return;
            inspectorHoverName = null;
            _scheduleHover(() => restorePinnedView());
        }});

        canvas.addEventListener('click', (event) => {{
            if (event.target.closest('g.node') || event.target.closest('g.edge')) return;
            pinned = null;
            clearFocus(true);
        }});

        window.addEventListener('keydown', (event) => {{
            if (event.key === 'Escape') {{
                pinned = null;
                clearFocus(true);
                return;
            }}
            if (event.key === '/' && document.activeElement !== searchInput) {{
                event.preventDefault();
                searchInput.focus();
                searchInput.select();
                return;
            }}
            const typingTarget = event.target;
            const isTyping =
                typingTarget instanceof HTMLInputElement ||
                typingTarget instanceof HTMLTextAreaElement ||
                (typingTarget instanceof HTMLElement && typingTarget.isContentEditable);
            if (!isTyping && (event.key === 'a' || event.key === 'A')) {{
                event.preventDefault();
                setArchitectureReviewMode(!reviewMode);
                return;
            }}
            if (!isTyping && (event.key === 'c' || event.key === 'C')) {{
                event.preventDefault();
                triggerDependencyChain();
                return;
            }}
            if (event.key === 'f' || event.key === 'F') {{
                fitToView();
                return;
            }}
            if (event.key === '+' || event.key === '=') {{
                scale = Math.min(4, scale + 0.1);
                applyTransform();
                return;
            }}
            if (event.key === '-') {{
                scale = Math.max(0.2, scale - 0.1);
                applyTransform();
                return;
            }}
            if (event.key === '0') {{
                scale = 1;
                applyTransform();
                return;
            }}
        }});

        canvas.addEventListener('wheel', (event) => {{
            event.preventDefault();
            const delta = Math.sign(event.deltaY) * -0.1;
            scale = Math.min(4, Math.max(0.2, scale + delta));
            applyTransform();
        }});

        canvas.addEventListener('mousedown', (event) => {{
            isDragging = true;
            startX = event.clientX - originX;
            startY = event.clientY - originY;
        }});

        window.addEventListener('mousemove', (event) => {{
            if (!isDragging) return;
            originX = event.clientX - startX;
            originY = event.clientY - startY;
            applyTransform();
        }});

        window.addEventListener('mouseup', () => {{
            isDragging = false;
        }});

        searchInput.addEventListener('input', () => runSearch(searchInput.value));
        searchInput.addEventListener('keydown', (event) => {{
            if (event.key === 'Enter') {{
                event.preventDefault();
                stepSearch();
            }}
        }});

        document.getElementById('btn-fit').addEventListener('click', fitToView);
        document.getElementById('btn-reset-zoom').addEventListener('click', () => {{
            scale = 1;
            applyTransform();
        }});
        document.getElementById('btn-zoom-in').addEventListener('click', () => {{
            scale = Math.min(4, scale + 0.1);
            applyTransform();
        }});
        document.getElementById('btn-zoom-out').addEventListener('click', () => {{
            scale = Math.max(0.2, scale - 0.1);
            applyTransform();
        }});
        document.getElementById('btn-chain').addEventListener('click', () => {{
            triggerDependencyChain();
        }});
        document.getElementById('btn-arch-review').addEventListener('click', () => {{
            setArchitectureReviewMode(!reviewMode);
        }});
        document.getElementById('btn-clear-focus').addEventListener('click', () => {{
            pinned = null;
            clearFocus(true);
        }});

        window.addEventListener('resize', fitToView);
        fitToView();
    </script>
</body>
</html>"""


def _html_with_fallback(dot: str, title: str, error: str) -> str:
    display_title = _display_graph_title(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{display_title}</title>
  <style>
    body {{
      margin: 0;
      font-family: Helvetica, Arial, sans-serif;
      background: #f4f4f4;
      color: #1e1e1e;
    }}
    header {{
      padding: 12px 16px;
      background: #1f2937;
      color: #f9fafb;
      font-size: 14px;
    }}
    .warning {{
      padding: 12px 16px;
      background: #fff3cd;
      color: #5c4400;
      border-bottom: 1px solid #e8d18a;
    }}
    pre {{
      padding: 16px;
      white-space: pre-wrap;
    }}
  </style>
</head>
<body>
    <header>{display_title}</header>
  <div class="warning">Graphviz rendering failed: {error}</div>
  <pre>{dot}</pre>
</body>
</html>"""


def _display_graph_title(title: str) -> str:
    return title.replace("import_cruiser", "import-cruiser")
