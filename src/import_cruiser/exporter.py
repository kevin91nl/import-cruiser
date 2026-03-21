"""Export a DependencyGraph to JSON or DOT (Graphviz) format."""
# pylint: disable=duplicate-code

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from pathlib import Path
from typing import TypedDict, cast

from import_cruiser.detector import detect_cycles
from import_cruiser.config import JSONDict
from import_cruiser.graph import DependencyGraph, Module
from import_cruiser.validator import Violation


class Cluster(TypedDict):
    id: str
    label: str
    parent: str | None
    modules: list[Module]


def export_json(
    graph: DependencyGraph,
    violations: list[Violation] | None = None,
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
    violations: list[Violation] | None = None,
    rankdir: str = "LR",
    cluster_depth: int = 1,
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

    if cluster_depth > 0:
        root_clusters, root_modules, flat_clusters = _build_clusters(
            graph.modules, cluster_depth, cluster_mode
        )
        lines.extend(
            _render_cluster_tree(
                root_clusters,
                flat_clusters,
                cycle_nodes,
                indent="    ",
                mode=cluster_mode,
            )
        )
        for module in graph.modules:
            cluster_key = _node_cluster_key(
                module.name, module.path, cluster_mode, cluster_depth
            )
            if cluster_key:
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
    violations: list[Violation] | None = None,
    engine: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 1,
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
    return _render_dot(dot, "svg", engine=engine)


def export_html(
    graph: DependencyGraph,
    graph_name: str = "import_cruiser",
    violations: list[Violation] | None = None,
    engine: str = "dot",
    rankdir: str = "LR",
    cluster_depth: int = 1,
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
        svg = _render_dot(dot, "svg", engine=engine)
        body = _html_with_svg(svg, graph_name)
    except RuntimeError as exc:
        body = _html_with_fallback(dot, graph_name, str(exc))
    return body


def _dot_id(name: str) -> str:
    """Convert a dotted module name to a valid DOT identifier."""
    return '"' + name.replace('"', '\\"') + '"'


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

        if parent_id is None:
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
) -> list[str]:
    lines: list[str] = []

    def children_of(parent_id: str) -> list[Cluster]:
        return [c for c in flat_clusters.values() if c["parent"] == parent_id]

    def render_cluster(cluster: Cluster, level_indent: str) -> None:
        cid = _cluster_id(cluster["id"])
        lines.append(f'{level_indent}subgraph "cluster_{cid}" {{')
        lines.append(f'{level_indent}    label="{cluster["label"]}";')
        lines.append(f'{level_indent}    color="#4A4A4A";')
        lines.append(f"{level_indent}    penwidth=1.2;")
        lines.append(f'{level_indent}    style="rounded,filled";')
        lines.append(f'{level_indent}    fillcolor="#FAFAFA";')
        lines.append(f'{level_indent}    fontname="Helvetica";')
        lines.append(f"{level_indent}    fontsize=11;")

        for child in sorted(children_of(cluster["id"]), key=lambda c: c["label"]):
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


def _leaf_label(module, mode: str) -> str:
    if mode == "path":
        return Path(module.path).name
    return module.name.split(".")[-1]


def _node_cluster_key(name: str, path: str, mode: str, depth: int) -> str:
    if depth <= 0:
        return ""
    if mode == "path":
        parts = name.split("/")
        if parts and parts[-1].endswith(".py"):
            parts = parts[:-1]
        if not parts:
            return ""
        return "/".join(parts[: min(depth, len(parts))])
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
        return os.path.commonpath(paths)
    except ValueError:
        return None


def _edges_from_violations(
    violations: list[Violation],
) -> dict[tuple[str, str], Violation]:
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
            'compound=true, concentrate=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white"'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#A0A0A0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    if style == "cruiser":
        graph_attrs = (
            f"rankdir={rankdir}, splines=curved, overlap=prism, ranksep=0.9, "
            'nodesep=0.5, pack=true, packmode="array_u", newrank=true, '
            'compound=true, concentrate=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white"'
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
            'compound=true, concentrate=true, ratio="compress", '
            'fontname="Helvetica", fontsize=10, bgcolor="white"'
        )
        node_attrs = (
            'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
            'color="#2F2F2F", fillcolor="#DFF3F8"'
        )
        edge_attrs = 'color="#B0B0B0", arrowsize=0.7'
        return graph_attrs, node_attrs, edge_attrs

    graph_attrs = (
        f"rankdir={rankdir}, splines=true, overlap=false, ranksep=1.1, nodesep=0.5, "
        'pack=true, packmode="array_u", newrank=true, compound=true, '
        'ratio="compress", fontname="Helvetica", fontsize=10, bgcolor="white"'
    )
    node_attrs = (
        'shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10, '
        'color="#2F2F2F", fillcolor="#DFF3F8"'
    )
    edge_attrs = 'color="#808080", arrowsize=0.7'
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


def _html_with_svg(svg: str, title: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
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
    }}
    .viewport:active {{
      cursor: grabbing;
    }}
  </style>
</head>
<body>
  <header>{title}</header>
  <div class="canvas" id="canvas">
    <div class="viewport" id="viewport">{svg}</div>
  </div>
  <script>
    const canvas = document.getElementById('canvas');
    const viewport = document.getElementById('viewport');
    const svg = viewport.querySelector('svg');
    let scale = 1;
    let originX = 0;
    let originY = 0;
    let isDragging = false;
    let startX = 0;
    let startY = 0;

    const applyTransform = () => {{
      viewport.style.transform = `translate(${{originX}}px, ${{originY}}px) scale(${{scale}})`;
    }};

    const fitToView = () => {{
      if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
      const vb = svg.viewBox.baseVal;
      if (!vb.width || !vb.height) return;
      const scaleX = canvas.clientWidth / vb.width;
      const scaleY = canvas.clientHeight / vb.height;
      scale = Math.min(scaleX, scaleY) * 0.95;
      originX = (canvas.clientWidth - vb.width * scale) / 2;
      originY = (canvas.clientHeight - vb.height * scale) / 2;
      applyTransform();
    }};

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
    fitToView();
  </script>
</body>
</html>"""


def _html_with_fallback(dot: str, title: str, error: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
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
  <header>{title}</header>
  <div class="warning">Graphviz rendering failed: {error}</div>
  <pre>{dot}</pre>
</body>
</html>"""
