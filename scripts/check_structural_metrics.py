#!/usr/bin/env python3
"""Check structural metrics: LCOM, DIT, fan-in/fan-out, and trampolines."""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


@dataclass
class MethodInfo:
    name: str
    lineno: int
    attrs_used: set[str] = field(default_factory=set)
    calls: set[str] = field(default_factory=set)
    is_instance: bool = True
    decorator_names: set[str] = field(default_factory=set)
    first_arg: str | None = None


@dataclass
class ClassInfo:
    name: str
    module: str
    file_path: Path
    lineno: int
    bases: list[str]
    methods: dict[str, MethodInfo] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class FunctionInfo:
    name: str
    module: str
    file_path: Path
    lineno: int
    calls: set[str] = field(default_factory=set)

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass
class MetricsConfig:
    max_lcom: int = 10
    max_dit: int = 4
    max_fanin: int = 20
    max_fanout: int = 20
    fail_on_trampoline: bool = True
    max_forwarder_lines: int = 2
    fail_on_indirection: bool = True
    max_instability: float = 0.7
    instability_allowlist: set[str] = field(default_factory=set)
    max_module_lcom: int = 25
    module_lcom_allowlist: set[str] = field(default_factory=set)
    fail_on_cycles: bool = True
    fail_on_mutual_calls: bool = True
    instability_scope_prefixes: list[str] = field(default_factory=list)
    cycle_scope_prefixes: list[str] = field(default_factory=list)
    mutual_call_scope_prefixes: list[str] = field(default_factory=list)
    module_lcom_scope_prefixes: list[str] = field(default_factory=list)
    lcom_allowlist: set[str] = field(default_factory=set)
    dit_allowlist: set[str] = field(default_factory=set)
    fanin_allowlist: set[str] = field(default_factory=set)
    fanout_allowlist: set[str] = field(default_factory=set)
    trampoline_allowlist: set[str] = field(default_factory=set)
    indirection_allowlist: set[str] = field(default_factory=set)
    paths: list[str] = field(default_factory=lambda: ["src"])
    exclude_paths: list[str] = field(default_factory=list)


@dataclass
class TrampolineInfo:
    name: str
    file_path: Path
    lineno: int
    fan_in: int
    fan_out: int


def load_config(pyproject_path: Path) -> MetricsConfig:
    if not pyproject_path.exists():
        return MetricsConfig()
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    cfg = data.get("tool", {}).get("import_cruiser_metrics", {})
    return MetricsConfig(
        max_lcom=int(cfg.get("max_lcom", 10)),
        max_dit=int(cfg.get("max_dit", 4)),
        max_fanin=int(cfg.get("max_fanin", 20)),
        max_fanout=int(cfg.get("max_fanout", 20)),
        fail_on_trampoline=bool(cfg.get("fail_on_trampoline", True)),
        max_forwarder_lines=int(cfg.get("max_forwarder_lines", 2)),
        fail_on_indirection=bool(cfg.get("fail_on_indirection", True)),
        max_instability=float(cfg.get("max_instability", 0.7)),
        instability_allowlist=set(cfg.get("instability_allowlist", [])),
        max_module_lcom=int(cfg.get("max_module_lcom", 25)),
        module_lcom_allowlist=set(cfg.get("module_lcom_allowlist", [])),
        fail_on_cycles=bool(cfg.get("fail_on_cycles", True)),
        fail_on_mutual_calls=bool(cfg.get("fail_on_mutual_calls", True)),
        instability_scope_prefixes=list(cfg.get("instability_scope_prefixes", [])),
        cycle_scope_prefixes=list(cfg.get("cycle_scope_prefixes", [])),
        mutual_call_scope_prefixes=list(cfg.get("mutual_call_scope_prefixes", [])),
        module_lcom_scope_prefixes=list(cfg.get("module_lcom_scope_prefixes", [])),
        lcom_allowlist=set(cfg.get("lcom_allowlist", [])),
        dit_allowlist=set(cfg.get("dit_allowlist", [])),
        fanin_allowlist=set(cfg.get("fanin_allowlist", [])),
        fanout_allowlist=set(cfg.get("fanout_allowlist", [])),
        trampoline_allowlist=set(cfg.get("trampoline_allowlist", [])),
        indirection_allowlist=set(cfg.get("indirection_allowlist", [])),
        paths=list(cfg.get("paths", ["src"])),
        exclude_paths=list(cfg.get("exclude_paths", [])),
    )


