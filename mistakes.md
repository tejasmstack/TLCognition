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

## M-010 · The Gate 4 finding's diagnosis was wrong: the "real-texture phantoms" were P33's spot halos
**Symptom:** GATE4_FINDING.md attributed the pooled-FP failure to "coherent structure in real
texture ... decidable only by human adjudication". The independent adversarial reviewer mapped
every textured-blank phantom back to tile coordinates: at a >= 0.5, 102/102 sit in tile rows
89-98 or 147-156 (uniform expectation 1.85 per 5-row bin) — P33 rectified rows ~168 and ~226,
where P33 carries a row of four broad spots and a diffuse lane-1 spot whose halos extend into
every gutter (residual dips to -26 MAD at the plate's right edge). Mirror pairs (e.g. seed 8014
rows 69.96/206.63) were the same feature and its reflection.
**Wrong hypothesis:** "gutters are chemistry-free by construction" (A-017). False for broad spots
and halos; the ±0.275-pitch+2 px gutter is inside the halo footprint. Also 7 of 42 tile columns
were od_valid=False constants, lightening the S1 null further.
**Actual cause:** a null built from real data was used without a pre-registered signal-free
screen — M-009's lesson, not learned the first time.
**Fix (attempt 3, spec-consistent, no contract weakening):** the tile is built with a
PRE-REGISTERED screen: drop invalid columns; smooth each column at the nominal FWHM; drop every
row where any column's smoothed residual exceeds 4 MAD (with a 2-FWHM margin); keep the largest
remaining contiguous block (>= 60 rows); record what was excised in the evidence. Second: the
agreement scale is corrected (D-015). Third: the 20-sigma miss at seed 9021 is investigated.
**Test added:** the battery evidence now records the tile screen (rows/columns excised, the
per-column max |smoothed residual| in MAD) so a contaminated null is visible, not inferred.
**Lesson:** when a null and a detector disagree, locate the disagreement in the null's own
coordinates before theorising about the detector; and "by construction" claims about real
data are hypotheses until measured.

## M-011 · Rectified frame was one pixel too small (0.5% scale error), biasing every position
**Symptom:** Gate 5 run 1: detected origin rows were 0.4-1.7 px ABOVE truth on every one of 60
synthetic plates (median -0.9), anchors -0.7 px, and Rst errors clustered just over 0.01.
**Wrong hypothesis:** origin-dot centroiding bias.
**Actual cause:** geometry.rectified_size returned round(corner distance) as the pixel COUNT.
Corners sit at pixel centres 0 and w-1 (distance w-1), so the rectified grid should have w
pixels, not w-1; mapping the corners to (0, W-1) with W = w-1 compresses the plate by (w-2)/(w-1)
and the error grows linearly with row: ~0.85 px at row 170 of 200.
**Fix:** rectified_size = round(distance) + 1 for both axes. Gate 2 evidence re-generated
(corner recovery is in source coordinates and unaffected; idempotency unchanged).
**Test added:** tests/test_geometry.py asserts a synthetic plate's rectified shape equals its
plate size and that a ground-truth row maps to the same row in the rectified frame within 0.3 px.
**Lesson:** pixel-centre vs pixel-edge conventions bite twice (M-005 was the generator side);
every resampling boundary needs an explicit round-trip test against ground truth.

## M-012 · The null screen was blind at its own band edge; a mirror seam manufactured the remaining textured phantoms
**Symptom:** final Gate 4 review: 16 of 18 textured phantoms at a >= 0.55 map to tile rows 0-4
(the mirror seam), 2 to rows 40-43; uniform expectation ~2 per 10-row bin.
**Wrong hypothesis:** A-017 second amendment called the remaining textured FP "a measurement of
real texture under an honest null".
**Actual cause:** cleanest_real_region scored rows inside the 0.20h-0.82h band with a running
median detrend in "nearest" mode, so at the band's first rows the detrend reproduces the signal
and the score is identically 0 — the "longest clean run" therefore always started at the band
edge (row 56 for BOTH candidate plates), and the tile's row 0 carried a -2.5 sigma row-mean dip
the screen structurally could not see. Mirror tiling then doubled that dip into a spot-shaped
feature. Also: the rule itself was fixed after the 4-MAD per-column screen failed on P33 (so
"pre-registered" was overstated), and its 3.0-MAD threshold is knife-edge (2.5 -> no region;
3.5-4.0 -> a PER-P19 region). Also: the battery cache keyed on seed only, so evidence generated
just before the M-011 geometry fix (tile 93x132) was reused instead of the HEAD tile (94x133).
**Fix:** score over the whole rectified height, discard >= 2.5 FWHM at each detrend edge before
choosing runs, require the tile's own row-mean profile to lie within 3x its iid expectation,
record the threshold sensitivity in the evidence; cache keyed by a code fingerprint so any
pipeline/generator change invalidates it; every "pre-registered" claim replaced by the actual
history of the rule.
**Test added:** the evidence now records the tile's row-mean max in iid-sigma units and the
phantom tile-row histogram, so a seam artefact is visible in the artifact itself.
**Lesson:** a screen must be checked in ITS OWN output coordinates (does it score every row it
can select?) before its verdict is trusted; and "pre-registered" is a claim about history, not
about intent.

