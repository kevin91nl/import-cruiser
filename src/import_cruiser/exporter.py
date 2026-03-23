"""Export a DependencyGraph to JSON or DOT (Graphviz) format."""
# pylint: disable=duplicate-code

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404
from pathlib import Path
from typing import Protocol, TypedDict, cast

from import_cruiser.graph import DependencyGraph, Module, detect_cycles

JSONDict = dict[str, object]


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
    style: str = "default",
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

    lines: list[str] = [
        f'digraph "{graph_name}" {{',
        f"    graph [{graph_attrs}];",
        f"    node [{node_attrs}];",
        f"    edge [{edge_attrs}];",
        "",
    ]

    cluster_index: dict[str, list[str]] = {}
    node_to_cluster: dict[str, str] = {}
    valid_cluster_keys: set[str] = set()

    path_root = _common_root(graph.modules) if cluster_mode == "path" else None

    if cluster_depth > 0:
        root_clusters, root_modules, flat_clusters = _build_clusters(
            graph.modules, cluster_depth, cluster_mode
        )
        non_empty_clusters = _non_empty_clusters(flat_clusters)
        valid_cluster_keys = set(non_empty_clusters)
        lines.extend(
            _render_cluster_tree(
                root_clusters,
                flat_clusters,
                cycle_nodes,
                indent="    ",
                mode=cluster_mode,
                allowed=non_empty_clusters,
            )
        )
        for module in graph.modules:
            cluster_key = _node_cluster_key(
                module.name,
                module.path,
                cluster_mode,
                cluster_depth,
                path_root,
            )
            if cluster_key and cluster_key in valid_cluster_keys:
                node_to_cluster[module.name] = cluster_key
                cluster_index.setdefault(cluster_key, []).append(module.name)
    else:
        root_modules = sorted(graph.modules, key=lambda m: m.name)

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

    if edge_mode == "cluster" and cluster_depth > 0:
        cluster_edges: set[tuple[str, str]] = set()
        for dep in graph.dependencies:
            src_cluster = node_to_cluster.get(dep.source)
            tgt_cluster = node_to_cluster.get(dep.target)
            if not src_cluster or not tgt_cluster or src_cluster == tgt_cluster:
                continue
            if _clusters_related(src_cluster, tgt_cluster):
                continue
            cluster_edges.add((src_cluster, tgt_cluster))

        if cluster_edges:
            for src_cluster, tgt_cluster in sorted(cluster_edges):
                src_node = cluster_index[src_cluster][0]
                tgt_node = cluster_index[tgt_cluster][0]
                lines.append(
                    f"    {_dot_id(src_node)} -> {_dot_id(tgt_node)} "
                    f'[ltail="cluster_{_cluster_id(src_cluster)}", '
                    f'lhead="cluster_{_cluster_id(tgt_cluster)}"];'
                )
        else:
            edge_mode = "node"

    if edge_mode != "cluster":
        for dep in sorted(graph.dependencies, key=lambda d: (d.source, d.target)):
            src = _dot_id(dep.source)
            tgt = _dot_id(dep.target)
            violation = violation_edges.get((dep.source, dep.target))
            if violation:
                color = _severity_color(violation.severity)
                lines.append(f'    {src} -> {tgt} [color="{color}", penwidth=2.2];')
            elif (dep.source, dep.target) in cycle_edges:
                lines.append(f'    {src} -> {tgt} [color="#C0392B", penwidth=1.6];')
            else:
                lines.append(f"    {src} -> {tgt};")

    lines.append("}")
    return "\n".join(lines)


