# CONFIDENCE AND CALIBRATION SPECIFICATION
## TLC Plate Readout System — Mstack Chemicals
### Normative spec. Version 1.0. Written against the 7-plate evaluation findings.

---

## 0. THE CONTRACT

Six rules that govern every number this system emits. Everything below is an elaboration of these.

**C1 — A confidence is a probability that a named, falsifiable proposition is true.** Not a score, not a similarity, not a fit quality. Every confidence field in the JSON must have a one-sentence proposition attached in the schema docs, and that proposition must be checkable by a chemist looking at the plate. If you cannot write the proposition, delete the field.

**C2 — No probability ships without a calibrator artifact.** Every `p_*` field traces to a versioned calibration artifact with a recorded `n`, `n_groups`, out-of-fold Brier score and calibration error with CI. If the artifact is missing, stale, or its feature hash doesn't match, the runtime emits `{"value": null, "status": "uncalibrated"}` — never a number. This is enforced at the serialization layer, not by convention.

**C3 — Point estimate + interval, always, for continuous quantities.** An Rst without an interval is not shipped.

**C4 — Abstention is a first-class answer.** It has its own representation, its own reason code, and its own remediation string. Refusing is cheaper than being wrong: a chemist who re-shoots a plate loses 90 seconds; a chemist who trusts a phantom spot loses a week.

**C5 — Never emit `Rf`.** There is no solvent front on any plate (finding 2). The field must be *absent from the schema*, not null. Rst is the deliverable. If there is no standard lane, emit `distance_from_origin_mm` with a `no_reference` flag — do not silently substitute a convention.

**C6 — Confidence is capped by the label ceiling.** The system may not emit a probability higher than the measured inter-annotator agreement on that claim type. If two chemists agree on spot existence with κ = 0.72, the system does not emit p = 0.99 for spot existence; it emits `min(p_cal, p_ceiling)` and records `capped: true`.

**Naming convention.** `p_<claim>` = calibrated probability in [0,1]. `s_<claim>` = raw uncalibrated score, internal only, never rendered in the UI or returned in the public API. `ci90_<quantity>` = 90% interval. `q_<claim>` = ordinal band (`low`/`medium`/`high`) used only where C2 forbids a probability.

---

## 1. CLAIM INVENTORY — WHAT NEEDS A CONFIDENCE

Every row is a proposition the system asserts and could be wrong about. "Operational meaning" is what a probability of 0.8 means to a chemist reading it — this text goes in the API docs and, abbreviated, in the UI tooltip.

| # | Claim | Field | Proposition (falsifiable) | Operational meaning of p = 0.8 | Estimator | Label source |
|---|---|---|---|---|---|---|
| 1 | Plate detected | `p_plate_detected` | A TLC plate occupies the returned quadrilateral | Of 100 images given this score, ~80 contain a plate at that location | Detector score → beta calibration | Human, trivially labelled, n = all images |
| 2 | Plate fully in frame | `p_plate_complete` | No plate edge is cropped by the image boundary | ~80 of 100 such plates have all four physical edges inside the frame | Margin-to-border in rectified coords + edge-continuity score → logistic | Human. **Base rate is bad: 4/7 plates cut off** |
| 3 | Rectification valid | `p_rect_valid` | Homography maps plate to rectangle with residual < 1% of plate width | ~80 of 100 rectifications are metrically usable for Rst | Reprojection RMS, corner condition number, aspect prior → logistic | Synthetic warp of known plates (unlimited labels) |
| 4 | Exposure adequate | `p_exposure_ok` **and** `clip_fraction` | Green-channel clipping < 0.5% within all lane bands, and OD is invertible | — | **Deterministic, not probabilistic.** `clip_fraction` is measured, not inferred | None needed |
| 5 | Lane count | `p_lane_count` | The plate has exactly *k* lanes | ~80 of 100 plates given k at 0.8 really have k lanes | VLM self-consistency over label row (§7) + signal grid as a *feature only* (finding 10) | Human |
| 6 | Lane position (each) | `p_lane[i]`, `ci90_lane_center[i]` | Lane *i*'s center is within ±3% of plate width of the returned x | — | Grid fit residual + VLM label anchor + config ensemble spread | Human, on rectified image |
| 7 | Origin row | `p_origin`, `ci90_origin_y` | The application line is at the returned y | ~80 of 100 are within the stated interval | Ensemble of origin cues (application streak, plate-bottom offset prior, VLM) → logistic | Human. **Dominates Rst variance — see §5.4** |
| 8 | Front row | — | — | — | **PERMANENTLY ABSENT.** Hard-coded abstention, reason `NO_SOLVENT_FRONT`. Not a low confidence — an absent field | — |
| 9 | Spot exists | `p_spot` (+ `p_interval` from Venn–Abers) | A chemist adjudication panel would mark a real analyte zone at this lane and position | ~80 of 100 spots reported at 0.8 are real; ~20 are phantoms | §2, full pipeline | Adjudicated human panel + blank plates + synthetic injection |
| 10 | Spot position | `rst`, `ci90_rst`, `rst_variance_budget` | True Rst lies in the interval | Interval has 90% coverage over new plates | §5 | Adjudicated + synthetic injection |
| 11 | Spot area | `area_rel`, `ci90_area_rel`, `area_status` | Integrated relative area within lane lies in the interval | — | §5.5. **Suppressed whenever `clip_fraction_box > 0`** | Synthetic injection only |
| 12 | Cross-lane correspondence | `p_same_compound[i][j]`, `delta_rst`, `ci90_delta_rst` | Spot in lane *i* and spot in lane *j* are the same compound | ~80 of 100 such pairs are the same compound — **never rendered as "is", always "consistent with"** | ΔRst test with reference-cancellation (§5.4) + profile shape correlation + co-spot resolution test | Known-composition co-spot plates (§3.7) |
| 13 | Sample ID text | `sample_id`, `p_sample_id`, `char_confidence[]` | The exact string, character for character | ~80 of 100 IDs at 0.8 are exactly right | §7 VLM self-consistency. **OCR is disabled entirely (finding 8)** | Human transcription |
| 14 | Lane label (each) | `lane_label[i]`, `p_lane_label[i]` | Lane *i* is labelled S / co / R / sd / blank / other | — | §7, closed vocabulary | Human |
| 15 | Annotation band location | `annotation_band`, `p_annotation_band` | Header ink lies entirely above the returned y | ~80 of 100 plates have no chemistry above that line | VLM + fixed geometric prior. **Never a ridge/sharpness detector (finding 7)** | Human |
| 16 | Streaking / non-Gaussian shape | `streak_index`, `shape_class` | Lane shows comet tailing, T-streak or halo | — | EMG asymmetry τ/σ, lane-width variance. **Gates area reporting, not a confidence** | Human |
| 17 | Plate usable overall | `p_plate_usable`, `verdict` | The plate yields at least one reportable spot at the stated FDR | — | Aggregate gate, §6 | Derived |
| 18 | Reaction conclusion | `conclusion`, `p_conclusion` | "SM consumed" / "product formed" / "inconclusive" | ~80 of 100 such calls agree with the chemist's own read | Composed from #9, #10, #12 by explicit rule (§5.6), never by a model | Human |

**Two claims that must NOT get confidence scores because they are measurements, not inferences:** `clip_fraction` (count the 255s) and `resolution_px_per_lane`. Reporting a "confidence" on a measured quantity is a category error and trains users to distrust the real ones.

---

## 2. CALIBRATED SPOT-EXISTENCE CONFIDENCE

This is the core of the system. Finding 3 says the naive pipeline invents 5.5 spots per blank plate; finding 6 says ensemble agreement caps at 0.78. Both must be fixed, and the second is largely an artifact of the first.

### 2.1 Fix the noise unit first — the ensemble is worthless on a circular σ

Finding 4 is fatal to any threshold expressed in σ: σ measured on the post-background residual varies 3.6× with the background radius, so "3σ" means a different physical thing in every config. An ensemble over configs whose thresholds all move with the config is not measuring detection robustness, it is measuring the σ estimator.

**Define σ once, at a fixed reference scale, independent of the background model.**

```
1. Build a spot-exclusion mask M: dilate all candidate zones from a permissive
   pre-pass (2σ, R=16) by 1.5× the nominal spot radius, plus the annotation band,
   plus lane gutters ±2 px, plus a 4 px border ring.
2. High-pass the plate at a FIXED scale: r = I - G_{σ=6px} * I   (σ=6 px fixed,
   NOT tied to the background radius).
3. Estimate the noise autocovariance C(Δ) from r restricted to ¬M, using the
   robust biweight midcovariance, out to |Δ| = 24 px.
4. σ0 = sqrt(C(0)).  Also retain the normalized autocorrelation ρ(Δ) = C(Δ)/C(0).
```

Every detection statistic is then a **matched-filter amplitude expressed in units of the filter's own noise**:

```
For template k (EMG-shaped, unit-peak):
    A     = Σ_i k_i · r_i / Σ_i k_i²                    (matched-filter amplitude)
    σ_A²  = ( Σ_ij k_i k_j C(i−j) ) / (Σ_i k_i²)²       (correlated-noise variance)
    z     = A / σ_A
```

`σ_A` uses the *full autocovariance*, not `σ0² Σk²`. Because the residual is spatially correlated (smoothing, JPEG, optics), the white-noise formula understates `σ_A` by a factor of typically 1.5–2.5 at these scales — which is roughly the 1.68× that finding 4 attributes to masking. **This single correction removes most of the circularity.** `z` is now comparable across background radii, and the ensemble measures what you want it to measure.

Log both `σ0` and the variance inflation factor `VIF = σ_A² / (σ0² / Σk²)` per plate. If VIF > 4, the image is dominated by structured artifacts, not noise — flag `NOISE_STRUCTURED` and tighten thresholds (§6).

### 2.2 The per-config detection statistic, with a defensible null

Do **not** threshold at "3σ" or "5σ". Two nulls, used together:

**(a) Analytic floor — random field theory (look-elsewhere).** For a 1-D smoothed Gaussian field, the expected number of upcrossings of level *u* is