def iter_python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for root in paths:
        if root.is_file() and root.suffix == ".py":
            yield root
            continue
        for path in root.rglob("*.py"):
            parts = {p for p in path.parts}
            if "__pycache__" in parts or ".venv" in parts or "venv" in parts:
                continue
            yield path


def module_name_for(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root)
    parts = list(rel.parts)
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for deco in node.decorator_list:
        if isinstance(deco, ast.Name):
            names.add(deco.id)
        elif isinstance(deco, ast.Attribute):
            names.add(deco.attr)
    return names


def is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(getattr(stmt, "value", None), ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def get_non_docstring_body(node: ast.AST) -> list[ast.stmt]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    return [stmt for stmt in node.body if not is_docstring(stmt)]


def get_single_call(node: ast.AST) -> ast.Call | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    body = get_non_docstring_body(node)
    if len(body) != 1:
        return None
    stmt = body[0]
    if isinstance(stmt, ast.Return):
        value = stmt.value
    elif isinstance(stmt, ast.Expr):
        value = stmt.value
    else:
        return None
    if isinstance(value, ast.Await):
        value = value.value
    if isinstance(value, ast.Call):
        return value
    return None


def extract_base_name(base: ast.expr) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def collect_self_attrs(node: ast.AST, instance_name: str) -> set[str]:
    attrs: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Attribute(self, attr_node: ast.Attribute) -> None:  # noqa: N802
            if isinstance(attr_node.value, ast.Name) and attr_node.value.id == instance_name:
                attrs.add(attr_node.attr)
            self.generic_visit(attr_node)

    Visitor().visit(node)
    return attrs


def collect_name_usage(node: ast.AST) -> set[str]:
    names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Name(self, name_node: ast.Name) -> None:  # noqa: N802
            names.add(name_node.id)
            self.generic_visit(name_node)

    Visitor().visit(node)
    return names


def collect_calls(
    node: ast.AST,
    module: str,
    module_funcs: set[str],
    class_name: str | None,
    class_methods: set[str],
    instance_name: str | None,
    class_lookup: dict[str, ClassInfo],
) -> set[str]:
    calls: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, call_node: ast.Call) -> None:  # noqa: N802
            target = call_node.func
            if isinstance(target, ast.Name):
                if target.id in module_funcs:
                    calls.add(f"{module}.{target.id}")
            elif isinstance(target, ast.Attribute):
                if isinstance(target.value, ast.Name):
                    value_name = target.value.id
                    if instance_name and value_name == instance_name and class_name:
                        if target.attr in class_methods:
                            calls.add(f"{module}.{class_name}.{target.attr}")
                    elif value_name in class_lookup:
                        cls = class_lookup[value_name]
                        if target.attr in cls.methods:
                            calls.add(f"{cls.module}.{cls.name}.{target.attr}")
            self.generic_visit(call_node)

    Visitor().visit(node)
    return calls


