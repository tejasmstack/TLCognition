# 12. Testing and evaluation strategy

> Scientific image pipelines fail in ways ordinary software tests do not catch. A pipeline can be
> 100% green on unit tests and still report five spots on a blank plate. This section exists to catch
> exactly that. It is binding.

## 12.1 The pyramid, and what each level cannot catch

| Level | What it tests | What it CANNOT catch | Runtime budget |
|---|---|---|---|
| Unit | pure functions: OD conversion, MAD, EMG evaluation, Rst arithmetic, schema validation | anything about whether the pipeline is scientifically right | < 20 s |
| Property / metamorphic | invariances that must hold with no ground truth | absolute accuracy | < 60 s |
| Synthetic ground truth | recovery of known spots under known defects | whether synthetic resembles reality | < 5 min |
| Null battery | false positives on plates with no chemistry | false negatives | < 5 min |
| Golden regression | drift on the real corpus | whether the golden values were ever right | < 3 min |
| Labelled-set evaluation | actual accuracy, actual calibration | anything outside the labelled distribution | nightly |
| VLM replay | prompt regressions, schema conformance, abstention paths | live provider drift | < 60 s |
| Live VLM probe | provider drift | — | nightly |

**The critical insight:** the first two levels can be fully green while the system is scientifically
worthless. Only the null battery and the labelled-set evaluation can tell you the system is right.
Never let CI be green on the first two alone — gate on the null battery from Phase 4 onward.

## 12.2 The synthetic plate generator (`tlc/synth/`)

Built in Phase 1, **before** the pipeline, so it cannot be unconsciously shaped to flatter it.

**Signature:** `make_plate(spec: PlateSpec, seed: int) -> (image_uint8, GroundTruth)`

`PlateSpec` fields and the ranges that reproduce the real corpus:

| Field | Range | Source |
|---|---|---|
| `size_px` | (71,130) … (400,760) | real corpus, plus a "good capture" band |
| `n_lanes` | 3–6 | 4 in the real corpus |
| `illum_swing` | 0.10–0.25 | measured 0.155 on plate7 |
| `illum_model` | random low-order 2-D polynomial + one off-centre Gaussian hotspot | matches the observed surface |
| `noise_sd` | ±20% of the real empty-band residual sd | measured |
| `noise_correlation_px` | 0.4–1.0 | JPEG/demosaic texture |
| `clip_fraction` | 0.0–0.60 | measured 14–60% on six plates |
| `tilt_deg` | 0–12 | measured 0.4–4.1, extended |
| `frame_overrun` | none / top / bottom / both | 4/7 real plates |
| `spot_shape` | `gaussian` \| `emg(tau)` \| `streak` \| `halo` | F11 |
| `spot_amplitude_sigma` | 1–30 (in units of the plate noise) | spans sub-threshold to saturated |
| `handwriting` | none / header / header+labels, with ink OD drawn from the measured 0.053–0.162 band | F7 |
| `front_line` | absent / drawn | absent in the whole real corpus |
| `empty_lanes` | list | F10 |

`GroundTruth` carries exact spot centres in rectified plate coordinates, exact amplitudes, exact
areas, the true origin row, the true front row (or `None`), and the true lane centres.

**Gate 1 acceptance (repeat of §6):** illumination swing inside the observed range; residual noise sd
within ±20% of the real empty-band value; the clipping knob reproduces 14–60%. Commit a
three-synthetic-vs-three-real side-by-side image.

**Trap to avoid:** do not generate spots only at amplitudes the pipeline can find. The amplitude
sweep must go below the detection floor, because measuring where recall collapses is the point.

## 12.3 The null battery — CI-gating, not an anecdote

Two independent nulls, both from the prior evaluation, both wired into CI.

**Null A — synthetic.** Fitted illumination surface + noise resampled from a real empty band,
lightly correlated. ≥ 200 realisations.

**Null B — real texture.** The actual residual field from a blank band of a real plate, tiled
mirror-wise over the illumination surface, at ≥ 13 independent phase shifts. This one cannot be
accused of getting the noise statistics wrong, because it *is* the plate's own noise.

**The gate (Phase 4):** at the shipped operating point,

```
blank_plate_false_positives_per_plate <= 0.2      (both nulls, mean)
AND recall_on_synthetic(amplitude >= 5 sigma) >= 0.95
```

Both, simultaneously. Reference point for how hard this is: the naive pipeline at R=35 px, 3σ scores
**5.5** false spots per blank plate with 100% of blanks affected. You are being asked for a ~27×
improvement, and the ensemble plus calibration is how you get it.

**If both cannot be met at any operating point:** that is a finding, not a failure. Emit the full
ROC-style curve of FP-per-blank against recall, write it into `EVALUATION.md`, and stop for a human
decision. Do not pick a point silently.

**Regression policy:** any commit that raises FP-per-blank above the gate fails CI. The number is
printed in every CI run so drift is visible before it crosses.

## 12.4 Metamorphic and property tests

These need no ground truth and catch the largest class of real bugs. Use Hypothesis for the
generative ones.