```
E[N(u)] = R₁ · (4 ln 2)^{1/2} / (2π) · exp(−u²/2)  =  0.265 · R₁ · exp(−u²/2)
R₁ = resel count = (lane length in px) / (FWHM of the smoothing in px)
```

With a typical R₁ = 20 resels/lane × 6 lanes = 120: `E[N] = 31.8·exp(−u²/2)`. Controlling family-wise error at 0.05 per plate needs **u = 3.59**, and u = 5 would predict **one false plate in ~8,400**.

Observed: 5σ still yields a detection on **69%** of blank plates (finding 3). The discrepancy is a factor of ~5,800. **This is the single most important diagnostic in the whole system:** the residual is emphatically not a Gaussian random field. Heavy tails from glare gradients, plate texture, JPEG blocking and handling marks dominate. Therefore RFT is used only as a *sanity floor* (`u_rft`) and a monitoring statistic; it never sets the operating threshold.

**(b) Operating null — empirical, per plate.** Generate N = 200 null realizations of *this plate* and run the *identical* pipeline on each:

| Surrogate | Method | Preserves | Breaks |
|---|---|---|---|
| S1 Gutter transplant | Tile lane band from inter-lane gutter strips of the same plate, flipped/rolled | Local texture, illumination, clipping | Real spots |
| S2 Phase randomization | IAAFT (Schreiber–Schmitz) on the lane band, 100 iters | Power spectrum + amplitude distribution | Phase-coherent structures = spots |
| S3 Vertical roll | Circular shift of the lane densitogram by a random amount ≥ 3 FWHM, wrapped | Everything | Peak *positions* only — weaker null, use for position-specificity |
| S4 Synthetic blank | Existing machinery from finding 3 | Noise texture | Everything else |

Use S1 and S2 as the primary null (100 each), S4 as an independent check. For each surrogate, record the **maximum** `z` per lane (for FWER-style per-lane p-values) and the **full set** of local-maximum `z` values (for FDR).

```
p_peak = ( 1 + #{null peaks with z_null ≥ z_obs} ) / ( 1 + #{null peaks} )
```

The `+1` in both places is the standard Davison–Hinkley Monte Carlo correction; it bounds p away from 0 and makes the test exact at N = 200 down to p = 1/201 ≈ 0.005. Do not report p smaller than 1/(N+1); report it as `< 0.005` and let the calibrator handle the rest via `z`.

**Multiplicity within a plate:** apply Benjamini–Hochberg across all candidate peaks on the plate at q = 0.10 for the *candidate* list. Peaks on a smoothed field are positively dependent (PRDS plausibly holds), so plain BH is appropriate; if `VIF > 4` or the surrogate null shows negative peak-to-peak correlation, fall back to Benjamini–Yekutieli (`p_(i) ≤ i·q / (m · Σ_{j=1}^m 1/j)`), which at m = 20 costs a factor of 3.6.

### 2.3 Designing the config grid so agreement means something

Finding 6 ran 4 radii × 4 background models × 2 thresholds = 32. The failure mode of such a grid is that the runs are near-duplicates, and agreement then measures redundancy rather than robustness.

**Quantify the redundancy before trusting it.** For binary detection vectors `d_c ∈ {0,1}^P` over all candidate positions P, across the dev set, compute the mean pairwise phi-correlation ρ̄ and the effective number of independent configs:

```
K_eff = K / (1 + (K − 1) · ρ̄)
```

At K = 32 and ρ̄ = 0.8 this is **K_eff = 1.24**. Thirty-two runs carrying the information of one and a quarter. Report `K_eff` in every result JSON; if `K_eff < 4`, the agreement fraction must not be used as the primary evidence and the system falls back to `z`-driven confidence.

**The grid axes, chosen for decorrelation, not for enumeration:**

| Axis | Levels | Rationale |
|---|---|---|
| Background radius R | 8, 16, 32, 64 px, **log-spaced and required to be ≥ 2× and ≤ 8× the nominal spot FWHM** | Radii within a factor of 1.5 of each other produce ρ > 0.95; a linear grid wastes the ensemble |
| Background model | (a) morphological opening / rolling ball, (b) running-median, (c) asymmetric least squares **arPLS** (λ = 10⁵), (d) 2-D Legendre surface, order 3 | These four have genuinely different failure modes: (a) under-fits ramps, (b) clips broad bands, (c) over-flattens shoulders, (d) rings at edges |
| σ / noise-unit variant | masked-MAD, unmasked-MAD, gutter-only, autocovariance-full | **Must be an axis** — finding 4 shows it moves the threshold 1.68×. Freezing it hides a real uncertainty |
| Densitogram extraction | column mean, column median, trimmed mean (20%) | Cheap, decorrelating, and directly probes streak sensitivity |
| Peak model | EMG, bi-Gaussian, none (raw local max) | Finding 11 |

That is 4 × 4 × 4 × 3 × 3 = 576 combinations. **Do not run all of them.** Select K = 24 by greedy max-min diversity on the dev set:

```
1. Run all 576 on the dev plates once, offline. Record detection vectors.
2. Discard any config with, on the labelled dev set:
     recall on adjudicated-true spots < 0.50, OR
     false peaks per blank plate > 1.0 at its own operating point.
   → This is where Kubelka–Munk exits (finding 5: 9 spots vs 2). A broken method
     counted as "disagreement" manufactures a low agreement ceiling. Exclusion
     must be by measured performance and recorded in the artifact, not by taste.
3. From the survivors, greedily pick K=24 maximizing min pairwise Hamming
   distance of detection vectors (farthest-point / k-center greedy).
4. Freeze the 24 as CONFIG_GRID_v1, hash it, store in the calibration artifact.
```

**Weighting.** Give config *c* weight `w_c ∝ 1 / (1 + Σ_{c'≠c} ρ_{cc'})` so a cluster of three near-identical survivors counts roughly once. Weighted agreement:

```
a = ( Σ_c w_c · 1[config c detects a peak within τ of position y] + α ) / ( Σ_c w_c + α + β )
τ = 0.4 × nominal spot FWHM      α = β = 0.5   (Jeffreys shrinkage, keeps a off 0 and 1)
```

### 2.4 Combining agreement with signal — one feature vector, one calibrator

Agreement alone is a low-resolution, saturating, bounded statistic. Combine it with continuous evidence. Feature vector per candidate peak:

```
x1  logit(a)                          weighted, shrunk ensemble agreement
x2  z_med                             median matched-filter z across the 24 configs
x3  z_min                             worst-case z (robustness to background model)
x4  −log10(p_Brown)                   Brown's method combination of the per-config
                                      Monte-Carlo p-values, with effective df from
                                      the config correlation matrix (Fisher's method
                                      is WRONG here — configs are correlated)
x5  sd_position                       SD of fitted center across configs, in Rst units
x6  fwhm / fwhm_lane_median           relative width (phantoms are narrow or absurdly broad)
x7  |tau/sigma|                       EMG asymmetry (comet tailing = real overload; also
                                      a shape prior against noise ripples)
x8  clip_fraction_box                 clipping inside the peak box + 3 px margin
x9  d_annotation                      distance to annotation band, in plate heights
x10 d_lane_edge                       distance to lane boundary (edge artifacts)
x11 vlm_proposed                      1 if a VLM band proposal falls within τ (§7.6)
x12 streak_index_lane                 lane-level tailing metric
x13 log(px_per_lane)                  resolution
```

