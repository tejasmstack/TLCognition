"""CI purity gates from spec 03 §7.1.2 (hard rule) and §7.2.2 (lint rules).

- tlc/pipeline/** may import numpy, scipy, skimage and tlc.core — nothing else.
- No wall-clock, uuid, os.urandom, locale, datetime.now inside tlc/pipeline/**.
- Module-level `np.random.*` anywhere under tlc/ is a failure (one PCG64 Generator, seeded
  from the data, passed explicitly).
"""

import ast
from pathlib import Path

TLC = Path(__file__).resolve().parent.parent / "tlc"
PIPELINE = TLC / "pipeline"

ALLOWED_PIPELINE_ROOTS = {"numpy", "scipy", "skimage", "tlc", "math", "dataclasses", "typing", "enum", "collections", "itertools", "functools"}
FORBIDDEN_PIPELINE_MODULES = {"time", "uuid", "locale", "datetime", "os", "random", "secrets", "socket", "requests", "httpx", "urllib"}


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_pipeline_imports_are_pure():
    violations = []
    for py in sorted(PIPELINE.rglob("*.py")):
        tree = ast.parse(py.read_text(), filename=str(py))
        for mod in _imports(tree):
            root = mod.split(".")[0]
            if root in FORBIDDEN_PIPELINE_MODULES:
                violations.append(f"{py.name}: forbidden import '{mod}'")
            elif root not in ALLOWED_PIPELINE_ROOTS:
                violations.append(f"{py.name}: import '{mod}' outside allowed set")
            if mod.startswith("tlc.") and not mod.startswith("tlc.core") and mod != "tlc":
                if not mod.startswith("tlc.pipeline"):
                    violations.append(f"{py.name}: pipeline may only import tlc.core, got '{mod}'")
    assert not violations, "\n".join(violations)


def test_no_module_level_np_random_under_tlc():
    violations = []
    for py in sorted(TLC.rglob("*.py")):
        src = py.read_text()
        tree = ast.parse(src, filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
                # matches np.random.<fn> / numpy.random.<fn>
                inner = node.value
                if isinstance(inner.value, ast.Name) and inner.value.id in {"np", "numpy"} and inner.attr == "random":
                    violations.append(f"{py.relative_to(TLC)}:{node.lineno}: module-level numpy.random.* — use a seeded Generator")
    assert not violations, "\n".join(violations)


def test_holdout_not_imported_by_tlc():
    """holdout/ is a separate, import-guarded package (spec §7.1.2); tlc must never read it."""
    violations = []
    for py in sorted(TLC.rglob("*.py")):
        tree = ast.parse(py.read_text(), filename=str(py))
        for mod in _imports(tree):
            if mod.split(".")[0] == "holdout":
                violations.append(f"{py.relative_to(TLC)}: imports holdout")
    assert not violations, "\n".join(violations)
