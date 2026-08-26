"""Guard for M-002: tlc.core.determinism must be imported before any BLAS-loading library.

The determinism module sets OMP/OPENBLAS/MKL/VECLIB thread counts via environment variables,
which BLAS reads once at load time. If numpy (or anything that imports it) loads first, the
setting is a no-op and results become scheduling-dependent (spec 03 §7.2.2 item 1). A sorter
or refactor can silently break this — M-002 is the incident. This test makes the ordering a
machine-checked fact for every entry-point file.
"""

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BLAS_LOADERS = {"numpy", "scipy", "skimage", "imageio", "h5py", "pandas", "sklearn", "cv2"}

# Entry points: every script, plus package entry points as they appear.
ENTRY_GLOBS = ["scripts/*.py", "tlc/cli/main.py", "tlc/jobs/worker.py"]


def _import_order(path: Path) -> tuple[int | None, int | None]:
    """(line of first BLAS-loader import, line of determinism import) or None each."""
    tree = ast.parse(path.read_text(), filename=str(path))
    first_blas = None
    determinism = None
    for node in ast.walk(tree):
        mods: list[tuple[str, int]] = []
        if isinstance(node, ast.Import):
            mods = [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods = [(node.module, node.lineno)]
        for mod, line in mods:
            root = mod.split(".")[0]
            if root in BLAS_LOADERS and (first_blas is None or line < first_blas):
                first_blas = line
            if mod.startswith("tlc.core.determinism") and (determinism is None or line < determinism):
                determinism = line
    return first_blas, determinism


def test_determinism_imported_before_blas_in_entry_points():
    violations = []
    for pattern in ENTRY_GLOBS:
        for py in sorted(REPO.glob(pattern)):
            first_blas, determinism = _import_order(py)
            if first_blas is None:
                continue  # no BLAS-loading import at all
            if determinism is None:
                violations.append(f"{py.relative_to(REPO)}: imports BLAS-loading modules but never tlc.core.determinism")
            elif determinism > first_blas:
                violations.append(
                    f"{py.relative_to(REPO)}: tlc.core.determinism at line {determinism} "
                    f"AFTER a BLAS-loading import at line {first_blas}"
                )
    assert not violations, "\n".join(violations)