| Property | Assertion | Tolerance | Catches |
|---|---|---|---|
| Exposure invariance | `Rst(k·I) == Rst(I)` for k ∈ [0.7, 1.3], on unclipped synthetic | ≤ 0.005 | forgetting the ratio; using linear difference |
| Rotation equivariance | rectified spot positions after a known rotation match the unrotated ones | ≤ 1.0 px to 12° | rectification bugs |
| Affine equivariance | apply a known projective transform, positions map back correctly | ≤ 1.0 px | corner-ordering bugs |
| Rectification idempotence | re-warping a rectified plate is a no-op | ≤ 0.5 px | double-correction |
| Amplitude monotonicity | recovered area is monotonic in synthetic amplitude | Spearman ρ > 0.98 | background model eating signal |
| σ stability | σ varies < 15% across background radii | — | **measuring σ post-masking (F4)** |
| Determinism | two runs, two machines, identical result hash | exact | unseeded randomness, dict ordering, thread nondeterminism |
| Lane-edge invariant | no detected lane centre within one half-width of the mask boundary | hard | the M-007 class of bug |
| Refusal totality | every input produces a result or a typed refusal | hard, 100% | silent nulls |
| Schema conformance | every output validates | hard, 100% | drift between code and schema |
| Streak suppression | every synthetic streak lane is flagged and unquantified | 100% | fabricating a streak position |
| Clip gating | photometry is refused above the clip threshold | 100% | reporting areas from destroyed data |

**Two that matter more than they look:** *σ stability* is the direct test for F4 and will fail loudly
if anyone reintroduces post-mask noise estimation. *Determinism* must run on CI and on a second
architecture if available — floating-point and thread-order nondeterminism is the most common cause
of a "reproducible" system that is not.

## 12.5 Golden regression on the real corpus

Store expected outputs for every image in `dataset/` as JSON, with array fields reduced to hashes.

- Numeric tolerance: positions ± 0.3 px, Rst ± 0.002, confidence ± 0.01, counts exact.
- **A golden update is a reviewed act.** `make goldens` regenerates, but CI fails if goldens changed
  in the same commit as pipeline code unless the commit message contains `GOLDEN-UPDATE:` and a
  `decisions.md` entry references it. This is what stops a golden file from silently absorbing a
  regression.
- Every golden diff prints as a human-readable table of what changed, not a JSON blob diff.

## 12.6 The accuracy evaluation harness (`tlc/eval/`)

Runs against the **held-out** partition of the labelled set only. Nothing in the pipeline or the
calibrator may read it.

**Matching.** A predicted spot matches a labelled spot if they are in the same lane and
|ΔRst| ≤ 0.03. Justification: manual Rf reading between analysts agrees to ±0.02–0.05, so a tolerance
inside that band is not measuring anything real, and a wider one would merge adjacent bands. State
the tolerance in every reported metric; a precision figure without its matching tolerance is
meaningless.

**Metrics, each with a bootstrap interval and an explicit n:**

- Spot detection: precision, recall, F1 at the stated tolerance, computed separately for `confirmed`
  and `confirmed ∪ candidate`.
- Position error: full distribution, not just the mean — report median, p95, and max in Rst units.
- Area error: only on the subset where ground truth exists and the plate is unclipped. Expect this to
  be a small n and say so.
- OCR: character error rate and **field-level exact-match accuracy**, per field. Field-level is the
  number that matters — a sample ID that is 90% correct is 100% useless.
- Calibration: ECE, MCE, Brier, reliability diagram.
- Refusal quality: of the plates the system refused, what fraction would a chemist also have refused?
  This is the metric that catches a system that achieves its FP gate by refusing everything.

**Cross-validation:** grouped by plate, always. Spots from the same plate are not independent; a
random split leaks and will overstate accuracy by a wide margin.

**Reporting honesty:** every metric carries n and an interval. Any metric that cannot be computed for
lack of labels is listed as `not_computed` with the reason, never omitted. At n=30 plates, be explicit
that a precision estimate has a 95% interval roughly ±0.15 wide — write that sentence into
`EVALUATION.md` rather than letting a reader assume three significant figures are meaningful.

## 12.7 Testing the VLM layer

- **Replay is the default.** Every test runs against cached responses keyed by
  `(image_hash, prompt_version, model_id, sample_index)`. `pytest` with the network disabled must
  pass in full. Enforce this with a fixture that monkeypatches the HTTP client to raise.
- **Prompt regression suite.** A fixed set of images with expected structured fields; run on every
  prompt change. Any change to a prompt bumps `prompt_version` and invalidates its cache entries.
- **Adversarial inputs, all of which must produce a clean refusal, not an exception and not a
  confident answer:** a uniform blank image; a photograph of something that is not a plate; a plate
  with no writing at all; a plate photographed upside down; a heavily clipped plate; a duplicate of
  an image already in the cache.
- **Abstention path coverage.** Assert that `UNREADABLE` is reachable and returned for a deliberately
  illegible crop. If the model never abstains in testing, the enum is decorative.
- **Live drift probe, nightly.** Ten fixed images against the live endpoint; alert on any field-level
  change from the cached baseline. Providers update models without notice and this is the only way to
  find out.

## 12.8 CI plan

**On every commit** (target < 6 minutes): unit, property/metamorphic, schema, golden regression, VLM
replay, determinism check, and a reduced null battery (50 realisations). Print FP-per-blank and the
determinism hash in the run summary.

**Nightly:** full null battery (≥ 200), full synthetic sweep across the defect matrix, labelled-set
evaluation with intervals, live VLM drift probe, and a regenerated `EVALUATION.md`.

**Keeping it fast:** cache the synthetic corpus by spec hash rather than regenerating; run the
null battery with multiprocessing; keep real-image tests to the rectified arrays rather than
re-decoding JPEGs.

## 12.9 The gate-review protocol

Each phase gate in §6 is checked by an agent that did not write the code, working only from the
committed evidence. Its output is a pass/fail with the specific numbers, appended to a
`GATES.md` file. A gate that "passes" without committed numeric evidence has not passed.
