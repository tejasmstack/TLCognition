# Mistakes log

Append-only. One entry per thing that did not work: symptom first, written **when the problem is
hit, before the fix**. Format: M-NNN · title / Symptom / Wrong hypothesis (if any) / Actual cause /
Fix / Test added / Lesson.

---
## M-001 · Committed a red CI while claiming Gate 0 evidence
**Symptom:** independent Gate 0 review ran `./scripts/ci.sh` on HEAD and got exit 1 — ruff I001
(un-sorted import block) at `scripts/dataset_audit.py:16` — while the commit message claimed
"CI green".
**Wrong hypothesis:** none; the cause was immediate on inspection.
**Actual cause:** CI was run once, early, before `scripts/dataset_audit.py` existed; the script
was then written and edited (rglob/series/inventory-hash changes) and committed without re-running
CI. "CI was green when I last ran it" is not "CI is green at HEAD".
**Fix:** lint fixed; CI re-run at the new HEAD before amending the gate claim; gate reviewer
re-confirms item 4. Process rule adopted: the commit that claims a gate runs `./scripts/ci.sh`
in the same shell session immediately before `git commit`, never from memory of an earlier run.
**Test added:** none needed — CI itself is the test; the reviewer protocol caught it.
**Lesson:** gate evidence must be produced at the commit boundary, not carried forward from an
earlier tree state. Also caught by the same review: files cited as evidence (`reference/*.json`,
`GATES.md`) were untracked — evidence a decision cites must be in the commit that cites it.

## M-002 · ruff --fix silently reordered the determinism import below numpy
**Symptom:** after `ruff check --fix` resolved M-001's I001, `import tlc.core.determinism`
("must precede numpy") sits at line 22, AFTER imageio/numpy/scipy/skimage — the BLAS
thread-count env vars are now set after BLAS has already loaded, i.e. they do nothing.
CI is green while the determinism contract is broken; the comment on the line still claims
the opposite.
**Wrong hypothesis:** that the lint fix was cosmetic and needed no re-inspection.
**Actual cause:** isort-style sorting has no notion of import-order side effects; first-party
`tlc` sorts after third-party imports by design. Any future auto-fix would do it again.
**Fix:** `# isort: split` fence after the determinism import so sorters cannot lift numpy above
it, plus a test (`tests/test_determinism_import_order.py`) that AST-walks every entry-point
script and fails if numpy/scipy/skimage/imageio is imported before `tlc.core.determinism`.
**Test added:** `tests/test_determinism_import_order.py` — guards every file under scripts/ and
the future worker/CLI entry points.
**Lesson:** an auto-fixer is a code author like any other and its diff gets reviewed like any
other; order-sensitive imports need a machine-checked guard, not a comment.

## M-003 · numpy rng.choice silently truncated enum values to 8-char strings
**Symptom:** `random_spec` crashed with `ValueError: np.str_('SpotShap') is not a valid SpotShape`.
**Actual cause:** `rng.choice([...enums...])` converts the list to a numpy fixed-width string
array (dtype '<U8' from the first element's str value), truncating "SpotShape.GAUSSIAN"'s value
and corrupting every member. numpy's implicit dtype coercion, not the enum, is the hazard.
**Fix:** draw integer indices (`rng.choice(n, p=...)`, `rng.integers`) and index a Python tuple.
**Test added:** covered by `test_random_spec_deterministic_and_in_range` (the failing test).
**Lesson:** never pass non-numeric Python objects to numpy RNG selection functions; draw indices.

## M-004 · Pipe through tail masked a red CI; gate commit landed anyway
**Symptom:** the Phase 1 commit chain `./scripts/ci.sh | tail -4 && git commit` committed while
ruff had 2 errors (B905, zip without strict= in scripts/gate1_check.py) — the pipeline's exit
status is tail's, not ci.sh's, so && proceeded.
**Wrong hypothesis:** M-001's process rule ("run CI in the same shell immediately before commit")
was sufficient. It was followed — and defeated by shell semantics.
**Actual cause:** POSIX pipeline exit status. Any `ci.sh | filter && commit` construction is
broken without pipefail.
**Fix:** `set -euo pipefail` was already inside ci.sh (its own steps are safe); the invoking
chain must never pipe it: run `./scripts/ci.sh` bare, then commit as a separate command after
seeing it pass. Lint errors fixed; phase commit amended before any push.
**Test added:** none applicable — the rule is about how commits are made.
**Lesson:** a process rule that depends on shell discipline should be made structurally
un-bypassable; from now on the gate-commit sequence is two separate tool calls: (1) bare ci.sh,
(2) commit only after reading its exit.
