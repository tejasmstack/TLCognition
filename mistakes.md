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

## M-005 · Generator ground-truth corners were 1 px outside the rendered plate
**Symptom:** Phase 2 corner recovery showed a floor of exactly sqrt(2) px error at tilt 0 — a
(1,1) systematic offset, not noise. Separately, tilt >= 6 gave 35-55 px errors with measured
tilt 0.00.
**Wrong hypothesis:** the corner detector's line fits were imprecise.
**Actual cause:** two independent bugs. (1) GroundTruth corners used the pixel-EDGE convention
([[0,0],[w,0],[w,h],[0,h]]) while the renderer paints pixels whose centres satisfy
0 <= x <= w-1 — the truth claimed corners ~1 px outside the physical plate. Gate 1's reviewer
corner check (bright 4 px inside / dark 4 px outside) was too coarse to catch it. (2) The
detection guard for "plate fills the frame" keyed on hue-mask coverage alone; the corrected teal
background (D-006) shares the hue band, so on tilted scenes the guard kept the full-frame hue
mask and the "plate" became the whole image, axis-aligned.
**Fix:** (1) generator corners/rotation centre moved to the pixel-centre convention
([[0,0],[w-1,0],...], centre ((w-1)/2,(h-1)/2)); test updated to expect side length w-1/h-1.
(2) guard now keys on Otsu class-mean contrast: the brightness split is applied only when
mean(bright)/mean(dark) >= 1.35 — a full-frame plate splits its own illumination gradient at
ratio ~1.1, a plate-vs-bench split sits ~2.
**Test added:** Gate 2 corner-recovery sweep across tilt 0-12 (scripts/gate2_check.py) plus
tests asserting sub-0.5 px recovery at representative tilts.
**Lesson:** a ground truth is code too — it can be wrong in ways a generous verification
tolerance hides; and any guard keyed on a scene statistic must be re-derived when the scene
model changes (D-006 changed the background and silently broke the guard's premise).

## M-006 · EMG rendering produced NaN plates; three synthetic detections "failed"
**Symptom:** Gate 2 sweep: 3 of 60 synthetic plates undetectable, with RuntimeWarnings
(invalid value in multiply / cast) from the generator.
**Wrong hypothesis (briefly):** the detector failing on unusual specs.
**Actual cause:** the EMG profile used erfcx((k−z)/√2)·exp(−z²/2); far down the tail the erfcx
argument is very negative and erfcx(x) ~ 2·exp(x²) overflows to inf, giving inf·0 = NaN, which
poisoned the whole OD field and the emitted image.
**Fix:** piecewise evaluation via the exact identity erfcx(x) = 2·exp(x²) − erfcx(−x): below
x = −20 the profile is the pure exponential tail 2·exp(k²/2 − k·z). No approximation error at
the switch point beyond float rounding.
**Test added:** Gate 2 sweep re-run covers it (EMG specs among the 60); plus the generator's
determinism tests would now catch NaN (array_equal fails on NaN).
**Lesson:** special functions blow up exactly where a physical tail is "obviously negligible";
evaluate tails in log/asymptotic form from the start.

## M-007 · Rolling-ball background destroyed the signal on float images (sigma read as 0)
**Symptom:** the Gate 3 sigma-stability grid returned sigma = 0.0 for rolling_ball at radii
12-35: the member's OD residual had literally zero median absolute difference.
**Wrong hypothesis:** median/rolling-ball edges would INFLATE the difference-based sigma.
**Actual cause:** skimage's rolling_ball couples the ball's intensity radius to its spatial
radius in the image's own units. On a [0,1] float image a radius of 12-55 intensity units is
enormous relative to the 0-1 range, and the envelope hugs every noise pixel: I0 == g almost
everywhere, OD identically 0 — background model eating ALL signal, exactly the failure mode
Gate 3's monotonicity property exists to catch, found here by the sigma probe instead.
**Fix:** run the ball in the ImageJ 8-bit convention (scale to 0-255 grays, radius in grays ~ px),
which is what the eval's Fiji Analyze>Gels reference did. All 17 grid members now agree on
sigma within 3.5%.
**Test added:** sigma-stability assertion over the full model x radius grid in
tests/test_photometry.py (any member collapsing to 0 fails loudly).
**Lesson:** unit conventions of morphological operators are part of the algorithm; "the same
algorithm" in a different unit system is a different algorithm.

## M-008 · The 576-config sweep ran 10x slower than estimated and died with the session
**Symptom:** grid selection ran >16 min with no artifact; after a session fork the parent was
alive with no workers and exit code -1. Foreground timing: 352 s per spotted plate, 191 s per
blank (estimate had been ~30 s).
**Wrong hypothesis:** the sweep was progressing normally and just needed more wall-clock.
**Actual cause:** (1) every one of 576 configs recomputed its 2-D background fit although only
16 distinct (model, radius) fits exist — a 36x redundancy per lane; (2) matched_filter_scan
rebuilt each template's m x m covariance matrix with a Python list comprehension on every call
(31 calls per shared scan); (3) skimage rolling_ball at R=64 is O(N*R^2) on the full-res image;
(4) the background process's worker children did not survive the session fork, and the job
had no checkpoints, so all partial work was lost.
**Fix:** per-plate OD-field cache keyed by (model, radius) threaded through the detector;
covariance matrices precomputed once per template set with vectorised lag indexing; rolling
ball run ImageJ-style on a shrunk image for R >= 16; sweep checkpoints each plate's result to
reports/grid_sweep_cache/ and skips finished plates on re-run.
**Test added:** timing assertion in the sweep log (per-plate seconds printed); determinism of
cached vs uncached detection asserted in tests/test_detection.py.
**Lesson:** estimate compute by counting the innermost call, not the outer loop; and any job
longer than a few minutes checkpoints to disk from the start.

## M-009 · The real-texture null was contaminated with real structure; Gate 4 attempt 1 failed
**Symptom:** Gate 4 battery: no operating point met both arms. Decomposed: synthetic blanks
0.00-0.07 phantoms/plate at a>=0.6, but real-texture blanks 0.90/plate with z_med 15-41 —
far too strong to be noise. Recall at a>=0.6 was 0.79 at 5 sigma and still only 0.92 at
20 sigma.
**Wrong hypothesis:** header ink bleeding into the chemistry zone of synthetic blanks.
Measured: handwriting-on synthetic blanks gave 0.07/plate — negligible.
**Actual cause:** (1) the P33 "empty band" tile (lowest-p95 eighth, full width, 36 rows) still
carried lane-ghost column structure (+/-1 sigma column means); mirror-tiling it every 36 rows
produced periodic, column-coherent bands at spot scale in every lane — real structure that
every config agrees on, so agreement could not suppress it. (2) Recall misses: spots on
clip=0.14 plates under clipped regions are unobservable by construction (recall 0.70 there vs
0.915 unclipped); and EMG truths (mu) sat 0.94-1.03 sigma_y from the detected intensity peak
with a matching tolerance of 0.94 sigma_y (the D-006 convention tension, now explicit).
**Fix:** tile from P33's gutter strips over the analysable band (no chemistry by definition,
163 rows so tiling repeats <= 2x — no spot-scale periodicity), verified noise-like at spot
scale; truth matching per spec 05 §12.6 (|dRst| <= 0.03, i.e. max(0.4 FWHM, 0.03 x migration
length)) with an observability filter (spot box >= 50% source-clipped => not observable, reported
separately, never counted as a miss); grid pruning tightened to recall >= 0.7 (D-012).
**Test added:** textured-null sanity check in the battery (mean phantoms on textured blanks
reported per family, so a contaminated tile is visible, not pooled away).
**Lesson:** a null built from real data must be shown to be signal-free BEFORE it is used to
judge a detector; "lowest-p95 band" is a heuristic for calibration statistics, not a proof of
emptiness.