def parse_module(path: Path, src_root: Path) -> tuple[list[FunctionInfo], list[ClassInfo]]:
    module = module_name_for(path, src_root)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(f"Skipping {path} due to syntax error: {exc}", file=sys.stderr)
        return [], []
    module_funcs: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    module_func_names: set[str] = set()
    class_name_to_methods: dict[str, set[str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            module_func_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            class_name_to_methods[node.name] = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

    class_lookup: dict[str, ClassInfo] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for base in node.bases:
            base_name = extract_base_name(base)
            if base_name:
                bases.append(base_name)
        class_info = ClassInfo(
            name=node.name,
            module=module,
            file_path=path,
            lineno=node.lineno,
            bases=bases,
        )
        class_lookup[node.name] = class_info
        classes.append(class_info)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_info = FunctionInfo(
                name=node.name,
                module=module,
                file_path=path,
                lineno=node.lineno,
            )
            func_info.calls = collect_calls(
                node,
                module,
                module_func_names,
                None,
                set(),
                None,
                class_lookup,
            )
            module_funcs.append(func_info)
        elif isinstance(node, ast.ClassDef):
            class_info = class_lookup[node.name]
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                deco_names = decorator_names(item)
                is_static = "staticmethod" in deco_names
                is_class = "classmethod" in deco_names
                first_arg = item.args.args[0].arg if item.args.args else None
                instance_name = None if (is_static or is_class) else first_arg
                attrs_used = set()
                if instance_name:
                    attrs_used = collect_self_attrs(item, instance_name)
                method = MethodInfo(
                    name=item.name,
                    lineno=item.lineno,
                    attrs_used=attrs_used,
                    is_instance=not (is_static or is_class),
                    decorator_names=deco_names,
                    first_arg=first_arg,
                )
                method.calls = collect_calls(
                    item,
                    module,
                    module_func_names,
                    class_info.name,
                    class_name_to_methods.get(class_info.name, set()),
                    instance_name,
                    class_lookup,
                )
                class_info.methods[item.name] = method

    return module_funcs, classes


def compute_lcom(class_info: ClassInfo) -> int:
    methods = [m for m in class_info.methods.values() if m.is_instance]
    if len(methods) < 2:
        return 0
    p = 0
    q = 0
    for i in range(len(methods)):
        for j in range(i + 1, len(methods)):
            if methods[i].attrs_used.intersection(methods[j].attrs_used):
                q += 1
            else:
                p += 1
    return max(p - q, 0)


def compute_module_lcom(function_uses: dict[str, set[str]]) -> int:
    funcs = list(function_uses.keys())
    if len(funcs) < 2:
        return 0
    shared = 0
    non_shared = 0
    for i, func_a in enumerate(funcs):
        for func_b in funcs[i + 1 :]:
            if function_uses[func_a] & function_uses[func_b]:
                shared += 1
            else:
                non_shared += 1
    return max(non_shared - shared, 0)


def build_module_dependency_graph(
    module_imports: dict[str, set[str]],
    modules: set[str],
) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {m: set() for m in modules}
    for module, imports in module_imports.items():
        for imported in imports:
            for candidate in modules:
                if imported == candidate or imported.startswith(f"{candidate}."):
                    graph[module].add(candidate)
    return graph


def tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    result: list[list[str]] = []

    def strongconnect(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for neighbor in graph.get(node, set()):
            if neighbor not in indices:
                strongconnect(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[neighbor])

        if lowlinks[node] == indices[node]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                scc.append(w)
                if w == node:
                    break
            result.append(scc)

    for node in graph.keys():
        if node not in indices:
            strongconnect(node)
    return result


def compute_dit(class_info: ClassInfo, class_index: dict[str, ClassInfo], stack: set[str]) -> int:
    name = class_info.qualified_name
    if name in stack:
        return 1
    stack.add(name)
    if not class_info.bases:
        stack.remove(name)
        return 0
    depths: list[int] = []
    for base in class_info.bases:
        resolved = None
        candidate = f"{class_info.module}.{base}"
        if candidate in class_index:
            resolved = class_index[candidate]
        else:
            matches = [cls for cls in class_index.values() if cls.name == base]
            if len(matches) == 1:
                resolved = matches[0]
        if resolved:
            depths.append(1 + compute_dit(resolved, class_index, stack))
        else:
            depths.append(1)
    stack.remove(name)
    return max(depths) if depths else 0


def is_trampoline(node: ast.AST) -> bool:
    return get_single_call(node) is not None


def _param_names(node: ast.AST) -> tuple[list[str], list[str], str | None, str | None]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ([], [], None, None)
    args = node.args
    positional = [a.arg for a in (args.posonlyargs + args.args)]
    kwonly = [a.arg for a in args.kwonlyargs]
    vararg = args.vararg.arg if args.vararg else None
    kwarg = args.kwarg.arg if args.kwarg else None
    return (positional, kwonly, vararg, kwarg)


def _call_matches_params(call: ast.Call, node: ast.AST) -> bool:
    positional, kwonly, vararg, kwarg = _param_names(node)
    expected_pos = positional[:]
    seen = set()

    for arg in call.args:
        if isinstance(arg, ast.Starred):
            if not vararg or not isinstance(arg.value, ast.Name) or arg.value.id != vararg:
                return False
            seen.add(vararg)
            continue
        if not expected_pos or not isinstance(arg, ast.Name) or arg.id != expected_pos[0]:
            return False
        seen.add(expected_pos.pop(0))

    for kw in call.keywords:
        if kw.arg is None:
            if not kwarg or not isinstance(kw.value, ast.Name) or kw.value.id != kwarg:
                return False
            seen.add(kwarg)
            continue
        if kw.arg not in positional and kw.arg not in kwonly:
            return False
        if not isinstance(kw.value, ast.Name) or kw.value.id != kw.arg:
            return False
        seen.add(kw.arg)

    required = set(positional + kwonly)
    if required - seen:
        return False
    if vararg and vararg not in seen:
        return False
    if kwarg and kwarg not in seen:
        return False
    return True


def is_indirection_wrapper(node: ast.AST, *, max_lines: int) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    call = get_single_call(node)
    if call is None:
        return False
    body = get_non_docstring_body(node)
    if not body:
        return False
    start = body[0].lineno
    end = body[-1].end_lineno or body[-1].lineno
    code_lines = end - start + 1
    if code_lines > max_lines:
        return False
    return _call_matches_params(call, node)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="pyproject.toml")
    parser.add_argument("--paths", nargs="*")
    parser.add_argument("--exclude", nargs="*")
    parser.add_argument("--max-lcom", type=int)
    parser.add_argument("--max-dit", type=int)
    parser.add_argument("--max-fanin", type=int)
    parser.add_argument("--max-fanout", type=int)
    parser.add_argument("--max-forwarder-lines", type=int)
    parser.add_argument("--fail-on-trampoline", action="store_true")
    parser.add_argument("--no-fail-on-trampoline", action="store_true")
    parser.add_argument("--fail-on-indirection", action="store_true")
    parser.add_argument("--no-fail-on-indirection", action="store_true")
    parser.add_argument("--max-instability", type=float)
    parser.add_argument("--max-module-lcom", type=int)
    parser.add_argument("--fail-on-cycles", action="store_true")
    parser.add_argument("--no-fail-on-cycles", action="store_true")
    parser.add_argument("--fail-on-mutual-calls", action="store_true")
    parser.add_argument("--no-fail-on-mutual-calls", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    if args.paths:
        cfg.paths = list(args.paths)
    if args.exclude:
        cfg.exclude_paths = list(args.exclude)
    if args.max_lcom is not None:
        cfg.max_lcom = args.max_lcom
    if args.max_dit is not None:
        cfg.max_dit = args.max_dit
    if args.max_fanin is not None:
        cfg.max_fanin = args.max_fanin
    if args.max_fanout is not None:
        cfg.max_fanout = args.max_fanout
    if args.max_forwarder_lines is not None:
        cfg.max_forwarder_lines = args.max_forwarder_lines
    if args.fail_on_trampoline:
        cfg.fail_on_trampoline = True
    if args.no_fail_on_trampoline:
        cfg.fail_on_trampoline = False
    if args.fail_on_indirection:
        cfg.fail_on_indirection = True
    if args.no_fail_on_indirection:
        cfg.fail_on_indirection = False
    if args.max_instability is not None:
        cfg.max_instability = args.max_instability
    if args.max_module_lcom is not None:
        cfg.max_module_lcom = args.max_module_lcom
    if args.fail_on_cycles:
        cfg.fail_on_cycles = True
    if args.no_fail_on_cycles:
        cfg.fail_on_cycles = False
    if args.fail_on_mutual_calls:
        cfg.fail_on_mutual_calls = True
    if args.no_fail_on_mutual_calls:
        cfg.fail_on_mutual_calls = False

    roots = [Path(p) for p in cfg.paths]
    excluded = {Path(p) for p in cfg.exclude_paths}
    src_root = None
    if len(roots) == 1 and roots[0].is_dir():
        src_root = roots[0]
    results = {
        "lcom": [],
        "dit": [],
        "fan_in": [],
        "fan_out": [],
        "trampolines": [],
        "indirections": [],
        "module_lcom": [],
        "instability": [],
        "cycles": [],
        "mutual_calls": [],
    }

    module_funcs: list[FunctionInfo] = []
    classes: list[ClassInfo] = []

    for root in roots:
        if root.is_dir():
            src_root = root
        if src_root is None:
            continue
        for path in iter_python_files([root]):
            if any(path.is_relative_to(excluded_path) for excluded_path in excluded if excluded_path.exists()):
                continue
            if not path.is_relative_to(src_root):
                continue
            funcs, cls = parse_module(path, src_root)
            module_funcs.extend(funcs)
            classes.extend(cls)

    class_index = {cls.qualified_name: cls for cls in classes}

    for cls in classes:
        lcom = compute_lcom(cls)
        dit = compute_dit(cls, class_index, set())
        results["lcom"].append((lcom, cls))
        results["dit"].append((dit, cls))

    function_index: dict[str, tuple[Path, int, ast.AST, str]] = {}
    module_imports: dict[str, set[str]] = {}
    module_function_uses: dict[str, dict[str, set[str]]] = {}
    callers: dict[str, set[str]] = {}
    callees: dict[str, set[str]] = {}

    for root in roots:
        if not root.is_dir():
            continue
        for path in iter_python_files([root]):
            if any(path.is_relative_to(excluded_path) for excluded_path in excluded if excluded_path.exists()):
                continue
            if not path.is_relative_to(root):
                continue
            module = module_name_for(path, root)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                print(f"Skipping {path} due to syntax error: {exc}", file=sys.stderr)
                continue
            module_imports.setdefault(module, set())
            module_function_uses.setdefault(module, {})
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    function_index[f"{module}.{node.name}"] = (path, node.lineno, node, module)
                    module_function_uses[module][node.name] = collect_name_usage(node)
                elif isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            function_index[f"{module}.{node.name}.{item.name}"] = (
                                path,
                                item.lineno,
                                item,
                                module,
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name:
                            module_imports[module].add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        module_imports[module].add(node.module)

    for func in module_funcs:
        callees.setdefault(func.qualified_name, set()).update(func.calls)
        for callee in func.calls:
            callers.setdefault(callee, set()).add(func.qualified_name)

    for cls in classes:
        for method in cls.methods.values():
            qname = f"{cls.module}.{cls.name}.{method.name}"
            callees.setdefault(qname, set()).update(method.calls)
            for callee in method.calls:
                callers.setdefault(callee, set()).add(qname)

    for qname, info in function_index.items():
        fan_in = len(callers.get(qname, set()))
        fan_out = len(callees.get(qname, set()))
        results["fan_in"].append((fan_in, qname, info))
        results["fan_out"].append((fan_out, qname, info))

        path, lineno, node, _ = info
        if cfg.fail_on_trampoline and fan_in == 1 and fan_out == 1:
            if is_trampoline(node) and qname not in cfg.trampoline_allowlist:
                results["trampolines"].append(
                    TrampolineInfo(qname, path, lineno, fan_in, fan_out)
                )
        if cfg.fail_on_indirection and qname not in cfg.indirection_allowlist:
            if is_indirection_wrapper(node, max_lines=cfg.max_forwarder_lines):
                results["indirections"].append((qname, path, lineno))

    errors: list[str] = []

    for lcom, cls in sorted(results["lcom"], key=lambda item: item[0], reverse=True):
        if lcom > cfg.max_lcom and cls.qualified_name not in cfg.lcom_allowlist:
            errors.append(
                f"LCOM {lcom} > {cfg.max_lcom} for {cls.qualified_name}"
                f" ({cls.file_path}:{cls.lineno})"
            )

    for dit, cls in sorted(results["dit"], key=lambda item: item[0], reverse=True):
        if dit > cfg.max_dit and cls.qualified_name not in cfg.dit_allowlist:
            errors.append(
                f"DIT {dit} > {cfg.max_dit} for {cls.qualified_name}"
                f" ({cls.file_path}:{cls.lineno})"
            )

    for fan_in, qname, info in sorted(results["fan_in"], key=lambda item: item[0], reverse=True):
        if fan_in > cfg.max_fanin and qname not in cfg.fanin_allowlist:
            path, lineno, _, _ = info
            errors.append(
                f"Fan-in {fan_in} > {cfg.max_fanin} for {qname} ({path}:{lineno})"
            )

    for fan_out, qname, info in sorted(results["fan_out"], key=lambda item: item[0], reverse=True):
        if fan_out > cfg.max_fanout and qname not in cfg.fanout_allowlist:
            path, lineno, _, _ = info
            errors.append(
                f"Fan-out {fan_out} > {cfg.max_fanout} for {qname} ({path}:{lineno})"
            )

    if results["trampolines"]:
        for tri in results["trampolines"]:
            errors.append(
                f"Trampoline {tri.name} (fan-in={tri.fan_in}, fan-out={tri.fan_out})"
                f" at {tri.file_path}:{tri.lineno}"
            )

    if results["indirections"]:
        for qname, path, lineno in results["indirections"]:
            errors.append(f"Indirection wrapper {qname} at {path}:{lineno}")

    for module, func_uses in module_function_uses.items():
        if cfg.module_lcom_scope_prefixes and not module.startswith(
            tuple(cfg.module_lcom_scope_prefixes)
        ):
            continue
        module_lcom = compute_module_lcom(func_uses)
        results["module_lcom"].append((module_lcom, module))
        if module_lcom > cfg.max_module_lcom and module not in cfg.module_lcom_allowlist:
            errors.append(f"Module LCOM {module_lcom} > {cfg.max_module_lcom} for {module}")

    modules = set(module_imports.keys())
    if modules:
        graph = build_module_dependency_graph(module_imports, modules)
        instability_modules = modules
        if cfg.instability_scope_prefixes:
            instability_modules = {
                m for m in modules if m.startswith(tuple(cfg.instability_scope_prefixes))
            }
        for module in instability_modules:
            imports = graph.get(module, set())
            ce = len(imports)
            ca = sum(1 for deps in graph.values() if module in deps)
            instability = ce / (ca + ce) if (ca + ce) else 0.0
            results["instability"].append((instability, module, ca, ce))
            if instability > cfg.max_instability and module not in cfg.instability_allowlist:
                errors.append(
                    f"Instability {instability:.2f} > {cfg.max_instability:.2f} for {module} (Ca={ca}, Ce={ce})"
                )

        if cfg.fail_on_cycles:
            cycle_modules = modules
            if cfg.cycle_scope_prefixes:
                cycle_modules = {
                    m for m in modules if m.startswith(tuple(cfg.cycle_scope_prefixes))
                }
            if cycle_modules:
                cycle_graph = {m: {d for d in deps if d in cycle_modules} for m, deps in graph.items() if m in cycle_modules}
            else:
                cycle_graph = graph
            sccs = [scc for scc in tarjan_scc(cycle_graph) if len(scc) > 1]
            for scc in sccs:
                results["cycles"].append(scc)
                errors.append(f"Module cycle detected: {' -> '.join(sorted(scc))}")

    if cfg.fail_on_mutual_calls:
        qname_to_module = {qname: meta[3] for qname, meta in function_index.items()}
        for caller, callees_set in callees.items():
            for callee in callees_set:
                if caller == callee:
                    continue
                if cfg.mutual_call_scope_prefixes:
                    caller_mod = qname_to_module.get(caller, "")
                    callee_mod = qname_to_module.get(callee, "")
                    if not (
                        caller_mod.startswith(tuple(cfg.mutual_call_scope_prefixes))
                        or callee_mod.startswith(tuple(cfg.mutual_call_scope_prefixes))
                    ):
                        continue
                if caller in callees.get(callee, set()):
                    caller_mod = qname_to_module.get(caller, "?")
                    callee_mod = qname_to_module.get(callee, "?")
                    results["mutual_calls"].append((caller, callee))
                    errors.append(
                        f"Mutual call {caller_mod}.{caller} <-> {callee_mod}.{callee}"
                    )

    if args.json:
        payload = {
            "errors": errors,
            "summary": {
                "max_lcom": max((lcom for lcom, _ in results["lcom"]), default=0),
                "max_dit": max((dit for dit, _ in results["dit"]), default=0),
                "max_fanin": max((fin for fin, _, _ in results["fan_in"]), default=0),
                "max_fanout": max((fout for fout, _, _ in results["fan_out"]), default=0),
                "trampolines": len(results["trampolines"]),
                "indirections": len(results["indirections"]),
                "module_lcom_max": max((lcom for lcom, _ in results["module_lcom"]), default=0),
                "max_instability": max((i for i, _, _, _ in results["instability"]), default=0.0),
                "cycles": len(results["cycles"]),
                "mutual_calls": len(results["mutual_calls"]),
            },
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if errors:
            print("Structural metrics failed:")
            for err in errors:
                print(f"- {err}")
        else:
            print("Structural metrics OK")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
