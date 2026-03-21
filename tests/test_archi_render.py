"""Tests for archi-style rendering on sample projects."""

from pathlib import Path

from import_cruiser.analyzer import Analyzer
from import_cruiser.exporter import export_dot
from import_cruiser.graph import aggregate_by_path, filter_graph


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_projects"


def test_project_level_edge() -> None:
    graph = Analyzer(FIXTURE_ROOT).analyze()
    aggregated = aggregate_by_path(graph, depth=1)
    assert {m.name for m in aggregated.modules} == {"proj_a", "proj_b"}
    assert set(aggregated.edges()) == {("proj_a", "proj_b")}


def test_archi_clusters_and_edge() -> None:
    graph = Analyzer(FIXTURE_ROOT).analyze()
    filtered = filter_graph(graph, include_paths=[r"src/"])
    aggregated = aggregate_by_path(filtered, depth=3)
    dot = export_dot(
        aggregated,
        rankdir="TB",
        cluster_depth=3,
        cluster_mode="path",
        style="archi",
    )
    assert "cluster_proj_a" in dot
    assert "cluster_proj_a_src" in dot
    assert "cluster_proj_a_src_proj_a" in dot
    assert "proj_a/src/proj_a" in dot
    assert '"proj_a/src/proj_a" -> "proj_b/src/proj_b"' in dot
