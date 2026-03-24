from pathlib import Path

from import_cruiser.analyzer import Analyzer


MODULE_COUNT = 431
EDGE_COUNT = 655


def _build_scale_fixture(root: Path) -> tuple[int, int]:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)

    groups = [
        ("worker_core", 120),
        ("worker_runtime", 80),
        ("worker_apps", 80),
        ("sdk_core", 50),
        ("sdk_modules.products", 50),
        ("sdk_modules.events", 30),
        ("shared", 21),
    ]

    module_names: list[str] = []
    path_map: dict[str, Path] = {}

    for prefix, count in groups:
        pkg_dir = src / prefix.replace(".", "/")
        pkg_dir.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            mod_name = f"{prefix}.mod_{i:03d}"
            module_names.append(mod_name)
            path_map[mod_name] = pkg_dir / f"mod_{i:03d}.py"

    if len(module_names) != MODULE_COUNT:
        raise AssertionError("Unexpected module count")

    adj: list[list[int]] = [[] for _ in range(MODULE_COUNT)]
    for i in range(MODULE_COUNT - 1):
        adj[i].append(i + 1)
    for i in range(224):
        if i + 2 < MODULE_COUNT:
            adj[i].append(i + 2)
    adj[0].append(10)

    edge_count = sum(len(items) for items in adj)
    if edge_count != EDGE_COUNT:
        raise AssertionError("Unexpected edge count")

    for i, name in enumerate(module_names):
        imports = [f"import {module_names[t]}" for t in adj[i]]
        content = "\n".join(imports)
        if content:
            content += "\n"
        path_map[name].write_text(content, encoding="utf-8")

    return MODULE_COUNT, EDGE_COUNT


def test_scale_fixture_counts(tmp_path: Path) -> None:
    modules, edges = _build_scale_fixture(tmp_path)
    graph = Analyzer(tmp_path).analyze()
    assert len(graph.modules) == modules
    assert len(graph.dependencies) == edges