Guardrails on model capacity: with n ≤ 300 labelled spots, fit **L2-regularized logistic regression on at most 6 features** selected by grouped-CV forward selection from the list above (start from x1, x2, x4, x8). Above n = 1,000 spots, gradient-boosted stumps (depth 1, 200 rounds, lr 0.05) may be used, but must then be re-calibrated on top (a boosted model's outputs are not probabilities). Below n = 100, use x1 and x2 only, with a fixed-intercept logistic — anything richer will overfit and produce confidently wrong probabilities, the worst possible failure.

### 2.5 The 0.78 ceiling — what it is and what to do about it

Three separate things produce it. Diagnose which, and treat each differently.

1. **Broken configs in the grid.** Kubelka–Munk reporting 9 spots where others report 2 means it *disagrees on the true spots too*. Removing measured-bad configs (§2.3 step 2) should raise the ceiling substantially. Do this first and re-measure. Report the ceiling before and after in the artifact.
2. **Genuine marginality.** Finding 5 says strong spots agree to 0.3 px; what differs is marginal features. If after pruning the ceiling is still ~0.8 on features that a chemist unambiguously calls real, the grid contains configs that are *systematically blind* to real spots (e.g. R = 8 px destroys broad diffuse bands). Those are also measured-bad and should exit.
3. **The ceiling as a legitimate signal.** Some residual ceiling is correct and informative: this data really is marginal.

**Do not interpret a = 0.78 as p = 0.78 under any circumstance.** The whole point of the calibration map is that the link function is *fitted*: it is entirely possible and expected that the fitted map sends a = 0.78, z_med = 6.1 → p = 0.96, and a = 0.78, z_med = 2.4 → p = 0.31. The ceiling costs you *resolution at the top* — with a ∈ [0, 0.78] you cannot distinguish "unanimous" from "near-unanimous" — which is exactly why x2/x4 (continuous, unbounded) must be in the model.

**Hard rule:** if, on the pooled out-of-fold dev set, no spot receives `p_spot > 0.9`, the system does not have a high-confidence regime and the UI must not display one. Cap the top band at whatever the data supports and say so: *"Highest confidence achievable on this image quality: 0.86."*

### 2.6 Two-tier output

Emit two lists, with different error guarantees (see §4.3):

- `spots_reported` — precision-oriented. FDR controlled at 5%. This is what the chemist reads.
- `spots_candidate` — recall-oriented. FNR controlled at 10%. Rendered greyed out, collapsible, labelled *"possible, not confirmed."*

A peak in neither list is dropped silently. A peak with `p_spot` between the two thresholds goes in `spots_candidate`. This structure is what makes honest low confidence *useful* rather than merely humble.

---

## 3. CALIBRATION METHODOLOGY

### 3.1 Which calibrator — decision by labelled-set size

| Labelled spots (n), plates (g) | Primary calibrator | Why |
|---|---|---|
| n < 100 | **None.** Emit `q_spot` ordinal bands from raw-score quantiles, `status: "uncalibrated"` | Any 2-parameter fit on n < 100 with grouped structure has CI on ECE wider than the ECE |
| 100 ≤ n < 300, g ≥ 25 | **Inductive Venn–Abers (IVAP)** primary; **beta calibration** as the smooth secondary | See §3.2 |
| 300 ≤ n < 1500 | Beta calibration (or Platt with prior-corrected targets), IVAP retained for the interval | Enough for a 3-parameter fit |
| n ≥ 1500, g ≥ 150 | Bagged/ensemble isotonic or spline calibration; still report IVAP interval | Only now is nonparametric safe |

**Platt scaling** — logistic regression of `y` on the single score `s`: `p = 1/(1 + exp(a·s + b))`. Two parameters, very stable at small n, but it *assumes* the two class-conditional score distributions are Gaussian with equal variance. Ensemble agreement fractions are bounded in [0, 0.78] and heavily massed near 0 — that assumption is plainly false, and Platt will systematically distort the tails. If used, **always with Platt's own prior-corrected targets** to avoid saturating on small n:

```
y⁺ = (N⁺ + 1)/(N⁺ + 2)      y⁻ = 1/(N⁻ + 2)
```

**Isotonic regression** — nonparametric, monotone, minimizes squared error via PAVA. It is the right answer at large n and the wrong answer here: it overfits badly below ~1,000 points, produces piecewise-constant outputs (so nearby spots get identical probabilities), and its extreme bins collapse to exactly 0 and 1 — which violates C1 and is a guaranteed source of embarrassing overconfidence. Do not use isotonic below n = 1,500, and when you do, bag it over 100 grouped bootstrap resamples and average.

**Beta calibration** — the correct default for scores already living in [0,1] with non-sigmoidal distortion. Three parameters, fitted as a plain logistic regression on two transformed features:

```
features:  u1 = ln(s),  u2 = −ln(1 − s)          (clip s to [ε, 1−ε], ε = 1e-3)
fit:       logit(p) = a·u1 + b·u2 + c
```

It contains the identity map as a special case (a = b = 1, c = 0), so it degrades gracefully when the score is already calibrated — a property Platt lacks. It handles the bounded, skewed shape of agreement fractions. Three parameters is affordable at n ≈ 200. This is the right smooth calibrator for this system. ([Kull et al. via MetricGate summary](https://metricgate.com/blogs/beta-calibration-vs-platt-vs-isotonic/); [Abzu introduction part II](https://www.abzu.ai/data-science/calibration-introduction-part-2/))

**Inductive Venn–Abers (IVAP)** — the honest choice for tiny labelled sets, and my primary recommendation for `p_spot`. It is isotonic regression applied twice (once assuming the test label is 0, once assuming 1), yielding a **probability interval** `[p₀, p₁]` that is automatically valid — perfectly calibrated in a well-defined multiprobabilistic sense — under exchangeability alone, with no distributional assumption. Two properties matter enormously here:

- The **width `p₁ − p₀` is an honest report of how little calibration data you have.** With 40 labelled plates it will be wide (perhaps 0.25); with 300 it narrows. Showing that width to the user is exactly the "honest confidence" the requirement asks for.
- It cannot be tuned into overconfidence by a hyperparameter.

Report the point estimate `p = p₁ / (1 − p₀ + p₁)` and also carry `[p₀, p₁]` in the JSON. Implementations: `venn-abers` (PyPI), `crepes`. ([Vovk et al., arXiv:1211.0025](https://arxiv.org/abs/1211.0025); [Generalized Venn and Venn-Abers Calibration, ICML 2025](https://arxiv.org/html/2502.05676v2))

**Multi-feature case.** All of the above map one score → probability. With the 6-feature logistic of §2.4 the model already outputs a probability; calibrate *on top of it* using out-of-fold predictions as the input score, with beta calibration or IVAP. Never calibrate a model on the data it was fit on.

### 3.2 Recommended stack for `p_spot`

```
raw features  →  L2 logistic (≤6 features)  →  out-of-fold score s
                 ↓ grouped-CV, plates as groups
s  →  IVAP (primary)  →  p_point, [p0, p1]      ← shipped
s  →  beta calibration (secondary)  →  p_beta   ← monitoring only; |p_point − p_beta|
                                                   > 0.15 raises CALIBRATOR_DISAGREE
```

### 3.3 Measuring calibration — and the sample sizes that make each metric real

| Metric | Formula / definition | Minimum n to be meaningful | Notes |
|---|---|---|---|
| **Brier score** | `BS = (1/n) Σ (p_i − y_i)²` | **n ≥ 50** | Lowest-variance proper scoring rule; report it always. Decompose (Murphy): `BS = reliability − resolution + uncertainty`. **Reliability** is the calibration term; **resolution** tells you whether the score is *useful* at all. A perfectly calibrated constant predictor has zero reliability and zero resolution — report both or you will ship a useless-but-calibrated system |
| **ECE** | `Σ_b (n_b/n)·|acc_b − conf_b|`, **equal-mass (quantile) bins**, not equal-width | **n ≥ 300** for B = 5; n ≥ 1,000 for B = 10; n ≥ 5,000 for B = 15 | Rule of thumb: ≥ 50 samples per bin. The plug-in estimator is **upward biased**, and the bias grows ≈ O(B/n); at n = 100, B = 10 a perfectly calibrated model shows ECE ≈ 0.08 out of pure noise. Use equal-mass binning and a bias-corrected/debiased estimator, and sweep B. ([Roelofs et al., *Mitigating Bias in Calibration Error Estimation*, arXiv:2012.08668](https://arxiv.org/abs/2012.08668); [ICLR 2025 blogpost on calibration](https://iclr-blogposts.github.io/2025/blog/calibration/)) |
| **MCE** | `max_b |acc_b − conf_b|` | **n ≥ 2,000** | A max over noisy bin estimates. At realistic n it is essentially the noise of the emptiest bin. **Do not report MCE below n = 2,000**, and never put it in the UI |
| **KS calibration error** | Kolmogorov–Smirnov distance between cumulative predicted and cumulative observed | **n ≥ 100** | **Binning-free** — this is the right primary calibration metric at this scale. Also gives the spline-based recalibrator. ([Gupta et al., ICLR 2021, arXiv:2006.12800](https://arxiv.org/abs/2006.12800)) |
| **Reliability diagram** | Binned accuracy vs confidence | Any n, **with intervals** | Mandatory: Clopper–Pearson or Wilson 95% CI on every bin, and print `n_b` above each bar. A reliability diagram without per-bin CIs at n = 200 is a Rorschach test |
| **Venn–Abers width** | mean `p₁ − p₀` | Any n | The most honest single number to show internally: "how much calibration data do we actually have" |

**Report ECE with a bootstrap CI, clustered by plate.** The gate: *if the 95% CI on ECE includes 0.15, you may not claim the system is calibrated.* Print the CI next to the point estimate everywhere, including internal dashboards.

**Also always report the score histogram.** A calibrated system on this data must emit many low probabilities. If `p_spot` never goes below 0.5, something is broken regardless of what ECE says.

### 3.4 Cross-validation without leakage

Spots on one plate share the origin row, the reference spot, the illumination, the exposure, the operator, and the noise texture. Treating them as independent inflates every metric.

```
GROUPING KEY (strictest available, in order):
   photo_session_id  >  plate_id  >  operator+date+camera
Multiple photos of the SAME plate are one group. Plates from one experiment run
on one day by one operator SHOULD be one group if the count allows it.

OUTER:  StratifiedGroupKFold(n_splits=5, shuffle, seed)   ← evaluation
INNER:  StratifiedGroupKFold(n_splits=3)                  ← feature selection,
                                                            regularization strength,
                                                            calibrator family choice
```
([sklearn StratifiedGroupKFold](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html))

Rules:
- **Pool out-of-fold predictions, then compute ECE/Brier once on the pooled set.** Computing ECE per fold and averaging is nearly meaningless at these sizes and biases upward.
- **Below 50 plates use leave-one-plate-out**, pool the OOF predictions identically.
- **All CIs are clustered bootstrap over plates**, resampling whole plates with replacement (B = 2,000), never resampling individual spots.
- **The original 7 evaluation plates are quarantined.** They are dev/debug artifacts, they have been looked at repeatedly, and every design decision above was informed by them. They enter no calibration set and no reported metric. Store them under `data/dev_burned/`.
- Feature standardization, the config-grid selection (§2.3), the config weights `w_c`, and the σ reference scale are all **fitted quantities** and must live inside the CV loop. Selecting the 24 configs on the full labelled set and then cross-validating the logistic on top of them is leakage and will look great and be wrong.

### 3.5 The label ceiling

Two chemists independently annotate each calibration plate on the rectified image at a fixed zoom, with a fixed protocol (mark spot centers, extents, and an existence confidence in {certain, probable, unsure}); a third adjudicates disagreements. Record:

- Cohen's κ on spot existence at each of the two operating points,
- the distribution of |Δy| between annotators in Rst units (this is the **irreducible floor** on position accuracy — the system cannot beat it and should not claim to),
- the fraction of "unsure" marks.

`p_ceiling` for existence is set from the adjudication-panel agreement, and C6 caps every emitted probability at it. If κ < 0.6, the label set is too noisy to calibrate against and the protocol must be tightened before any model is fit.

### 3.6 Getting to a usable n — synthetic injection

You will not hand-label 1,500 spots. You do not have to for everything.

- **Position and area calibration: synthetic injection.** Take real blank/gutter texture from real plates (the finding-3 machinery), inject EMG-shaped quenching zones of known center, width, asymmetry and integrated density, at controlled SNR, including deliberately clipped variants. This gives unlimited exact labels for `ci90_rst`, `ci90_area_rel`, and the conformal residual distributions. It is *the* way to make §5 rigorous.
- **Existence calibration: real labels only.** Synthetic injection gives a real null but a *synthetic alternative*; a detector tuned on injected EMGs will be overconfident on real T-streaks and halos (finding 11). Existence calibration must use adjudicated real spots. Blank-plate runs give you free negatives at scale — 20 solvent-only plates is cheap and directly measures the phantom rate.
- **Cross-lane correspondence: designed plates.** Run plates where the composition is known by construction (S pure; R = S + product; co = physical mixture of the two). The correspondence labels come free. Ask the user for 40 such plates. This is the only clean way to calibrate claim #12.

### 3.7 Calibration artifact schema

```json
{
  "artifact_id": "spot_existence_ivap_v3",
  "claim": "spot_exists",
  "created": "2026-09-14T10:22:00Z",
  "model": {"family": "logistic_l2", "C": 0.5,
            "features": ["logit_a","z_med","nlog10_p_brown","clip_fraction_box",
                         "sd_position","d_annotation"],
            "feature_spec_hash": "sha256:9f2c..."},
  "calibrator": {"family": "ivap", "secondary": "beta"},
  "config_grid": {"id": "CONFIG_GRID_v1", "K": 24, "K_eff": 7.9,
                  "hash": "sha256:41ab...", "excluded": ["kubelka_munk_*"]},
  "data": {"n_spots": 412, "n_positive": 231, "n_plates": 63,
           "n_blank_plates": 20, "date_range": "2026-06..2026-09",
           "cameras": ["iphone13","iphone15"], "operators": 3,
           "data_hash": "sha256:0c7e..."},
  "oof_metrics": {"brier": 0.112, "brier_reliability": 0.009,
                  "brier_resolution": 0.098, "ks_cal_error": 0.041,
                  "ece_q5": 0.052, "ece_q5_ci95": [0.021, 0.094],
                  "mce": null, "mce_reason": "n<2000",
                  "auroc": 0.914, "mean_va_width": 0.17},
  "label_ceiling": {"kappa_existence": 0.79, "p_ceiling": 0.95,
                    "annotator_position_sd_rst": 0.011},
  "valid_for": {"px_per_lane_min": 40, "clip_fraction_max": 0.02},
  "status": "active"
}
```

The runtime loads this, checks `feature_spec_hash` against the live feature extractor, checks the incoming plate against `valid_for`, and refuses to emit a probability if either fails.

---

## 4. CONFORMAL PREDICTION

### 4.1 Is it appropriate? Yes for position, partly for existence, and not yet at all with 7 plates.

Split conformal gives distribution-free finite-sample marginal coverage under exchangeability, with no assumption about the model. That is exactly the right tool for `ci90_rst`. It is a weaker fit for existence, where what you actually want is a calibrated probability (§3) plus a controlled error *rate* (§4.3), not a set drawn from {present}, {absent}, {both}, {∅} — a "{both}" prediction set for a binary claim conveys nothing a probability doesn't convey better.

### 4.2 Split conformal for spot position — exact procedure

```
SETUP
  Calibration set: n plates, disjoint from training, grouped-split (§3.4).
  Difficulty estimate (normalization):
      σ̂(x) = sd of fitted peak center across the 24 configs, in Rst units
  Nonconformity score, locally adaptive:
      s_i = | Rst_true,i − Rst_hat,i | / ( σ̂_i + λ ),   λ = 0.1 · median(σ̂)

CALIBRATION
  k = ceil( (n + 1) · (1 − α) )
  If k > n  →  the interval is (−∞, ∞)  →  ABSTAIN, reason INSUFFICIENT_CALIBRATION
  q̂ = the k-th smallest value of {s_1 … s_n}

PREDICTION for a new spot
  interval = [ Rst_hat − q̂·(σ̂ + λ) ,  Rst_hat + q̂·(σ̂ + λ) ]
```

The `(n+1)` is the finite-sample correction; without it the interval undercovers. ([Tibshirani, *Conformal Prediction* lecture notes](https://www.stat.berkeley.edu/~ryantibs/statlearn-s23/lectures/conformal.pdf))

**Minimum n for a finite interval:** n ≥ ⌈1/α⌉ − 1. So **α = 0.20 → n ≥ 4; α = 0.10 → n ≥ 9; α = 0.05 → n ≥ 19; α = 0.01 → n ≥ 99.** These are the sizes at which the interval is merely *finite*, not at which it is *stable*.

**How unstable a small-n conformal interval is — say this out loud.** Conditional on the calibration set, realized coverage is `Beta(n + 1 − l, l)` with `l = ⌊(n+1)α⌋`:

| n (calibration units) | α | Mean coverage | SD | Approx 95% range of realized coverage |
|---|---|---|---|---|
| 20 | 0.10 | 0.905 | 0.063 | 0.76 – 0.99 |
| 50 | 0.10 | 0.902 | 0.041 | 0.81 – 0.97 |
| 100 | 0.10 | 0.901 | 0.029 | 0.84 – 0.95 |
| 200 | 0.10 | 0.901 | 0.021 | 0.86 – 0.94 |

At 50 calibration plates your "90% interval" may in truth be an 81% interval. **This must appear in the API docs and in an internal dashboard.** It does not make conformal useless — it makes it the only method that tells you this.

**The grouping problem is the real subtlety.** Spots within a plate are *not* exchangeable with each other: they share the origin row and the reference spot, so their residuals are strongly positively correlated. Treating 400 spots from 60 plates as n = 400 will silently undercover. Two correct options:

- **Per-plate simultaneous band (recommended for the deliverable).** One nonconformity score per plate: `s_plate = max over spots in plate of s_i`. Then the guarantee is *"with probability ≥ 1 − α, every spot on a new plate is inside its interval simultaneously"*. n = number of plates. This is what a chemist actually wants from a plate report, and it is honest about the shared-origin coupling.
- **Marginal per-spot with plate-level splits.** Keep per-spot scores but perform the calibration/test split by plate, and interpret coverage as marginal over the (plate, spot) draw. Weaker, and undercovers if plates vary in spot count; only acceptable with a documented caveat.

Ship the per-plate simultaneous band as `ci90_rst`, and optionally the narrower marginal band as `ci90_rst_marginal` clearly labelled.

**Two-step conformal for extent/area boxes.** If you also want a conformal box around the spot extent (not just the center), use the two-step procedure that first conformalizes a per-coordinate difficulty then a global scaling — it avoids the over-conservative Bonferroni across coordinates. ([Timans et al., *Adaptive Bounding Box Uncertainties via Two-Step Conformal Prediction*, ECCV 2024, arXiv:2403.07263](https://arxiv.org/abs/2403.07263))

**Small-n alternative worth implementing:** with fewer than ~40 plates, split conformal wastes half the data. **CV+ / jackknife+** reuses everything and retains a coverage guarantee of 1 − 2α in the worst case (and ≈ 1 − α in practice), with the leave-one-out folds defined by *plate*. ([Barber, Candès, Ramdas, Tibshirani, *Predictive inference with the jackknife+*, Ann. Statist. 2021](https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-1/Predictive-inference-with-the-jackknife/10.1214/20-AOS1965.pdf); efficiency improvements in [arXiv:2503.01495](https://arxiv.org/pdf/2503.01495))

### 4.3 Conformal risk control for the two spot lists

For the existence side, the useful guarantee is not a prediction set but a **bounded error rate on the emitted list**. Conformal risk control gives that for any monotone risk, and it is three lines of code.

```
Let λ be the detection threshold on p_spot (higher λ = fewer spots reported).
Risk on plate i:
   FNR list:  L_i(λ) = (# adjudicated-true spots missed at λ) / (# true spots)   [B = 1]
   FDR list:  L_i(λ) = (# reported spots that are false at λ) / max(1, # reported)
Choose:
   λ̂ = inf { λ : (n/(n+1)) · R̂_n(λ) + B/(n+1) ≤ α },   R̂_n(λ) = (1/n) Σ_i L_i(λ)
Guarantee: E[ L_test(λ̂) ] ≤ α  on a fresh exchangeable plate.
```
([Angelopoulos, Bates, Fisch, Lei, Schuster, *Conformal Risk Control*, ICLR 2024, arXiv:2208.02814](https://arxiv.org/abs/2208.02814); reference implementation at [github.com/aangelopoulos/conformal-risk](https://github.com/aangelopoulos/conformal-risk))

Run it twice per release: `α_FNR = 0.10` → threshold for `spots_candidate`; `α_FDR = 0.05` → threshold for `spots_reported`. Store both λ̂ in the calibration artifact and print them in the JSON so a reader can see what guarantee they are being given. **The plate is the unit** — this handles the within-plate dependence correctly by construction.

Add **Mondrian (class/group-conditional) stratification** by the variables that plainly shift the error rate: clipping band (`clip_fraction` < 2% / 2–20%), resolution band (`px_per_lane` < 40 / ≥ 40), and camera. Compute a separate λ̂ per stratum where n permits (≥ 20 plates in the stratum), otherwise pool and record `mondrian: false`. Marginal validity is not subgroup validity, and a system that hits 90% coverage overall while covering 60% on clipped plates is exactly the dishonest system the requirement forbids. ([Mondrian conformal predictors](https://onlineprediction.net/index.html?n=Main.MondrianConformalPredictor); [MAPIE theoretical description](https://mapie.readthedocs.io/en/latest/theoretical_description_mondrian.html); [subgroup validity failure modes, arXiv:2605.05562](https://arxiv.org/html/2605.05562))

### 4.4 Where conformal breaks here — state it in the docs

1. **Exchangeability across batches.** New camera, new UV lamp, new silica lot, a different operator's spotting technique, a change in ambient light — each breaks exchangeability, and coverage degrades by an amount related to the distribution drift. Mitigations, in order of preference:
   - **Mondrian strata** on camera/operator/resolution (above) — cheap, effective, no theory needed.
   - **Weighted conformal** with likelihood-ratio weights `w(x) = dP_test/dP_train` estimated by a domain classifier on plate-level covariates, if you can characterize the shift as covariate shift. ([Tibshirani, Barber, Candès, Ramdas, *Conformal Prediction Under Covariate Shift*, arXiv:1904.06019](https://arxiv.org/pdf/1904.06019))
   - **Conformal beyond exchangeability** with fixed, decaying weights on older calibration points — you lose the exact guarantee but gain an explicit, bounded coverage gap that depends on the drift. ([Barber, Candès, Ramdas, Tibshirani, Ann. Statist. 2023](https://projecteuclid.org/journals/annals-of-statistics/volume-51/issue-2/Conformal-prediction-beyond-exchangeability/10.1214/23-AOS2276.pdf); see also [Split Conformal Prediction and Non-Exchangeable Data, JMLR 2024](https://www.jmlr.org/papers/volume25/23-1553/23-1553.pdf))
2. **Multiple photos of the same plate.** Two shots of one plate are near-duplicates. If both land in the calibration set, effective n is smaller than n. Group by `photo_session_id`, not `image_id`.
3. **Adaptive re-shooting.** If the system tells the chemist to re-shoot and the chemist re-shoots until the answer looks right, the incoming distribution is selected on the system's own output. This is a real and likely feedback loop. Mitigate by logging `n_retakes` and excluding retake-selected images from the calibration set.
4. **Tiny n today.** With 7 plates there is **no conformal guarantee of any kind**. Until 30 plates exist, ship intervals computed by bootstrap and label the field `interval_method: "bootstrap_heuristic"`, not `"conformal"`. Flip the label only when n crosses the threshold in the table of §4.2. Lying about the provenance of an interval is worse than having a wide one.

**Coverage monitoring in production (mandatory).** Maintain a rolling audit set: 1 in every 20 plates goes to a chemist for adjudication. Track realized coverage over the last 100 audited plates. Alarm when empirical coverage < (1 − α) − 3·SE, with `SE = sqrt(α(1−α)/m)`. This is the only thing standing between you and silent drift.

---

## 5. UNCERTAINTY ON POSITION AND AREA

### 5.1 Peak-fit covariance — and the correction everyone forgets

Fit EMG (exponentially modified Gaussian; finding 11) by weighted nonlinear least squares:

```
f(y) = A · (σ_g/τ) · sqrt(π/2) · exp( (σ_g²/(2τ²)) − (y−μ)/τ ) · erfc( (σ_g/τ − (y−μ)/σ_g)/√2 ) + baseline
```

The naive covariance `Cov = ŝ²·(JᵀJ)⁻¹` **assumes white residuals and is wrong here** — the densitogram is smoothed and the noise is spatially correlated. It will understate position SE by the square root of the variance inflation factor, typically 1.3–1.8×.

Correct it. Estimate the residual autocorrelation `ρ̂_k` and:

```
VIF = 1 + 2 · Σ_{k=1}^{K} (1 − k/n) · ρ̂_k ,     K = ceil(3 × FWHM_px)
Cov_corrected = VIF · ŝ² · (JᵀJ)⁻¹
```
Or, better, use a HAC / Newey–West sandwich estimator `(JᵀJ)⁻¹ (Jᵀ Ω̂ J) (JᵀJ)⁻¹` with a Bartlett kernel of bandwidth `2×FWHM`. Report `VIF` in the JSON — it is a useful diagnostic on its own.

### 5.2 Bootstrap over the config ensemble — Rubin's rules

Two sources of position variance: *within-config* fit noise, and *between-config* model choice. Combine them the way multiple imputation does.

```
For each of the K=24 configs c:
    μ̂_c      = fitted center
    V_c      = corrected variance of μ̂_c  (§5.1), optionally refined by a
               BLOCK residual bootstrap (block length = 2×correlation length,
               B = 500 refits) — use the block bootstrap when the EMG fit is
               near a parameter bound

W̄ = Σ_c w_c V_c / Σ_c w_c                              (within-config variance)
μ̄ = Σ_c w_c μ̂_c / Σ_c w_c                              (pooled estimate)
B̄ = Σ_c w_c (μ̂_c − μ̄)² / (Σ_c w_c − 1)                 (between-config variance)

Var_total(μ) = W̄ + (1 + 1/K_eff) · B̄
```

Use `K_eff` (§2.3), not `K` — with 24 correlated configs the small-sample inflation must reflect the *effective* count. Report the split: if `B̄ ≫ W̄`, the dominant uncertainty is which pipeline you chose, and that is worth telling the user.

### 5.3 Rst and its interval

```
Rst = (y_spot − y_origin) / (y_ref − y_origin) ,   D ≡ y_ref − y_origin
```

Delta method (partials: `∂/∂y_s = 1/D`, `∂/∂y_r = −Rst/D`, `∂/∂y_0 = (Rst − 1)/D`):

```
Var(Rst) ≈ (1/D²) · [ Var(y_s) + Rst² · Var(y_r) + (Rst − 1)² · Var(y_0) ]
           + cross terms if y_r and y_0 estimates are correlated (they are if
             both come from the same lane-grid fit — carry the full 3×3 covariance)
```

**Publish the variance budget.** Three numbers summing to 100%:

```json
"rst_variance_budget": {"spot": 0.21, "origin": 0.62, "reference": 0.17}
```

This is not decoration. On these plates the origin row will usually dominate, and telling the chemist *"62% of your uncertainty is that we can't see where you spotted"* converts an unfixable-looking number into a one-line lab habit change: draw the origin line in pencil.

The final `ci90_rst` is the **conformal** interval (§4.2) when a conformal calibrator exists; the delta-method/Rubin variance feeds in as the normalization `σ̂(x)`. When it doesn't, ship the delta-method interval with `interval_method: "delta_bootstrap"`.

### 5.4 ΔRst — the reference and origin cancel, and this matters enormously

For two spots on the **same plate**, the shared `y_0` and `y_r` largely cancel:

```
∂(R1 − R2)/∂y_r = −(R1 − R2)/D        ∂(R1 − R2)/∂y_0 = (R1 − R2)/D

Var(R1 − R2) ≈ (1/D²)·[ Var(y_s1) + Var(y_s2) ]
             + ((R1 − R2)²/D²)·[ Var(y_r) + Var(y_0) − 2Cov(y_r, y_0) ]
```

When `R1 ≈ R2` — precisely the co-elution case — the second term vanishes and **ΔRst is far more precise than either Rst individually.** This is the correct statistical basis for claim #12 and it must be implemented as such. Do not compute `p_same_compound` by overlapping the two marginal Rst intervals; that is systematically over-conservative and will make the system say "inconclusive" on plates a chemist reads instantly.

Test statistic and its companions:

```
t      = |ΔRst| / SE(ΔRst)                     position agreement
r_prof = Pearson r of the two normalized EMG profiles, resampled to a common grid
Rs     = 2·|μ1 − μ2| / (w1 + w2)               chromatographic resolution in the co lane
                                                (Rs < 1.0 → unresolved → co-spot is
                                                 consistent with identity)
```
Feed `t`, `r_prof`, `Rs`, plus both `p_spot` values, into a small logistic calibrated on the designed co-spot plates (§3.6). **Cap `p_same_compound` at the measured ceiling** — co-elution on a single mobile phase is weak evidence for identity, and no amount of pixel precision changes that. UI wording is fixed: *"consistent with the same compound (p = 0.84, single eluent system)"*.

### 5.5 Area — the honest position is mostly refusal

Area is the quantity most damaged by the clipping in finding 1, because `OD = log10(I0/I)` needs a trustworthy `I0`.

**Hard gates:**
- `clip_fraction` inside the peak box + 3 px margin > 0 → `area_status: "suppressed"`, reason `CLIPPED`. No number, not even a wide one.
- `clip_fraction` anywhere in the lane band > 2% → area suppressed for the whole lane (the local `I0` reference is unreliable).
- `streak_index` above the streaking threshold → area suppressed, reason `NON_GAUSSIAN_SHAPE` (finding 11).
- Kubelka–Munk transform: **forbidden** in this regime (finding 5).

**When it is allowed:** report only `area_rel` — the peak's integrated OD as a fraction of the lane's total integrated OD — with an interval from the same Rubin combination as §5.2 (integrate the EMG analytically; propagate through the fitted parameter covariance, or bootstrap the integral directly, which is simpler and more robust to the erfc parameterization).

**Never report concentration or % conversion from area without ≥ 3 standard levels of the analyte spotted on the same plate.** TLC densitometric response is nonlinear in amount and plate-specific. Without the standards, emit `concentration: {"value": null, "status": "abstained", "reason_code": "NO_CALIBRATION_LADDER"}`. If the standards exist, fit the ladder, report the interval from the inverse-prediction (calibration) interval — which is asymmetric and wider than the naive one — and refuse extrapolation beyond the ladder range.

### 5.6 Deriving the reaction conclusion by rule, not by model

`p_conclusion` is composed, not learned, so it can be audited:

```
SM_consumed  requires: SM spot present in S lane (p_spot > 0.9)
                       AND SM-corresponding spot absent from R lane
                       AND the R lane is not abstained
             p = p_spot(S) · (1 − p_spot(R_at_matching_Rst)) · p_lane_label(S) · p_lane_label(R)
             — with the caveat printed: absence of evidence at this SNR is weak.
               If the R lane's detection limit (z=3 equivalent amount) is not
               below 5% of the S-lane spot amount, emit "inconclusive", not
               "consumed". A missing spot on a noisy plate is not a consumed
               starting material.
```
That last clause is the difference between a useful system and a dangerous one. Compute and report a per-lane **limit of detection** in `area_rel` units from the empirical null (§2.2): `LOD = 3·σ_A / (lane total OD)`. Any "absent" claim must be accompanied by its LOD.

---

## 6. ABSTENTION POLICY

### 6.1 Representation in JSON — no sentinels, ever

```json
{
  "value": null,
  "status": "abstained",
  "reason_code": "EXPOSURE_CLIPPED",
  "detail": "Green channel saturates on 31% of lane 3 (limit 2%). Optical density is not invertible where I0 is clipped.",
  "measured": {"clip_fraction": 0.31, "threshold": 0.02},
  "remediation": "Re-shoot at 1/3 exposure (EV −1.5) or move the plate further from the lamp. Keep the same framing.",
  "severity": "blocking"
}
```

`status` ∈ `ok` | `ok_with_warning` | `abstained` | `uncalibrated` | `not_applicable`. A field is never `0`, `-1`, `"N/A"` or omitted-and-defaulted. Consumers switch on `status` before touching `value`; the serializer enforces that `value` is `null` whenever `status != "ok"` and `!= "ok_with_warning"`.

`severity` ∈ `blocking` (nothing downstream can be trusted) | `local` (this claim only) | `advisory`.

### 6.2 Decision table

Evaluated in order. First blocking row wins for the whole plate; local rows disable only their scope.

| # | Condition | Measurement | Threshold | Scope | Action | reason_code |
|---|---|---|---|---|---|---|
| 1 | No plate found | detector score | `p_plate_detected < 0.5` | plate | abstain, return nothing | `NO_PLATE` |
| 2 | Plate cropped | min margin of plate quad to image border, rectified | `< 0` px on any edge | plate if bottom/top edge; lane-local if side edge | abstain on all Rst; spot list still emitted in mm-from-visible-bottom with `no_origin` | `PLATE_CROPPED` |
| 3 | Rectification poor | reprojection RMS / plate width | `> 1.5%` | plate | abstain on all positions | `RECT_FAILED` |
| 4 | Severe clipping | `clip_fraction` over full plate | `> 40%` | plate | abstain entirely | `EXPOSURE_CLIPPED` |
| 5 | Lane clipping | `clip_fraction` in lane band | `> 20%` | lane | abstain on that lane | `EXPOSURE_CLIPPED` |
| 6 | Mild clipping | `clip_fraction` in lane band | `> 2%` | lane | positions OK, **areas suppressed**, `ok_with_warning` | `AREA_UNRELIABLE_CLIP` |
| 7 | Any clipping in peak box | `clip_fraction_box` | `> 0` | spot | area suppressed | `CLIPPED` |
| 8 | No solvent front | always true on this corpus | — | plate | `Rf` field absent from schema; UI shows Rst only | `NO_SOLVENT_FRONT` |
| 9 | No standard lane | VLM lane labels contain no `sd`/`S` reference | — | plate | Rst undefined → emit `distance_from_origin_mm` only | `NO_REFERENCE_LANE` |
| 10 | Origin not locatable | `ci90_origin_y` width | `> 8%` of plate height | plate | abstain on Rst; emit raw mm; remediation "draw the origin line" | `ORIGIN_UNCERTAIN` |
| 11 | Lane count disputed | VLM self-consistency (§7) | `p_lane_count < 0.7` **or** VLM count ≠ label-row count | plate | emit spots in plate coordinates, abstain on all lane assignment and all lane labels | `LANE_COUNT_DISPUTED` |
| 12 | Resolution too low for text | median glyph height | `< 12 px` | text only | abstain on `sample_id`; spots unaffected | `TEXT_RESOLUTION` |
| 13 | Resolution too low for spots | `px_per_lane` | `< 10` | plate | abstain on spots | `SPOT_RESOLUTION` |
| 14 | Structured noise | `VIF` | `> 6` | plate | abstain; likely glare, fingerprints or heavy JPEG | `NOISE_STRUCTURED` |
| 15 | Empirical null too permissive | phantom peaks at the operating λ̂ on this plate's own surrogates | `> 1.0` expected | plate | abstain on `spots_reported`; emit `spots_candidate` only | `NULL_UNSTABLE` |
| 16 | Streaking lane | `streak_index` | above dev-set 95th pct | lane | positions with widened intervals, areas suppressed, `shape_class: "streak"` | `NON_GAUSSIAN_SHAPE` |
| 17 | Calibrator invalid | plate outside `valid_for` of the artifact | — | claim | `status: "uncalibrated"`, emit `q_*` ordinal band only | `OUT_OF_CALIBRATION_DOMAIN` |
| 18 | Calibration set too small | conformal n below §4.2 table | — | interval | interval labelled `bootstrap_heuristic`, not `conformal` | `INTERVAL_HEURISTIC` |
| 19 | Calibrators disagree | `\|p_ivap − p_beta\| > 0.15` | — | spot | downgrade to `spots_candidate`, `ok_with_warning` | `CALIBRATOR_DISAGREE` |
| 20 | Spot inside annotation band | peak y above `annotation_band` boundary | — | spot | **drop, do not report** (finding 7: ink is often darker than chemistry) | `IN_ANNOTATION_BAND` |
| 21 | Annotation band unlocatable | `p_annotation_band < 0.6` | — | plate | abstain on all spots in the top 25% of the plate | `ANNOTATION_BAND_UNKNOWN` |

Row 20 deserves emphasis. Finding 7 measured that header ink runs OD 0.053–0.162 while chemistry runs 0.038–0.092 — the ink is frequently **darker** than the analyte. There is no pixel-level discriminator. The only defence is a geometric exclusion zone whose boundary comes from the VLM or an operator convention, and anything above it is discarded unconditionally.

### 6.3 Global abstention rules

- **No probability without a calibrator** (C2). If the relevant calibrator has fewer than 20 labelled examples *in the plate's stratum*, emit `q_*` ordinal bands defined by dev-set score quantiles, plainly labelled `"not a probability"`.
- **Refuse rather than extrapolate.** Every calibrator carries `valid_for`. Outside it, abstain — do not clamp.
- **A plate with zero `spots_reported` is a valid, complete result**, not an error. Finding 11 records a plate with zero spots at 5σ. Render it as *"No spots detected above the reporting threshold. LOD for each lane: …"* with the per-lane LOD table. That is a genuine scientific answer.
- **Every abstention gets a remediation string** and, where applicable, a `retake_advice` block: target exposure change in EV, distance, whether to draw the front line, whether to write labels larger. The user is a chemist standing at the bench who can act on this in 90 seconds.
- **Abstentions route to a review queue** in the frontend with the specific question pre-posed ("Is there a spot at Rst 0.42 in lane 3?"), and the chemist's answer becomes a label. Abstention is the system's cheapest source of training data — instrument it.

---

## 7. VLM CONFIDENCE

Findings 8, 9, 10 make a frontier VLM load-bearing for text, lane count, lane labels, and the annotation band. Frontier APIs mostly do not expose token logprobs, so confidence has to be constructed from sampling.

### 7.1 What the VLM is asked, and what it is never trusted with

| Task | Type | VLM role |
|---|---|---|
| Lane count | closed, 1–8 | **Authoritative** (finding 10: signal-based lane detection fails on empty lanes) |
| Lane labels | closed vocab {S, co, R, sd, blank, other, UNREADABLE} | **Authoritative** |
| Sample ID / header text | free text | **Authoritative** (finding 8: OCR is disabled) |
| Solvent front drawn? | closed y/n | Authoritative (expected: always "no") |
| Plate fully in frame? | closed y/n | Corroborating; geometry is authoritative |
| Annotation band boundary | continuous y | **Authoritative** (finding 7) |
| Band / spot positions | continuous | **Proposal only, never authoritative** (finding 9: invented a band at 1.8σ) |

### 7.2 Sampling design — vary nuisance, not just temperature

Temperature-only sampling measures the model's decoding entropy. What you want to measure is whether the answer is robust to things that should not matter. Ensemble over nuisance variation:

```
4 prompt paraphrases  ×  2 image presentations (raw rectified, CLAHE + gamma 0.7)  ×  2 samples
= 16 samples at T = 0.7
+ 1 anchor sample at T = 0  on the canonical prompt/image
```

`T = 0.7` (not 1.0): high enough for real diversity, low enough that the samples stay on-task under a constrained schema. The anchor at T = 0 gives a deterministic reference; whether the plurality agrees with the anchor is a useful calibration feature.

**Adaptive stopping** (latency, not cost, is the constraint — finding 14):
```
Draw 5 samples (spanning ≥ 2 prompts and both image presentations).
  If unanimous under the task's equivalence relation → stop, record k = 5, agreement = 1.0.
  Else draw to 16.
  If after 16 the plurality share < 0.4 → return UNREADABLE, do not draw more.
```
A quick note on resolution: the standard error of a plurality share estimated from k samples is at most `0.5/√k` — **0.22 at k = 5, 0.125 at k = 16.** Sixteen samples resolve agreement into roughly four meaningful bands, not sixteen. Do not report `agreement = 0.6875` as though the third digit exists; the calibrator consumes the raw value, the UI shows a band.

### 7.3 Scoring agreement

**Closed vocabulary.** Jeffreys-smoothed plurality share plus the full distribution's entropy:

```
a_vlm = (k_top + 0.5) / (k + 0.5·|V|)                   |V| = vocabulary size incl. UNREADABLE
H     = −Σ_v p̂_v log p̂_v                                normalized by log|V|
```

**Free text (sample IDs).** Do not use exact string match — one character differing throws away the information that 15 of 16 samples agreed on the other 7 characters.

```
1. Normalize each sample: strip whitespace, uppercase, and apply the domain
   confusion classes {0,O,o,Q}, {1,l,I,|}, {5,S}, {2,Z}, {8,B}, {6,G}, {rn,m}.
   Chemistry IDs draw from a known alphabet — define the classes explicitly and
   version them.
2. Pairwise normalized Levenshtein distance; agglomerative clustering, average
   linkage, cut at d = 0.20 (i.e. ≥80% character agreement).
3. a_vlm = largest cluster share.
4. Consensus string: character-wise majority vote after multiple sequence
   alignment of the largest cluster (ROVER-style), NOT the medoid — MSA voting
   recovers the correct string even when no single sample is fully correct.
5. char_confidence[j] = vote share for the winning character at aligned position j,
   Jeffreys-smoothed.
```

`char_confidence` is a genuinely valuable output: render the ID with low-confidence characters highlighted so the chemist corrects two characters instead of retyping the string. Any character below 0.6 renders as `▯` and the whole ID is marked `needs_confirmation`.

**Continuous (annotation band y).** Report the median across samples and the interquartile range; convert IQR to a conservative SD via `IQR/1.35`; feed to the abstention rule in table row 21.

### 7.4 Constrained decoding with an explicit UNREADABLE

Use the API's structured-output / JSON-schema mode. Every enum includes `UNREADABLE` and, where meaningful, `NOT_PRESENT`.

```json
{"type":"object","required":["lane_count","lanes","header","front_drawn"],
 "properties":{
  "lane_count":{"type":["integer","string"],"enum":[1,2,3,4,5,6,7,8,"UNREADABLE"]},
  "lanes":{"type":"array","items":{"type":"object","required":["x_frac","label","label_evidence"],
    "properties":{
      "x_frac":{"type":"number","minimum":0,"maximum":1},
      "label":{"enum":["S","co","R","sd","blank","other","UNREADABLE"]},
      "label_evidence":{"type":"string","maxLength":120}}}},
  "header":{"type":"object","required":["text","legible"],
    "properties":{"text":{"type":["string","null"]},
                  "legible":{"enum":["clear","partial","UNREADABLE"]},
                  "uncertain_char_indices":{"type":"array","items":{"type":"integer"}}}},
  "front_drawn":{"enum":["yes","no","UNREADABLE"]},
  "annotation_band_y_frac":{"type":["number","null"]}}}
```

Prompt requirements (all mandatory):
- State explicitly that `UNREADABLE` is a **correct** answer and is preferred over a guess. Without this, models guess.
- Require `label_evidence`: a short description of the pixels supporting each label. Free-text justification measurably reduces confabulation and gives a human something to check.
- Forbid inference from chemical plausibility: *"Report only what is visible. Do not infer a label from what a TLC plate usually has."*
- **Adversarial second look:** one of the four prompt paraphrases must be a critique prompt — *"Here is a proposed reading: {anchor}. Argue why it may be wrong, then give your own reading."* Its output counts as a sample. This catches the fluent-and-wrong mode that plain resampling does not.

### 7.5 Agreement → calibrated probability

Fit **a separate calibrator per task type**. Do not share one across `lane_label`, `lane_count` and `sample_id`; their agreement→accuracy curves are entirely different.

Features: `[a_vlm, normalized entropy H, k used, anchor_agrees_with_plurality, median glyph height in px, clip_fraction over the label row, px_per_lane, verbalized_confidence (if elicited)]`.

Glyph height must be in the model: finding 8 measured 3–16 px against 12–15 px vendor minimums, and it is the known physical driver of the failure. Calibrator: beta calibration or IVAP as in §3, with grouped CV by plate.

**The verbalized confidence the model states is a feature, never the output.** Recent work finds verbal confidence and self-consistency are complementary but that neither is calibrated out of the box, and that reasoning traces surface better-calibrated signals than direct elicitation. ([*Confidence Improves Self-Consistency in LLMs*, ACL Findings 2025](https://aclanthology.org/2025.findings-acl.1030.pdf); [*Two Samples Are Enough: Verbal Confidence Meets Self-Consistency*](https://openreview.net/forum?id=66D3rZrNjV); [*Reasoning Helps Surface Self-Confidence Signals in LLMs*, UncertaiNLP 2025](https://aclanthology.org/2025.uncertainlp-main.21.pdf); [ConfidenceBench](https://arxiv.org/html/2607.20526))

**Measure the unanimity ceiling and publish it.** On the labelled set, compute:

```
P(wrong | a_vlm = 1.0)
```

This is the rate at which the model is *consistently* wrong — the systematic-bias mode that no amount of sampling detects. Given the CER numbers in finding 8, on 4-character label rows this could be 10–20% for `sample_id`. **That number is the hard ceiling on what unanimity can ever mean**, and it must cap `p_sample_id` under C6. If unanimity means 0.85, the system never shows 0.99 for a sample ID, no matter how many samples agree.

### 7.6 VLM band proposals — the fusion rule

Finding 9: the VLM placed bands to within 0.012 apparent Rf but invented one at 1.8σ and missed the origin residue. The rule:

1. VLM band proposals **seed** the peak search — they add candidate positions the pixel pipeline must evaluate. This fixes the missed origin residue.
2. A VLM proposal enters the existence model only as the binary feature `x11 = vlm_proposed`. Its weight is *learned from labelled data*, so its real value is measured rather than assumed.
3. A proposal with no pixel support never appears in `spots_reported`. It appears in `spots_candidate` at whatever low probability the calibrator assigns, with `source: "vlm_proposed_unsupported"`.
4. A pixel-detected spot the VLM did not propose is not penalized beyond the fitted weight.

Never suppress an unsupported proposal silently and never promote one. Both are lies of a different sign.

---

## 8. ANTI-PATTERNS — WHAT MUST NEVER BE SHOWN TO A USER AS "CONFIDENCE"

Each of these looks like a probability, sorts like a probability, and is not one. Anything on this list that reaches the UI is a bug of the same severity as a wrong Rst.

1. **A softmax output, or any raw model score.** Modern networks are systematically overconfident; the softmax is a normalized activation, not a frequency. If a detector is ever added, its objectness score is a feature, not a confidence. ([Guo et al., *On Calibration of Modern Neural Networks*; see also detector-specific miscalibration, arXiv:2202.12785](https://arxiv.org/abs/2202.12785))

2. **Raw ensemble agreement.** This system's own starting point, and the most seductive item on the list, because 21/24 *looks* like 0.875. It is not. It is (a) uncalibrated, (b) inflated by near-duplicate configs — `K_eff = 1.24` at ρ̄ = 0.8, so 24 runs can carry one run's worth of evidence — and (c) capped at 0.78 by grid composition, so it cannot even express high confidence. **Rule: the agreement fraction never appears in the UI as a percentage.** It may appear as `"21 of 24 pipelines"` next to the calibrated probability, as supporting detail.

3. **IoU, overlap, or any similarity measure as confidence.** IoU between a fitted spot and a config's spot answers "do these two boxes coincide"; it has no relationship to "is there a spot here". Detection calibration literature is explicit that localization quality and existence confidence are distinct and must be calibrated separately. ([Küppers et al., *Multivariate Confidence Calibration for Object Detection*, CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w20/Kuppers_Multivariate_Confidence_Calibration_for_Object_Detection_CVPRW_2020_paper.pdf); [Multiclass Confidence and Localization Calibration, arXiv:2306.08271](https://arxiv.org/html/2306.08271))

4. **Error bars from the peak fit presented as correctness.** `sqrt(diag((JᵀJ)⁻¹ ŝ²))` answers: *given that an EMG plus this baseline model is the truth, and given white noise, how well is μ pinned down?* Both givens are false here — the baseline model is a choice (that is `B̄` in §5.2, often the larger term) and the noise is correlated (that is VIF, typically 1.3–1.8×). A fit SE of ±0.004 Rst on a feature that is not a spot at all is a precise measurement of nothing. **Fit covariance may only be shown as a component inside the variance budget, never as the interval.**

5. **R², χ²/dof, SNR or peak height relabelled as a percentage.** `z = 4.2` is not "84% confident". Any transform of a raw statistic into a 0–100 scale by a hand-picked squashing function is a fabrication wearing a probability's clothes.

6. **Any probability evaluated on the data used to fit it.** In-sample calibration is always excellent and always a lie. The 7 evaluation plates are permanently quarantined for exactly this reason.

7. **Isotonic outputs of exactly 0.00 or 1.00.** Certainty is never justified here. Clip all emitted probabilities to [0.01, `p_ceiling`] and show two significant figures at most. `p = 0.8`, not `p = 0.7963`.

8. **Marginal coverage presented as per-plate coverage.** "Our 90% intervals cover 90% of spots" is compatible with covering 99% on clean plates and 55% on clipped ones. Report Mondrian-stratified coverage or say plainly that you have not measured it.

9. **A confidence distribution that never goes low.** If `p_spot` is above 0.5 for 95% of emitted spots, the system is not discriminating; it is decorating. Ship the score histogram in the internal dashboard as a standing check.

10. **Rf, computed against any assumed solvent front.** The same spot yields Rf 0.34–0.97 depending on convention (finding 2). Emitting one of those with an interval that does not span 0.34–0.97 is the single most misleading thing this system could do.

11. **VLM self-consistency treated as correctness.** Consistency measures the model's stability, not its accuracy; a systematically misread glyph produces 16 identical wrong answers. This is why §7.5 mandates measuring and publishing `P(wrong | agreement = 1.0)`.

12. **A calibrated probability applied outside its calibration domain.** A calibrator fitted on `px_per_lane ≥ 40` says nothing about a 12 px lane. Clamping to the nearest calibrated regime is extrapolation with a friendly face — abstain instead (table row 17).

13. **Co-elution reported as identity.** `p_same_compound = 0.92` must never render as "same compound". One eluent system cannot establish identity, and the wording is fixed at "consistent with".

---

## 9. IMPLEMENTATION NOTES

### 9.1 Result JSON skeleton (abridged)

```json
{
  "schema_version": "1.0",
  "image_id": "...", "photo_session_id": "...",
  "plate": {
    "p_plate_detected": 0.99,
    "p_plate_complete": 0.31,
    "p_rect_valid": 0.94,
    "clip_fraction": 0.27, "clip_fraction_max_lane": 0.41,
    "vif": 2.1, "px_per_lane": 58, "median_glyph_height_px": 7,
    "verdict": "partial",
    "flags": ["PLATE_CROPPED", "EXPOSURE_CLIPPED", "NO_SOLVENT_FRONT"]
  },
  "geometry": {
    "origin_y_frac": 0.081, "ci90_origin_y_frac": [0.062, 0.104], "p_origin": 0.88,
    "front": {"value": null, "status": "not_applicable",
              "reason_code": "NO_SOLVENT_FRONT",
              "detail": "No solvent front is drawn on this plate. Rf is undefined; Rst is reported instead."},
    "annotation_band_y_frac": 0.78, "p_annotation_band": 0.91,
    "reference": {"lane": 4, "spot_index": 0, "label": "sd"}
  },
  "lanes": [
    {"index": 3, "x_frac": 0.52, "ci90_x_frac": [0.505, 0.536], "p_lane": 0.93,
     "label": "R", "p_lane_label": 0.86, "vlm_agreement": {"k": 16, "top_share": 0.81},
     "lod_area_rel": 0.021, "streak_index": 0.14, "status": "ok"}
  ],
  "spots_reported": [
    {"id": "s3_1", "lane": 3,
     "rst": 0.62, "ci90_rst": [0.588, 0.651],
     "interval_method": "conformal_split_perplate", "alpha": 0.10,
     "rst_variance_budget": {"spot": 0.21, "origin": 0.62, "reference": 0.17},
     "p_spot": 0.91, "p_spot_interval": [0.84, 0.95],
     "calibrator": "spot_existence_ivap_v3",
     "support": {"configs_detecting": 21, "configs_total": 24, "k_eff": 7.9,
                 "z_med": 6.4, "z_min": 4.1, "p_mc": "<0.005"},
     "area_rel": {"value": null, "status": "abstained",
                  "reason_code": "CLIPPED",
                  "detail": "Green channel saturates inside the peak box.",
                  "remediation": "Re-shoot at EV −1.5."},
     "shape_class": "emg", "tau_over_sigma": 0.9}
  ],
  "spots_candidate": [
    {"id": "s3_2", "lane": 3, "rst": 0.34, "ci90_rst": [0.281, 0.402],
     "p_spot": 0.28, "p_spot_interval": [0.14, 0.44],
     "source": "vlm_proposed_unsupported",
     "support": {"configs_detecting": 5, "configs_total": 24, "z_med": 1.8}}
  ],
  "correspondences": [
    {"lanes": [1, 3], "spots": ["s1_1", "s3_1"],
     "delta_rst": 0.004, "ci90_delta_rst": [-0.009, 0.017],
     "p_same_compound": 0.84, "capped": false,
     "statement": "consistent with the same compound (single eluent system)"}
  ],
  "conclusion": {"value": "inconclusive", "p_conclusion": null,
                 "status": "abstained", "reason_code": "LOD_TOO_HIGH",
                 "detail": "Lane 3 LOD (area_rel 0.021) is not below 5% of the lane-1 SM spot; absence of an SM band does not establish consumption."},
  "guarantees": {"fdr_reported": 0.05, "fnr_candidate": 0.10,
                 "conformal_alpha": 0.10, "conformal_n_plates": 63,
                 "conformal_coverage_sd": 0.036, "mondrian_stratum": "clip_2_20pct",
                 "artifact_ids": ["spot_existence_ivap_v3", "position_conformal_v2"]},
  "retake_advice": ["Reduce exposure by 1.5 EV — 27% of the plate is saturated.",
                    "Include the full plate; the top edge is cut off.",
                    "Draw the origin line and the solvent front in pencil before photographing.",
                    "Write labels at least 2× larger; glyphs measure 7 px, minimum reliable is 12 px."]
}
```

### 9.2 CI gates (build fails if violated)

| Gate | Check |
|---|---|
| G1 Phantom rate | On the 20 held-out blank plates, mean spots in `spots_reported` ≤ 0.25/plate |
| G2 Blank plate clean rate | ≥ 80% of blank plates yield an empty `spots_reported` |
| G3 Calibration | Pooled OOF `ece_q5` upper 95% CI bound < 0.15 |
| G4 Resolution | OOF Brier resolution term > 0.05 (guards against a calibrated-but-useless constant predictor) |
| G5 Coverage | Realized coverage of `ci90_rst` on OOF plates within `[1−α−3SE, 1]` |
| G6 No Rf | Static check: no code path emits a field named `rf` |
| G7 No sentinels | Schema validation: `value != null` implies `status ∈ {ok, ok_with_warning}` |
| G8 Ceiling respected | No emitted probability exceeds its artifact's `p_ceiling` |
| G9 Quarantine | The 7 dev plates appear in no calibration or metric artifact (hash check) |
| G10 Grid health | `K_eff ≥ 4` on the current dev set |

### 9.3 Build order (this is the dependency chain, and it is strict)

```
1. σ reference scale + matched-filter noise unit (§2.1)   ← nothing works before this
2. Empirical null / surrogate generation (§2.2)           ← the phantom fix
3. Config grid selection + K_eff (§2.3)
4. Labelling protocol, annotator agreement, label ceiling (§3.5)
5. Synthetic injection harness (§3.6)                     ← unblocks position/area
6. Logistic + IVAP calibration, grouped CV (§3)
7. Conformal position intervals, per-plate (§4.2)
8. Conformal risk control for the two lists (§4.3)
9. VLM self-consistency + its own calibrators (§7)
10. Abstention table, JSON contract, CI gates (§6, §9)
```

Steps 1–3 are worth more than the rest combined: they convert an uncalibratable pipeline into one whose output *can* be calibrated. Attempting step 6 before step 1 will produce a well-fitted model of a circular statistic.

---

## Sources

- [Kull et al. beta calibration — comparison with Platt and isotonic (MetricGate)](https://metricgate.com/blogs/beta-calibration-vs-platt-vs-isotonic/)
- [An introduction to calibration (part II): Platt scaling, isotonic regression, and beta calibration — Abzu](https://www.abzu.ai/data-science/calibration-introduction-part-2/)
- [Niculescu-Mizil & Caruana, *Predicting Good Probabilities With Supervised Learning*, ICML 2005](https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf)
- [Classifier Calibration at Scale: An Empirical Study of Model-Agnostic Post-Hoc Methods (arXiv:2601.19944)](https://arxiv.org/html/2601.19944v1)
- [Vovk, Petej, Fedorova, *Venn-Abers predictors* (arXiv:1211.0025)](https://arxiv.org/abs/1211.0025)
- [Generalized Venn and Venn-Abers Calibration with Applications in Conformal Prediction, ICML 2025 (arXiv:2502.05676)](https://arxiv.org/html/2502.05676v2)
- [Roelofs et al., *Mitigating Bias in Calibration Error Estimation*, AISTATS 2022 (arXiv:2012.08668)](https://arxiv.org/abs/2012.08668)
- [Understanding Model Calibration — ICLR 2025 Blogposts](https://iclr-blogposts.github.io/2025/blog/calibration/)
- [Gupta et al., *Calibration of Neural Networks using Splines* (KS calibration error), ICLR 2021 (arXiv:2006.12800)](https://arxiv.org/abs/2006.12800)
- [Tibshirani, *Conformal Prediction* — lecture notes, Berkeley 2023](https://www.stat.berkeley.edu/~ryantibs/statlearn-s23/lectures/conformal.pdf)
- [Barber, Candès, Ramdas, Tibshirani, *Predictive inference with the jackknife+*, Ann. Statist. 49(1), 2021](https://projecteuclid.org/journals/annals-of-statistics/volume-49/issue-1/Predictive-inference-with-the-jackknife/10.1214/20-AOS1965.pdf)
- [Improving the statistical efficiency of cross-conformal prediction (arXiv:2503.01495)](https://arxiv.org/pdf/2503.01495)
- [Tibshirani, Barber, Candès, Ramdas, *Conformal Prediction Under Covariate Shift* (arXiv:1904.06019)](https://arxiv.org/pdf/1904.06019)
- [Barber, Candès, Ramdas, Tibshirani, *Conformal prediction beyond exchangeability*, Ann. Statist. 51(2), 2023](https://projecteuclid.org/journals/annals-of-statistics/volume-51/issue-2/Conformal-prediction-beyond-exchangeability/10.1214/23-AOS2276.pdf)
- [Split Conformal Prediction and Non-Exchangeable Data, JMLR 25, 2024](https://www.jmlr.org/papers/volume25/23-1553/23-1553.pdf)
- [Angelopoulos, Bates, Fisch, Lei, Schuster, *Conformal Risk Control*, ICLR 2024 (arXiv:2208.02814)](https://arxiv.org/abs/2208.02814) · [reference code](https://github.com/aangelopoulos/conformal-risk)
- [Mondrian Conformal Predictor — on-line prediction wiki](https://onlineprediction.net/index.html?n=Main.MondrianConformalPredictor) · [MAPIE Mondrian docs](https://mapie.readthedocs.io/en/latest/theoretical_description_mondrian.html)
- [Socio-Conformal Calibration: Marginal Validity Is Not Enough for Subgroup Reliability (arXiv:2605.05562)](https://arxiv.org/html/2605.05562)
- [Timans et al., *Adaptive Bounding Box Uncertainties via Two-Step Conformal Prediction*, ECCV 2024 (arXiv:2403.07263)](https://arxiv.org/abs/2403.07263)
- [Küppers et al., *Multivariate Confidence Calibration for Object Detection*, CVPRW 2020](https://openaccess.thecvf.com/content_CVPRW_2020/papers/w20/Kuppers_Multivariate_Confidence_Calibration_for_Object_Detection_CVPRW_2020_paper.pdf) · [*Confidence Calibration for Object Detection and Segmentation* (arXiv:2202.12785)](https://arxiv.org/abs/2202.12785) · [Multiclass Confidence and Localization Calibration (arXiv:2306.08271)](https://arxiv.org/html/2306.08271)
- [Brett, *Thresholding with random field theory*](https://matthew-brett.github.io/teaching/random_fields.html) · [Brett, Penny, Kiebel, *An Introduction to Random Field Theory* (SPM book, Ch. 14)](https://www.fil.ion.ucl.ac.uk/spm/doc/books/hbf2/pdfs/Ch14.pdf) · [MRC-CBU, Thresholding with Random Field Theory](https://imaging.mrc-cbu.cam.ac.uk/imaging/PrinciplesRandomFields)
- [*Confidence Improves Self-Consistency in LLMs*, ACL Findings 2025](https://aclanthology.org/2025.findings-acl.1030.pdf)
- [*Two Samples Are Enough: Verbal Confidence Meets Self-Consistency in Reasoning LLMs*](https://openreview.net/forum?id=66D3rZrNjV)
- [*Reasoning Helps Surface Self-Confidence Signals in LLMs*, UncertaiNLP 2025](https://aclanthology.org/2025.uncertainlp-main.21.pdf)
- [ConfidenceBench: Evaluating Confidence Calibration in Large Language Models (arXiv:2607.20526)](https://arxiv.org/html/2607.20526)
- [sklearn StratifiedGroupKFold](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.StratifiedGroupKFold.html)