def export_svg(
    graph: DependencyGraph,
    graph_name: str = "import_cruiser",
    violations: list[ViolationLike] | None = None,
    engine: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 2,
    cluster_mode: str = "path",
    style: str = "default",
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
    style: str = "default",
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


def _dot_node_lines(
    name: str,
    path: str,
    in_cycle: bool,
    indent: str,
    label: str,
    cluster_key: str | None = None,
) -> list[str]:
    safe = _dot_id(name)
    attrs = [f'label="{label}"', f'tooltip="{path}"']
    if cluster_key:
        attrs.append(f'class="node_cluster_{_cluster_id(cluster_key)}"')
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
        lines.append(f'{level_indent}    class="cluster_key_{cid}";')
        lines.append(f"{level_indent}    margin=26;")
        lines.append(f'{level_indent}    color="black";')
        lines.append(f"{level_indent}    penwidth=1.0;")
        lines.append(f'{level_indent}    style="rounded,bold";')
        lines.append(f'{level_indent}    fontname="Helvetica";')
        lines.append(f"{level_indent}    fontsize=9;")

        allowed_children = [
            child for child in children_of(cluster["id"]) if child["id"] in allowed
        ]
        if not cluster["modules"] and allowed_children:
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
                    cluster_key=cluster["id"],
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
    if style == "archi":
        graph_attrs = (
            f"rankdir={rankdir}, splines=ortho, overlap=false, ranksep=0.9, "
            'nodesep=0.45, pack=true, packmode="array_u", newrank=true, '
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.35, margin=0.35'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#A0A0A0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    if style == "cruiser":
        graph_attrs = (
            f"rankdir={rankdir}, splines=curved, overlap=false, ranksep=1.05, "
            "nodesep=0.6, pack=false, newrank=true, concentrate=true, "
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.45, margin=0.45'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#B0B0B0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    if style == "navigator":
        graph_attrs = (
            f"rankdir={rankdir}, splines=curved, overlap=prism, ranksep=0.9, "
            'nodesep=0.5, pack=true, packmode="clust", newrank=true, '
            'compound=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white", pad=0.35, margin=0.35'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#B0B0B0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    graph_attrs = (
        f"rankdir={rankdir}, splines=curved, overlap=false, nodesep=0.16, ranksep=0.18, "
        'fontname="Helvetica-bold", fontsize=9, style="rounded,bold,filled", '
        'fillcolor="#FFFFFF", compound=true, pack=true, packmode="array_u", '
        'newrank=true, ratio="compress", pad=0.35, margin=0.35'
    )
    node_attrs = (
        'shape=box, style="rounded,filled", height=0.2, color="black", '
        'fillcolor="#FFFFCC", fontcolor="black", fontname="Helvetica", fontsize=9'
    )
    edge_attrs = (
        'arrowhead="normal", arrowsize=0.6, penwidth=2.0, '
        'color="#00000033", fontname="Helvetica", fontsize=9'
    )
    return graph_attrs, node_attrs, edge_attrs


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
        }}
        .canvas {{
            width: 100vw;
            height: calc(100vh - 44px);
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
            max-height: calc(100vh - 90px);
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
        .controls {{
            position: absolute;
            top: 12px;
            left: 12px;
            z-index: 11;
            display: flex;
            gap: 8px;
        }}
        .controls button {{
            border: 1px solid #d1d5db;
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.95);
            color: #111827;
            font-size: 12px;
            padding: 6px 10px;
            cursor: pointer;
        }}
        .controls button:hover {{
            background: #f9fafb;
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
        .dimmed {{
            opacity: 0.14;
            transition: opacity 0.08s ease-out;
        }}
        g.node.active > polygon,
        g.node.active > ellipse,
        g.node.active > rect {{
            stroke: #2563eb;
            stroke-width: 2.2px;
        }}
        g.edge.active > path {{
            stroke: #2563eb;
            stroke-width: 2.4px;
            opacity: 1;
        }}
        g.edge.active > polygon {{
            fill: #2563eb;
            stroke: #2563eb;
            opacity: 1;
        }}
        g.cluster > path,
        g.cluster > polygon {{
            fill: none !important;
        }}
        g.cluster > text {{
            cursor: pointer;
            user-select: none;
        }}
        g.cluster.collapsed > text {{
            font-weight: 700;
            fill: #78350f;
        }}
        g.cluster.collapsed > path,
        g.cluster.collapsed > polygon {{
            display: none;
        }}
        #collapsed-proxy-layer line {{
            stroke: #6b7280;
            stroke-width: 1.4px;
            opacity: 0.9;
        }}
        #collapsed-proxy-layer .collapsed-proxy-badge {{
            fill: #fde68a;
            stroke: #b45309;
            stroke-width: 1px;
        }}
        #collapsed-proxy-layer .collapsed-proxy-dot {{
            fill: #92400e;
        }}
        #cluster-branch-toggle-layer .branch-toggle-badge {{
            fill: #f3f4f6;
            stroke: #6b7280;
            stroke-width: 1px;
        }}
        #cluster-branch-toggle-layer .branch-toggle-text {{
            fill: #374151;
            font-size: 11px;
            font-weight: 700;
            text-anchor: middle;
            dominant-baseline: central;
        }}
    </style>