## M-013 · No region of a reaction plate is blank at the ensemble's sensitivity
**Symptom:** with seams removed (crop-mode textured blanks) the rule-v3 region of PER-P19 still
yields ~1 phantom/plate at a >= 0.55 (z_med >> 10); P44 gave ~0.5. Every screening rule tried
(v1-v3) passed material the 24-config ensemble then found spots in.
**Wrong hypothesis (three times):** that a screen can certify a region of a real reaction plate
as chemistry-free.
**Actual cause:** a "blank band" screen is a spot detector with a threshold, and the ensemble is a
more sensitive spot detector than any screen normalised by the plate's own residual MAD (which
the faint chemistry itself inflates). On plates that were actually run, the corpus has no band
that is blank at the ensemble's sensitivity. This is not a bug in the screen; it is the nature
of the material.
**Fix:** the real-noise-texture variant of the null battery is declared NOT CONSTRUCTIBLE from
this corpus (D-019). The FP arm is measured on 200 synthetic-noise blanks (spec 05 §12.3 Null A,
>= 200 realisations); the textured family is retained as a labelled DIAGNOSTIC whose phantom
rate is an upper bound on real-texture phantoms, never pooled into the gate number. Real blank
plates (solvent-only, CAPTURE_PROTOCOL) are the only honest source of Null B — requested.
**Test added:** the battery evidence labels the textured family "diagnostic_not_null" and
records the phantom tile-row histogram.
**Lesson:** when three successive fixes to a null each fail the same way, the null's premise is
wrong; stop repairing and re-state what can be measured.
**Evidence for M-013 (crop mode, no seams):** 15 phantoms at a >= 0.55 over 16 textured blanks;
tile-row histogram {20-29: 6, 30-39: 9}, spread over lanes 0/1/3 — one row-coherent real feature
of the PER-P19 region, invisible to a 3-MAD screen normalised by the plate's own residual.

## M-014 · False streak flags hid mislocalised spots; degenerate EMG fits drove them
**Symptom:** independent Gate 5 review: 20 of 221 non-streak synthetic lanes (9%) flagged as
streaking, suppressing 33 real detections; re-scoring those detections lifts the position p95
from 0.0085 to 0.0117 — over the bound. The self-check had not measured the false-streak rate.
**Wrong hypothesis:** streak rules only needed to be sensitive enough (19/19 true streaks caught).
**Actual cause:** (1) the contiguous-run rule treats two ordinary spots ~20 px apart as one
> 2.5-FWHM run; (2) the tail-ratio rule took max(tau/sigma) over every tiered peak, and the
EMG fitter returned degenerate solutions on plain Gaussians (sigma 2.7, tau 37 -> tau/sigma 14,
mode 2.4 px off) — a lower-cost local optimum with no complexity penalty against the
4-parameter Gaussian.
**Fix:** fit_emg selects between an explicit Gaussian fit and the EMG multi-start by AIC
(n ln(SS/n) + 2k) — EMG must earn its extra parameter; streak run rule ignores runs that
contain >= 2 tiered peaks unless the plateau rule also holds; tail rule uses only the dominant
peak and only when its fit is non-degenerate (sigma >= 0.5 sigma_nom); Gate 5 evidence reports
the false-streak-lane rate and scores every non-streak-lane detection. D-017 threshold set to
1 FWHM (the value its own context cited), no longer 2.
**Test added:** tests/test_phase5.py — a clean Gaussian profile must fit as gaussian (no tail
ratio > 3); two adjacent spots must not be flagged as a streak; gate5 evidence carries
`false_streak_lanes`.
**Lesson:** every suppression path needs its own false-positive metric in the gate evidence;
a gate that only counts true positives of a suppressor is measuring half of it.
