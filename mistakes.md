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