</head>
<body>
    <header>{display_title}</header>
    <div class="canvas" id="canvas">
        <div class="controls">
            <button type="button" id="expand-all">Expand all</button>
            <button type="button" id="collapse-all">Collapse all</button>
        </div>
        <div class="viewport" id="viewport">{svg}</div>
        <aside id="inspector">
            <h4>Context</h4>
            <div class="muted">Hover a module or dependency edge to see context.</div>
        </aside>
    </div>
    <script>
        const canvas = document.getElementById('canvas');
        const viewport = document.getElementById('viewport');
        const svg = viewport.querySelector('svg');
        const inspector = document.getElementById('inspector');
        const expandAllButton = document.getElementById('expand-all');
        const collapseAllButton = document.getElementById('collapse-all');
        const SVG_NS = 'http://www.w3.org/2000/svg';
        let scale = 1;
        let originX = 0;
        let originY = 0;
        let isDragging = false;
        let startX = 0;
        let startY = 0;
        let pinned = null;

        const esc = (text) => String(text)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');

        const titleOf = (group) => group?.querySelector('title')?.textContent?.trim() || '';
        const nodeGroups = [...svg.querySelectorAll('g.node')];
        const edgeGroups = [...svg.querySelectorAll('g.edge')];
        const clusterGroups = [...svg.querySelectorAll('g.cluster')];
        const nodeByName = new Map();
        const outgoing = new Map();
        const incoming = new Map();
        const edgeKeys = new Set();
        const collapsedClusters = new Set();
        const clusterNodeCounts = new Map();
        const clusterLabels = new Map();
        const clusterAnchors = new Map();
        const proxyPoints = new Map();
        const proxyBoxes = new Map();
        const clusterToggleLayer = document.createElementNS(SVG_NS, 'g');
        clusterToggleLayer.setAttribute('id', 'cluster-branch-toggle-layer');
        const proxyLayer = document.createElementNS(SVG_NS, 'g');
        proxyLayer.setAttribute('id', 'collapsed-proxy-layer');
        const graphRoot =
            svg.querySelector('g[id^="graph"]') || svg.querySelector('g') || svg;
        graphRoot.appendChild(clusterToggleLayer);
        graphRoot.appendChild(proxyLayer);

        nodeGroups.forEach((node) => {{
            const name = titleOf(node);
            node.dataset.name = name;
            const leafKeys = [...node.classList]
                .filter((c) => c.startsWith('node_cluster_'))
                .map((c) => c.replace(/^node_cluster_/, ''));
            const allKeys = new Set();
            leafKeys.forEach((key) => {{
                const parts = key.split('_');
                for (let i = 1; i <= parts.length; i += 1) {{
                    allKeys.add(parts.slice(0, i).join('_'));
                }}
            }});
            node.dataset.clusterKeys = [...allKeys].join('|');
            nodeByName.set(name, node);
            outgoing.set(name, []);
            incoming.set(name, []);
        }});

        edgeGroups.forEach((edge) => {{
            const raw = titleOf(edge);
            const [src, tgt] = raw.split('->').map((s) => s.trim());
            edge.dataset.src = src || '';
            edge.dataset.tgt = tgt || '';
            if (src && tgt) {{
                edgeKeys.add(`${{src}}->${{tgt}}`);
                outgoing.get(src)?.push(tgt);
                incoming.get(tgt)?.push(src);
            }}
        }});

        const labelTextFor = (cluster) => {{
            return [...cluster.children].find(
                (el) => el.tagName?.toLowerCase() === 'text'
            );
        }};

        const keyFromCluster = (cluster) =>
            titleOf(cluster).replace(/^cluster_/, '');

        const nodeInCluster = (node, key) =>
            (node.dataset.clusterKeys || '').split('|').includes(key);

        clusterGroups.forEach((cluster) => {{
            const key = keyFromCluster(cluster);
            cluster.dataset.key = key;
            const labelEl = labelTextFor(cluster);
            if (!labelEl) return;
            const raw = labelEl.textContent || '';
            const base = raw.replace(/^[▸▾]\\s*/, '');
            clusterLabels.set(cluster, base);
            const labelBox = labelEl.getBBox();
            clusterAnchors.set(cluster, {{
                x: labelBox.x,
                y: labelBox.y,
                width: labelBox.width,
                height: labelBox.height,
                textX: parseFloat(labelEl.getAttribute('x') || '0'),
                textY: parseFloat(labelEl.getAttribute('y') || '0'),
                textAnchor: labelEl.getAttribute('text-anchor') || 'middle',
            }});
            clusterNodeCounts.set(
                cluster,
                nodeGroups.filter((node) => nodeInCluster(node, key)).length
            );
        }});

        const isVisible = (element) => element.style.display !== 'none';
        const isNodeVisibleByName = (name) => {{
            const node = nodeByName.get(name);
            return Boolean(node) && isVisible(node);
        }};

        const updateClusterLabel = (cluster) => {{
            const labelEl = labelTextFor(cluster);
            const base = clusterLabels.get(cluster);
            if (!labelEl || !base) return;
            const prefix = collapsedClusters.has(cluster) ? '▸ ' : '▾ ';
            labelEl.textContent = `${{prefix}}${{base}}`;
            labelEl.style.display = collapsedClusters.has(cluster) ? 'none' : '';
            cluster.classList.toggle('collapsed', collapsedClusters.has(cluster));
        }};

        const refreshClusterAnchor = (cluster) => {{
            const labelEl = labelTextFor(cluster);
            if (!labelEl) return;
            const box = labelEl.getBBox();
            if (!box.width && !box.height) return;
            clusterAnchors.set(cluster, {{
                x: box.x,
                y: box.y,
                width: box.width,
                height: box.height,
            }});
        }};

        const ensureProxyArrowMarker = () => {{
            let defs = svg.querySelector('defs');
            if (!defs) {{
                defs = document.createElementNS(SVG_NS, 'defs');
                svg.insertBefore(defs, svg.firstChild);
            }}
            if (defs.querySelector('#collapsed-proxy-arrow')) return;
            const marker = document.createElementNS(SVG_NS, 'marker');
            marker.setAttribute('id', 'collapsed-proxy-arrow');
            marker.setAttribute('viewBox', '0 0 10 10');
            marker.setAttribute('refX', '8');
            marker.setAttribute('refY', '5');
            marker.setAttribute('markerWidth', '5');
            marker.setAttribute('markerHeight', '5');
            marker.setAttribute('orient', 'auto-start-reverse');
            const path = document.createElementNS(SVG_NS, 'path');
            path.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
            path.setAttribute('fill', '#6b7280');
            marker.appendChild(path);
            defs.appendChild(marker);
        }};

        const collapsedClustersForNode = (node) => {{
            const keys = (node.dataset.clusterKeys || '').split('|').filter(Boolean);
            return [...collapsedClusters]
                .filter((cluster) => keys.includes(cluster.dataset.key || ''))
                .sort(
                    (a, b) =>
                        (b.dataset.key || '').length - (a.dataset.key || '').length
                );
        }};

        const descendantsForCluster = (cluster) => {{
            const key = cluster.dataset.key || '';
            if (!key) return [];
            return clusterGroups.filter((candidate) => {{
                if (candidate === cluster) return false;
                const candidateKey = candidate.dataset.key || '';
                return candidateKey.startsWith(`${{key}}_`);
            }});
        }};

        const toggleClusterDescendants = (cluster) => {{
            const descendants = descendantsForCluster(cluster).filter(
                (candidate) => (clusterNodeCounts.get(candidate) || 0) > 0
            );
            if (!descendants.length) return;
            const allCollapsed = descendants.every((candidate) =>
                collapsedClusters.has(candidate)
            );
            if (allCollapsed) {{
                descendants.forEach((candidate) => collapsedClusters.delete(candidate));
            }} else {{
                descendants.forEach((candidate) => collapsedClusters.add(candidate));
            }}
            pinned = null;
            updateVisibility();
            clearFocus(true);
        }};

        const endpointForNodeName = (name) => {{
            const node = nodeByName.get(name);
            if (node && isVisible(node)) {{
                const box = node.getBBox();
                return {{
                    id: `node:${{name}}`,
                    center: {{ x: box.x + box.width / 2, y: box.y + box.height / 2 }},
                    box,
                }};
            }}
            if (!node) return null;
            const targets = collapsedClustersForNode(node);
            for (const target of targets) {{
                const center = proxyPoints.get(target);
                const box = proxyBoxes.get(target);
                if (center && box) {{
                    return {{
                        id: `cluster:${{target.dataset.key || ''}}`,
                        center,
                        box,
                    }};
                }}
            }}
            return null;
        }};

        const boxEdgePoint = (box, from, to) => {{
            const hw = Math.max(1, box.width / 2);
            const hh = Math.max(1, box.height / 2);
            const cx = box.x + hw;
            const cy = box.y + hh;
            const dx = to.x - from.x;
            const dy = to.y - from.y;
            if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) {{
                return {{ x: cx, y: cy }};
            }}
            const tx = Math.abs(dx) / hw;
            const ty = Math.abs(dy) / hh;
            const t = 1 / Math.max(tx, ty, 1e-6);
            return {{ x: cx + dx * t, y: cy + dy * t }};
        }};

        const drawCollapsedProxyEdges = () => {{
            ensureProxyArrowMarker();
            proxyLayer.replaceChildren();
            proxyPoints.clear();
            proxyBoxes.clear();

            [...collapsedClusters]
                .sort((a, b) => (a.dataset.key || '').localeCompare(b.dataset.key || ''))
                .forEach((cluster) => {{
                    if (hasCollapsedAncestorCluster(cluster)) return;
                    const label = clusterLabels.get(cluster) || (cluster.dataset.key || '');
                    const anchor = clusterAnchors.get(cluster);
                    if (!anchor) return;
                    const text = `▸ ${{label}}`;
                    const textEstimate = Math.max(12, text.length * 7.5);
                    const badgeW = Math.max(76, textEstimate + 28);
                    const badgeH = 18;
                    const badgeX = anchor.x - 14;
                    const badgeY = anchor.y - 1;
                    const box = {{
                        x: badgeX,
                        y: badgeY,
                        width: badgeW,
                        height: badgeH,
                    }};
                    const point = {{
                        x: box.x + box.width / 2,
                        y: box.y + box.height / 2,
                    }};
                    proxyPoints.set(cluster, point);
                    proxyBoxes.set(cluster, box);

                    const group = document.createElementNS(SVG_NS, 'g');
                    group.setAttribute('class', 'collapsed-proxy');
                    group.style.pointerEvents = 'none';

                    const rect = document.createElementNS(SVG_NS, 'rect');
                    rect.setAttribute('class', 'collapsed-proxy-badge');
                    rect.setAttribute('x', String(box.x));
                    rect.setAttribute('y', String(box.y));
                    rect.setAttribute('width', String(box.width));
                    rect.setAttribute('height', String(box.height));
                    rect.setAttribute('rx', '3');

                    const dot = document.createElementNS(SVG_NS, 'circle');
                    dot.setAttribute('class', 'collapsed-proxy-dot');
                    dot.setAttribute('cx', String(box.x + 8));
                    dot.setAttribute('cy', String(box.y + box.height / 2));
                    dot.setAttribute('r', '2.2');

                    const textEl = document.createElementNS(SVG_NS, 'text');
                    textEl.setAttribute('x', String(box.x + 14));
                    textEl.setAttribute('y', String(box.y + box.height / 2 + 4));
                    textEl.setAttribute('fill', '#78350f');
                    textEl.setAttribute('font-size', '14');
                    textEl.setAttribute('font-weight', '700');
                    textEl.textContent = text;

                    group.appendChild(rect);
                    group.appendChild(dot);
                    group.appendChild(textEl);
                    group.style.pointerEvents = 'auto';
                    group.style.cursor = 'pointer';
                    group.addEventListener('click', (event) => {{
                        event.stopPropagation();
                        toggleCluster(cluster);
                    }});
                    proxyLayer.appendChild(group);
                }});

            const seen = new Set();
            edgeGroups.forEach((edge) => {{
                const src = edge.dataset.src;
                const tgt = edge.dataset.tgt;
                if (!src || !tgt) return;
                const srcNode = nodeByName.get(src);
                const tgtNode = nodeByName.get(tgt);
                const srcVisible = Boolean(srcNode) && isVisible(srcNode);
                const tgtVisible = Boolean(tgtNode) && isVisible(tgtNode);
                if (srcVisible && tgtVisible) return;
                const srcEnd = endpointForNodeName(src);
                const tgtEnd = endpointForNodeName(tgt);
                if (!srcEnd || !tgtEnd) return;
                if (srcEnd.id === tgtEnd.id) return;
                const key = `${{srcEnd.id}}->${{tgtEnd.id}}`;
                if (seen.has(key)) return;
                seen.add(key);
                const p1 = boxEdgePoint(srcEnd.box, srcEnd.center, tgtEnd.center);
                const p2 = boxEdgePoint(tgtEnd.box, tgtEnd.center, srcEnd.center);
                const line = document.createElementNS(SVG_NS, 'line');
                line.setAttribute('x1', String(p1.x));
                line.setAttribute('y1', String(p1.y));
                line.setAttribute('x2', String(p2.x));
                line.setAttribute('y2', String(p2.y));
                line.setAttribute('marker-end', 'url(#collapsed-proxy-arrow)');
                proxyLayer.appendChild(line);
            }});
        }};

        const drawClusterBranchToggles = () => {{
            clusterToggleLayer.replaceChildren();
            clusterGroups.forEach((cluster) => {{
                if (hasCollapsedAncestorCluster(cluster)) return;
                if (collapsedClusters.has(cluster)) return;
                const descendants = descendantsForCluster(cluster).filter(
                    (candidate) => (clusterNodeCounts.get(candidate) || 0) > 0
                );
                if (!descendants.length) return;
                const anchor = clusterAnchors.get(cluster);
                if (!anchor) return;
                const w = 14;
                const h = 14;
                const x = anchor.x + anchor.width + 8;
                const y = anchor.y - 1;
                const allCollapsed = descendants.every((candidate) =>
                    collapsedClusters.has(candidate)
                );

                const group = document.createElementNS(SVG_NS, 'g');
                group.style.cursor = 'pointer';
                group.style.pointerEvents = 'auto';

                const rect = document.createElementNS(SVG_NS, 'rect');
                rect.setAttribute('class', 'branch-toggle-badge');
                rect.setAttribute('x', String(x));
                rect.setAttribute('y', String(y));
                rect.setAttribute('width', String(w));
                rect.setAttribute('height', String(h));
                rect.setAttribute('rx', '2');

                const text = document.createElementNS(SVG_NS, 'text');
                text.setAttribute('class', 'branch-toggle-text');
                text.setAttribute('x', String(x + w / 2));
                text.setAttribute('y', String(y + h / 2));
                text.textContent = allCollapsed ? '+' : '-';

                group.appendChild(rect);
                group.appendChild(text);
                group.addEventListener('click', (event) => {{
                    event.stopPropagation();
                    toggleClusterDescendants(cluster);
                }});
                clusterToggleLayer.appendChild(group);
            }});
        }};

        const hasCollapsedCluster = (node) => {{
            for (const cluster of collapsedClusters) {{
                const key = cluster.dataset.key || '';
                if (key && nodeInCluster(node, key)) return true;
            }}
            return false;
        }};

        const hasCollapsedAncestorCluster = (cluster) => {{
            const key = cluster.dataset.key || '';
            if (!key) return false;
            for (const parent of collapsedClusters) {{
                if (parent === cluster) continue;
                const parentKey = parent.dataset.key || '';
                if (!parentKey) continue;
                if (key.startsWith(`${{parentKey}}_`)) return true;
            }}
            return false;
        }};

        const updateVisibility = () => {{
            clusterGroups.forEach((cluster) => {{
                if (hasCollapsedAncestorCluster(cluster)) {{
                    cluster.style.display = 'none';
                    return;
                }}
                cluster.style.display = '';
                updateClusterLabel(cluster);
                if (!collapsedClusters.has(cluster)) {{
                    refreshClusterAnchor(cluster);
                }}
            }});

            nodeGroups.forEach((node) => {{
                node.style.display = hasCollapsedCluster(node) ? 'none' : '';
            }});

            edgeGroups.forEach((edge) => {{
                const src = edge.dataset.src;
                const tgt = edge.dataset.tgt;
                const srcNode = nodeByName.get(src);
                const tgtNode = nodeByName.get(tgt);
                edge.style.display =
                    srcNode && tgtNode && isVisible(srcNode) && isVisible(tgtNode)
                        ? ''
                        : 'none';
            }});
            drawCollapsedProxyEdges();
            drawClusterBranchToggles();
        }};

        const expandAllClusters = () => {{
            collapsedClusters.clear();
            updateVisibility();
        }};

        const collapseAllClusters = () => {{
            clusterGroups
                .filter((cluster) => (clusterNodeCounts.get(cluster) || 0) > 0)
                .forEach((cluster) => collapsedClusters.add(cluster));
            updateVisibility();
        }};

        const toggleCluster = (cluster) => {{
            if (collapsedClusters.has(cluster)) {{
                collapsedClusters.delete(cluster);
            }} else {{
                refreshClusterAnchor(cluster);
                collapsedClusters.add(cluster);
            }}
            pinned = null;
            updateVisibility();
            clearFocus(true);
        }};

        const applyTransform = () => {{
            viewport.style.transform = `translate(${{originX}}px, ${{originY}}px) scale(${{scale}})`;
        }};

        const fitToView = () => {{
            if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
            const vb = svg.viewBox.baseVal;
            if (!vb.width || !vb.height) return;
            const scaleX = canvas.clientWidth / vb.width;
            const scaleY = canvas.clientHeight / vb.height;
            scale = Math.min(scaleX, scaleY) * 0.90;
            originX = (canvas.clientWidth - vb.width * scale) / 2;
            originY = (canvas.clientHeight - vb.height * scale) / 2;
            applyTransform();
        }};

        const clearFocus = (force = false) => {{
            if (pinned && !force) return;
            nodeGroups.forEach((n) => n.classList.remove('dimmed', 'active'));
            edgeGroups.forEach((e) => e.classList.remove('dimmed', 'active'));
            inspector.innerHTML = '<h4>Context</h4><div class="muted">Hover a module or dependency edge to see context.</div>';
        }};

        const markNodeActive = (name) => {{
            const n = nodeByName.get(name);
            if (n) {{
                n.classList.remove('dimmed');
                n.classList.add('active');
            }}
        }};

        const focusNode = (name, isPinned = false) => {{
            const out = (outgoing.get(name) || []).filter(
                (candidate) => isNodeVisibleByName(candidate)
            );
            const inc = (incoming.get(name) || []).filter(
                (candidate) => isNodeVisibleByName(candidate)
            );
            const neighbors = new Set([name, ...out, ...inc]);

            nodeGroups.forEach((n) => {{
                n.classList.add('dimmed');
                n.classList.remove('active');
            }});
            edgeGroups.forEach((e) => {{
                e.classList.add('dimmed');
                e.classList.remove('active');
            }});

            neighbors.forEach(markNodeActive);

            edgeGroups.forEach((edge) => {{
                const src = edge.dataset.src;
                const tgt = edge.dataset.tgt;
                if (src === name || tgt === name) {{
                    edge.classList.remove('dimmed');
                    edge.classList.add('active');
                }}
            }});

            const outList = out.length ? `<ul>${{out.map((n) => `<li>${{esc(n)}}</li>`).join('')}}</ul>` : '<div class="muted">none</div>';
            const inList = inc.length ? `<ul>${{inc.map((n) => `<li>${{esc(n)}}</li>`).join('')}}</ul>` : '<div class="muted">none</div>';
            const pinBadge = isPinned ? '<div class="muted"><strong>Pinned</strong> · click empty space or press Esc to release</div>' : '';

            inspector.innerHTML = `
                <h4>${{esc(name)}}</h4>
                ${{pinBadge}}
                <div><strong>Outgoing</strong> (${{out.length}})</div>
                ${{outList}}
                <div><strong>Incoming</strong> (${{inc.length}})</div>
                ${{inList}}
            `;
        }};

        const focusEdge = (src, tgt, isPinned = false) => {{
            nodeGroups.forEach((n) => {{
                n.classList.add('dimmed');
                n.classList.remove('active');
            }});
            edgeGroups.forEach((e) => {{
                e.classList.add('dimmed');
                e.classList.remove('active');
            }});

            markNodeActive(src);
            markNodeActive(tgt);

            edgeGroups.forEach((edge) => {{
                if (edge.dataset.src === src && edge.dataset.tgt === tgt) {{
                    edge.classList.remove('dimmed');
                    edge.classList.add('active');
                }}
            }});

            const isBidirectional = edgeKeys.has(`${{tgt}}->${{src}}`);
            const pinBadge = isPinned ? '<div class="muted"><strong>Pinned</strong> · click empty space or press Esc to release</div>' : '';

            inspector.innerHTML = `
                <h4>Dependency</h4>
                ${{pinBadge}}
                <div><strong>From:</strong> ${{esc(src)}}</div>
                <div><strong>To:</strong> ${{esc(tgt)}}</div>
                <div><strong>Direction:</strong> ${{isBidirectional ? 'bidirectional' : 'one-way'}}</div>
            `;
        }};

        clusterGroups.forEach((cluster) => {{
            const labelEl = labelTextFor(cluster);
            if (!labelEl) return;
            labelEl.addEventListener('click', (event) => {{
                event.stopPropagation();
                toggleCluster(cluster);
            }});
        }});

        expandAllButton.addEventListener('click', () => {{
            expandAllClusters();
            clearFocus(true);
        }});

        collapseAllButton.addEventListener('click', () => {{
            collapseAllClusters();
            clearFocus(true);
        }});

        nodeGroups.forEach((node) => {{
            const name = node.dataset.name;
            node.addEventListener('mouseenter', () => {{
                if (pinned) return;
                focusNode(name);
            }});
            node.addEventListener('mouseleave', () => clearFocus());
            node.addEventListener('click', (event) => {{
                event.stopPropagation();
                pinned = {{ kind: 'node', name }};
                focusNode(name, true);
            }});
        }});

        edgeGroups.forEach((edge) => {{
            const src = edge.dataset.src;
            const tgt = edge.dataset.tgt;
            if (!src || !tgt) return;
            edge.addEventListener('mouseenter', () => {{
                if (pinned) return;
                focusEdge(src, tgt);
            }});
            edge.addEventListener('mouseleave', () => clearFocus());
            edge.addEventListener('click', (event) => {{
                event.stopPropagation();
                pinned = {{ kind: 'edge', src, tgt }};
                focusEdge(src, tgt, true);
            }});
        }});

        canvas.addEventListener('click', (event) => {{
            if (
                event.target.closest('g.node')
                || event.target.closest('g.edge')
                || event.target.closest('g.cluster')
            ) {{
                return;
            }}
            pinned = null;
            clearFocus(true);
        }});

        window.addEventListener('keydown', (event) => {{
            if (event.key !== 'Escape') return;
            pinned = null;
            clearFocus(true);
        }});

        canvas.addEventListener('wheel', (event) => {{
            event.preventDefault();
            const delta = Math.sign(event.deltaY) * -0.1;
            scale = Math.min(3, Math.max(0.2, scale + delta));
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

        window.addEventListener('resize', fitToView);
        updateVisibility();
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
