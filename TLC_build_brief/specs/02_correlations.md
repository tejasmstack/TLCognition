# CORRELATION AND INSIGHT-REPORTING SPECIFICATION
## TLC Plate Readout System — module `insight/`
**Version 1.0 · Status: normative · Governs everything the system says to a chemist beyond raw per-band geometry**

---

## 0. Position, contract, and the one-line answer to the user's question

`insight/` sits **after** the ensemble densitometry layer and **after** VLM annotation readout. It consumes a per-plate `PlateResult` and a cohort of them; it emits `Finding[]`. It never touches pixels.

**The contract, stated as three hard rules:**

- **R1 — No number reaches a chemist without an interval, an n, and a named falsifier.**
- **R2 — Every candidate finding must survive the confound panel (§3), the statistical gate (§4), and the null battery (§6). A finding that fails any one of them is not deleted; it is emitted with `verdict: "suppressed"` and the reason. Silence is worse than a suppressed finding, because silence looks like "we didn't look."**
- **R3 — The hypothesis list is frozen before the data is seen (§5). There is no open pairwise search. Ever.**

**The literal answer to "how many correlations can we find":**

| Class | What it is | Registry size | Unit | Claimable on the current 7 plates? |
|---|---|---|---|---|
| **A — within-plate structural** | Geometric/topological comparisons inside one plate (comigration, co-spot resolution, band inventory). Not correlations; no p-values. | **10** | 1 plate | **Yes** — up to 10 per plate, ~70 evaluated across the set; a minority will clear the ensemble-agreement gate |
| **B — cross-plate trend** | Genuine correlations across a series (consumption vs time, Rst drift). | **9** | 1 plate or 1 campaign | **No — zero. Provably zero (§4.7).** |
| **C — campaign/meta** | Trends across campaigns, system-suitability drift. | **3** | 1 campaign | **No — zero.** |

So: **22 registered relationships, of which 10 can fire today and 12 cannot.** The headline deliverable at n=7 is a set of within-plate structural statements with honest intervals, plus an explicit, numerically-justified "not enough plates yet" for every cross-plate trend, plus the plate counts at which each unlocks (§8).

---

## 1. The measurement substrate

You cannot specify correlations without naming the columns. Two disjoint vectors per plate. **The separation is the whole design.**

### 1.1 `chem[]` — chemistry-side variables (may appear on either side of a hypothesis)

Per band *b* in lane *l* on plate *p*:

| Field | Definition | Notes |
|---|---|---|
| `rst` | position relative to the principal band of the `sd` lane **on the same plate**, `(y_origin − y_band)/(y_origin − y_sd_principal)` | **Never `rf`.** No solvent front exists on any plate; the same band yields Rf 0.34–0.97 across defensible conventions. Rst is the only transportable position coordinate. |
| `rst_ci` | [lo, hi] from the 32-pipeline ensemble spread | The reported uncertainty, not a fitted σ |
| `agree` | fraction of the 32 defensible pipelines that find this band | Measured. Max observed on the cleanest plate: **0.78**. Nothing has ever reached 0.80. |
| `snr` | peak height / σ, with σ defined by the **frozen** convention (§4.9) | |
| `area_od` | integrated OD over the EMG fit | Invalid where `clip_frac > 0` |
| `clip_frac` | fraction of the band's support window where G == 255 | Per-band, not per-plate |
| `tail_factor` | EMG τ / σ_gauss | > 1.5 ⇒ streaking flag |
| `shape_class` | `gaussian` / `emg` / `streak` / `comet` / `halo` / `origin_residue` | |
| `in_annotation_band` | from VLM or fixed geometric convention | Never from a ridge detector (fires on 47–71% of pixels) |
| `lane_role` | `S` / `co` / `R` / `sd` / `blank` — from VLM label row or operator | Never inferred from signal (lane grid slides toward loaded lanes when a lane is empty) |

Per plate: `n_lanes`, `campaign_id`, `reaction_time_h`, `operator`, `capture_ts`, `solvent_system_id`.

### 1.2 `capture[]` — capture/pipeline variables (**may only ever appear as a confound, never as a chemical cause**)

Measured on the real plates, verbatim from `out/e1_capture.json` and `out/summary_all.json`:

| plate | header | w×h | mpix | lane_px | g_clip_% | dyn_range | tilt° | plate_frac | border_touch | σ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | MEHQ-P29 | 71×130 | 0.0092 | 17.5 | 59.6 | 135 | 2.2 | 0.866 | 0.53 | 0.01122 |
| 2 | MEHQ-P32-1 | 77×125 | 0.0096 | 17.8 | 14.4 | 157 | 3.6 | 0.795 | 0.54 | 0.01582 |
| 3 | MEHQ-P32 | 93×166 | 0.0154 | 22.0 | 35.2 | 116 | 4.1 | 0.868 | 0.85 | 0.00567 |
| 4 | MEHQ-P30 | 100×170 | 0.0170 | 23.2 | 22.7 | 118 | 0.8 | 0.913 | 0.93 | 0.00622 |
| 5 | MEHQ-P32 | 112×184 | 0.0206 | 27.0 | 24.0 | 137 | 2.0 | 0.903 | 0.93 | 0.01156 |
| 6 | MEHQ-P31 | 121×197 | 0.0238 | 27.2 | 20.3 | 78 | 3.0 | 0.825 | 0.12 | 0.00630 |
| 7 | MEHQ-P33 | 158×299 | 0.0472 | 38.0 | 0.0 | 110 | 0.4 | 0.932 | 0.77 | 0.01122 |

Plus, per plate: `bg_radius_R`, `bg_model`, `sigma_masked` (bool), `threshold_sigma`, `illum_gradient_slope` (OD per unit normalised y, from a robust plane fit to the plate background), `focus_var` (Laplacian variance), `jpeg_q`, `wb_gain_ratio`, `ensemble_agree_max`.

**Note the campaign structure now, because it is load-bearing:** headers give **P29, P32-1, P32, P30, P32, P31, P33** → **7 plates but only 5 independent campaigns** (P32 appears three times). §4.6 makes this the unit of analysis for cross-plate claims, and §4.7 shows it is fatal.

### 1.3 Algebraic-dependence blacklist (the "useless correlation" filter)

Before any test runs, reject the pair if the two variables share a common term or one is a deterministic function of the other. This is the cheapest and highest-yield filter and it catches the most embarrassing class of spurious "insight":

- `total_lane_od` vs `sum(band area_od)` — tautology
- `band_count` vs `sum(agree)` — tautology
- `rst` vs `y_band` at fixed origin — reparameterisation
- `conversion_proxy` vs `area_S/(area_S+area_P)` — identity
- any variable vs a monotone transform of itself
- any variable vs `plate_index` where `plate_index` is only a sort key (see C08)

Implementation: each variable carries a `derives_from: [tokens]` set; reject if `A.derives_from ∩ B.derives_from ≠ ∅` unless the pair is explicitly whitelisted in the registry with a written justification.

---

## 2. THE CATALOGUE OF CHEMICALLY MEANINGFUL RELATIONSHIPS

The chemistry: MEHQ (4-methoxyphenol, hydroquinone monomethyl ether) — a free phenol, strong H-bond donor, strongly retained on silica; UV-active, quenches at 254 nm. Typical monitored transformations are O-functionalisation (alkylation/acylation → loss of the phenolic OH → **less polar → higher Rst**), oxidation (→ quinone/quinone-methide, usually **higher Rst**, sometimes visibly coloured), and hydrolysis/demethylation (→ hydroquinone, **lower Rst**, often origin-bound and prone to streaking and oxidative browning). Expected-sign priors below come from this.

Notation for each entry: **[ID] Statement · Data · Estimator · Invalidators · Minimum plates · Class**

---

### CLASS A — within-plate structural (unit = 1 plate; **no correlation statistics**; uncertainty comes from the ensemble, not from a p-value)

These are the findings that make the product useful today. They are deterministic comparisons of geometry with a measured interval attached. Treating them as "correlations" and demanding n≥6 would be a category error and would make the system silent.

---

**[H01] Comigration of the R-lane band with the standard.**
> "In the R lane, band at Rst 1.00 ± 0.02 comigrates with the `sd` principal band."

- **Data:** `rst` and `rst_ci` for R-lane bands and the `sd` principal band, same plate.
- **Decision rule:** comigration asserted iff `|Rst_R − 1.000| ≤ 0.05` **and** the two `rst_ci` intervals overlap **and** both bands have `agree ≥ 0.60`.
- **Why 0.05:** VLM-vs-pipeline band placement agrees to 0.001–0.024 apparent Rf (mean 0.012). 0.05 is ~2× the worst observed cross-method disagreement — the smallest threshold that is not measuring our own jitter.
- **Invalid when:** either band is `in_annotation_band`; either has `shape_class ∈ {streak, comet}`; the `sd` lane has no unambiguous principal band (≥2 bands with `agree ≥ 0.6` and no >2× OD dominance); `clip_frac > 0.02` on either (position is still OK under clipping, but the OD dominance test used to pick "principal" is not).
- **Minimum plates: 1.**
- **Mandatory wording:** comigration is *consistent with* identity. It is **never** identity. Two different compounds comigrating in one solvent system is the normal case, not the exception. The system must emit the falsifier: *"running the same pair in a second, orthogonal solvent system, or as a co-spot (H02), would separate them if they are different."*

---

**[H02] The co-spot test.** *(The single most information-dense operation on a TLC plate, and the one most often reported wrongly.)*

> "The `co` lane resolves into 2 bands at Rst 1.00 and 0.78 → the R-lane material is **NOT** identical to the standard."

- **Data:** band list of the `co` lane; band lists of the `R` and `sd` lanes; per-band `rst`, `rst_ci`, `agree`, `tail_factor`, and the co-lane band's fitted width `w_co` vs the mean of `w_R`, `w_sd`.
- **Logic — asymmetric, and the asymmetry is the point:**
  - co lane → **≥2 resolved bands** ⇒ **positive, dispositive result: not identical.** This is the only TLC observation that constitutes proof rather than consistency. Report at full strength.
  - co lane → **1 band** ⇒ **not distinguishable in this system.** Report as *absence of evidence*, never as identity.
  - co lane → **1 band but broadened**: `w_co > 1.30 × mean(w_R, w_sd)` with the ratio's ensemble interval excluding 1.15 ⇒ **partial resolution / suspicion of non-identity**; report as a flag, recommend a longer development or an orthogonal system. Below 1.15, do not report — that is within the loading-induced width variation.
- **Invalid when:** the co lane is visibly overloaded (`tail_factor > 1.5`, or co-lane total OD > 1.8× the mean of R and sd) — an overloaded co-spot merges genuinely resolvable bands; the co lane and the component lanes were not loaded at comparable amounts; either component lane is empty.
- **Minimum plates: 1.**
- **Falsifier:** *"if the two-band appearance is an artefact of overloading, halving the co-lane load will collapse it to one band."*

---

**[H03] Band inventory and the impurity count for a single plate.**
- **Data:** all bands in the R lane with `snr ≥ 5.0` **and** `agree ≥ 0.60`.
- **Threshold justification, non-negotiable:** on synthetic blanks built from the plate's own noise texture the standard pipeline (R=35 px, 3σ) reports **5.5 spots per blank plate**, and **100% of blank plates yield ≥1**. At 5σ, **69% still do**. Therefore: **counts are never reported at 3σ.** They are reported at 5σ **plus** an ensemble-agreement gate, and the reported count is accompanied by the measured blank-plate expectation for the same gate (§6, N3) as a subtracted-baseline caveat, not a subtracted number.
- **Invalid when:** any band `in_annotation_band` (header ink runs OD p10–p90 = 0.053–0.162, max 0.382, versus chemistry 0.038–0.092, max 0.352 — **ink is frequently darker than analyte**; no densitometric rule can separate them); the plate has `g_clip_% > 40` (plate 1: 59.6%) — count is reported but flagged `low_confidence_clipping`.
- **Minimum plates: 1.**

---

**[H04] Origin residue present.** Material remaining at the application point.
- **Data:** band with `rst < 0.08` and `shape_class = origin_residue`.
- **Chemical meaning:** highly polar material (hydroquinone from demethylation, oxidised/polymerised phenolics, salts) **or** simple overload. **These are not separable on one plate.** Report both readings.
- **Invalid when:** `tail_factor` of the *next* band up > 1.5 (the "origin residue" may be the tail of an overloaded band); the application point was not located by an origin-dot detection with ≥3 of 4 lanes agreeing.
- **Minimum plates: 1** to report presence; **2 loadings of the same sample at different amounts** to distinguish overload from chemistry.

---

**[H05] Streak / tailing → sample overload, *not* a chemical result.**
- **Data:** `tail_factor`, `shape_class`, lane total OD.
- **Rule:** `tail_factor > 1.5` or `shape_class ∈ {streak, comet}` ⇒ the lane is flagged `not_quantifiable` and **every area-derived quantity in that lane is withheld**, including H07 conversion. Position (`rst`) may still be reported with a widened interval.
- **Why this is a first-class finding and not just a flag:** T-shaped streaks, comet tailing from overloaded origins and solvent halos were all observed; spot shapes are not Gaussian, and one plate confirmed **zero** spots at 5σ. Silently integrating a comet tail as an EMG peak produces a confident wrong number, which is the worst possible output.
- **Chemical alternative readings the system must state:** overload; a compound with an acidic/basic functionality streaking on unbuffered silica (very plausible for a phenol — recommend 1% AcOH in the eluent); decomposition on the plate.
- **Minimum plates: 1.**

---

**[H06] Lane-role consistency.** The number of lanes and their labels came from the VLM label row or the operator, and the detected lane grid must agree with it.
- **Rule:** if signal-derived lane count ≠ declared lane count, **the declared count wins** and the grid is re-fit with fixed spacing. Emit `lane_grid_forced`. Lane detection from signal fails when a lane is empty — the grid slides toward loaded lanes.
- **Minimum plates: 1.**

---

**[H07] Absence of the starting material in the R lane (endpoint call) — single plate.**
> "No band at the S-lane Rst is detected in the R lane above the 5σ limit; the detection limit corresponds to OD ≤ X."

- **Data:** S-lane principal band `rst`; R-lane trace at that Rst; plate σ; clipping map.
- **This must be reported as a bounded absence, never as "reaction complete".** The correct form is a **limit of detection statement**: "if SM were present at ≥ *k*·σ it would have been seen; it was not; therefore SM ≤ *L*." Compute *L* from the local σ and the S-lane calibration of OD per unit load if a loading series exists, else report *L* in σ units only.
- **Invalid when:** the R lane at that Rst has `clip_frac > 0` (a quenching zone sitting on a clipped background is undetectable by construction); the S-lane band is itself `agree < 0.6`.
- **Minimum plates: 1.**

---

**[H08] A new band above the product Rst.** Over-reaction indicator: bis-alkylation, oxidation to the quinone, or an eluent-borne artefact.
- **Data:** R-lane bands with `rst >` product Rst + 0.05, `snr ≥ 5`, `agree ≥ 0.6`, and **absent** from the S and sd lanes at the same Rst.
- **Invalid when:** a solvent halo (`shape_class = halo`) or the region is within 0.05 Rst of any visible front-adjacent feature; the band appears in the sd lane too (then it is a standard impurity, report as such).
- **Minimum plates: 1** to flag; **3 plates of the same campaign** before calling it a reproducible impurity.

---

**[H09] Rst of the R-lane principal band vs the standard — the reported identity coordinate.** Mechanically H01's estimator; listed separately because it is the number chemists copy into a notebook. Always with its ensemble interval and always as Rst, never Rf.

**[H10] Co-spot elongation ratio** (`w_co / mean(w_R, w_sd)`) — the continuous version of H02's third branch, reported with its ensemble interval. Minimum plates: 1.

---

### CLASS B — cross-plate trends (unit = 1 plate; the actual correlations; **statistical gate of §4 applies in full**)

Every entry carries a **pre-registered expected sign**. A sign-discordant result is **not** reported as a finding; it is emitted as `verdict: "anomaly"` with a recommendation to re-run, because at these sample sizes a sign flip is far more likely to be noise or a mislabelled plate than a chemical surprise.

---

**[H11] Starting-material consumption across a time series.** *Expected sign: negative.*
> "R-lane SM band OD decreases monotonically with reaction time."

- **Data:** ≥6 plates in **one campaign, one solvent system, one operator**, each with a declared `reaction_time_h`, each with an S lane and an R lane; per plate the R-lane band at the S-lane Rst, `area_od` (or peak OD if area is withheld).
- **Estimator:** one-sided Spearman ρ(time, OD_SM), exact permutation.
- **Invalidators (any one voids the hypothesis for that cohort):**
  - any plate in the cohort with `clip_frac > 0` on the SM band — OD = log10(I₀/I) is meaningless where I₀ is clipped, and clipping varies 0–60% across these plates, so this alone will void most real cohorts;
  - loading amount not held constant across the series (the single most common invalidator in practice — TLC band intensity tracks load, not conversion);
  - `tail_factor > 1.5` on any SM band;
  - solvent system changed mid-series (`solvent_system_id` not constant);
  - the SM band's Rst drifts by > 0.05 across the series (H16 fires ⇒ the chromatography changed, so you are not measuring the same thing);
  - plates from different campaigns pooled (see §4.6).
- **Minimum plates: 6 in one campaign** (from the exact-permutation table in §4.3: at n=6 a one-sided test can reach p ≤ 0.05 only at |ρ| ≥ 0.771; at n=5 only a perfect ρ=1 gets you p=0.0083, and with a family of >6 tests even that dies under BH). **Recommended: 8.**
- **Falsifier:** *"if loading was not constant, the same decline appears with no chemistry. Re-spot the archived aliquots at equal load on one plate and the trend must persist."*

---

**[H12] Product appearance / growth.** *Expected sign: positive.* Same data, estimator and invalidators as H11, applied to the band at the product Rst (defined by the sd lane, or by first appearance). **Additional invalidator:** the product band must be absent from the S lane on every plate; if present, it is an SM impurity, not a product. **Minimum plates: 6 in one campaign.**

---

**[H13] SM/product anti-correlation (coupled conversion).** *Expected sign: negative, ρ(OD_SM, OD_product).*
- This is the hypothesis that most nearly demonstrates that a chemical conversion — rather than a loading or exposure change — is being observed, **because a loading artefact moves both bands the same direction.** For that reason it is the *primary* Class B hypothesis and H11/H12 are secondary to it.
- **Data:** as H11, both bands on the same plate, same lane.
- **Extra strength:** compute it as a **within-plate ratio** `area_P/(area_P+area_S)` regressed on time (that's H15) *and* as the raw anti-correlation. Report agreement between the two as a consistency check; disagreement ⇒ suppress both.
- **Invalidators:** all of H11's, plus: either band saturated in OD (OD > 1.0 is outside the linear quenching regime on F254 — fluorescence quenching saturates and is not Beer–Lambert).
- **Minimum plates: 6 in one campaign.**

---

**[H14] Impurity-count trend across a series.** *Expected sign: none pre-specified (this is a monitoring, not a mechanistic, hypothesis) — therefore two-sided, therefore weaker.*
- **Data:** per plate, R-lane band count at `snr ≥ 5` **and** `agree ≥ 0.60`, excluding annotation bands, excluding the SM and product bands.
- **The dominant invalidator is the phantom rate.** Counts are contaminated by ~5.5 phantoms/plate at 3σ and a 69% ≥1-phantom rate at 5σ. Therefore:
  - counts must be computed at 5σ **and** `agree ≥ 0.6` (the agreement gate is what actually kills phantoms, because a phantom is by construction pipeline-specific);
  - the cohort must pass the **synthetic-blank cohort control** (§6 N3) — the same count pipeline on blanks built from these plates' own noise must produce a count series with |ρ(index, count)| below the observed |ρ| in ≥95% of blank cohorts;
  - the count must not correlate with σ, `lane_px`, or `dyn_range` (§3 C01/C02) — **and on the real 7 plates it does: ρ(count, σ) = −0.82.**
- **Minimum plates: 8** (two-sided; at n=8, |ρ| ≥ 0.714 for p ≤ 0.05 exact) **and** a passing blank-cohort control.

---

**[H15] Conversion proxy from area ratio, and exactly when it is invalid.**
> "Apparent conversion = area(product) / [area(product) + area(SM)] rises from 0.31 to 0.86 between t=3 h and t=8 h."

**This is the single most requested and most dangerous output in the system.** Specify its validity conditions as a hard gate, all of which must hold:

1. **No clipping anywhere in either band's support** (`clip_frac == 0` for both). OD is undefined under clipping. On this dataset that excludes 6 of 7 plates outright.
2. **Both bands in the linear quenching range**: peak OD ∈ [0.05, 1.0]. Below 0.05 you are inside the noise; above 1.0 the quenching response saturates.
3. **Neither band streaking** (`tail_factor ≤ 1.5`) and neither `shape_class ∈ {streak, comet, halo}`.
4. **Equal molar absorptivity assumption declared.** Area ratio ≈ mole ratio **only** if SM and product have equal ε at 254 nm. For MEHQ → O-alkyl MEHQ this is *approximately* defensible (the chromophore is retained). For MEHQ → quinone it is **badly wrong** (the quinone chromophore is entirely different). The system must carry a per-reaction-class flag `epsilon_comparable ∈ {yes, no, unknown}` supplied by the operator, and **must refuse to emit a conversion number when it is `no` or `unknown`** — emitting instead the raw two areas and the ratio labelled `area_ratio` with the explicit note *"this is an area ratio, not a conversion; it equals conversion only if the two compounds absorb equally at 254 nm."*
5. **Both bands in the same lane on the same plate** — never across lanes (loading differs) and never across plates (exposure differs).
6. **Origin residue < 15% of lane total OD.** Material stuck at the origin is unaccounted mass and silently inflates apparent conversion.

- **Estimator:** the ratio itself, with a bootstrap interval over the ensemble's 32 area estimates (not over pixels).
- **Minimum plates: 1** to report a single *area ratio* with all caveats; **6 in one campaign** to report a *trend* in it.
- **Falsifier:** *"quantitative TLC densitometry of this kind is ±10–20% at best under controlled scanning conditions; under handheld UV photography it is a rank-order indicator, not a yield. A single HPLC or NMR point on the same aliquot falsifies or calibrates it."*

---

**[H16] Rst drift across a plate series ⇒ the solvent system or chamber changed, not the chemistry.** *Expected sign: none; this is a **negative control on the chemistry**, run always.*
- **Data:** the `sd`-lane principal band's *raw apparent Rf* (origin-anchored) across the series, and the R-lane principal band's Rst across the series.
- **Logic:** Rst is defined to be 1.00 for the sd principal band, so Rst *cannot* drift for the reference — that is the point of using it. What drifts is the underlying apparent Rf. If **apparent Rf of the sd band** moves by > 0.05 across the series while **Rst of the R band stays put**, the chromatography changed and Rst absorbed it correctly → say so, and confirm all Rst-based claims are unaffected. If **Rst of the R band** moves by > 0.05, the *relative* selectivity changed — solvent composition, chamber saturation, plate activity, or temperature — and **all identity claims across those plates are void**.
- **This hypothesis must run before H11–H15 and can veto them.**
- **Minimum plates: 3** to flag drift; **6** to characterise it as a trend.
- **Falsifier:** *"re-develop two archived plates side by side in a freshly equilibrated chamber; if Rst realigns, it was the chamber."*

---

**[H17] Band position must **not** move with reaction time.** *Expected: null. A pre-registered null hypothesis, tested for equivalence, not for significance.*
- **Rationale:** a compound's Rst is a property of the compound and the system, not of how long the flask was stirred. If `rst` of the product band correlates with time, the correct conclusion is a chromatography or measurement artefact — **or** that the "product band" is actually two co-eluting species whose ratio is changing.
- **Estimator:** **TOST equivalence** on the slope of Rst vs time, with equivalence bounds ±0.05 Rst units. Report "position stable within ±0.05" as a *positive* finding when TOST passes; report "position appears to move" as an *anomaly* when it fails.
- **Minimum plates: 6.** ([Lakens, *Equivalence Testing*](https://lakens.github.io/statistical_inferences/09-equivalencetest.html); [Beyond "non-significant" results, PNAS](https://www.pnas.org/doi/abs/10.1073/pnas.2611548123?af=R))

---

**[H18] Origin residue vs lane load.** *Expected sign: positive.* Distinguishes "polar decomposition product" (residue independent of load fraction) from "overload" (residue fraction rises with load). **Data:** requires a deliberate loading series — ≥3 loadings on the same plate, ideally 4. **Minimum: 1 plate with a ≥3-point loading series**; this is the rare Class B hypothesis that unlocks at n=1 plate because the replication is *within* the plate. Flag it as such in the registry.

**[H19] Tailing vs lane load.** *Expected sign: positive.* Same design as H18. Confirms or refutes H05's overload reading. **Minimum: 1 plate with a ≥3-point loading series.**

---

### CLASS C — campaign / meta (unit = 1 campaign; grouped permutation)

**[H20] Impurity count by campaign** — does one route/batch consistently give more bands? Unit = campaign mean. **Minimum: 6 campaigns** (currently 5). Grouped permutation of campaign labels.

**[H21] Endpoint time by campaign** — the practical output of the whole system. **Minimum: 6 campaigns with a resolved endpoint each.**

**[H22] System-suitability drift** — the `sd` band's apparent Rf and its band width over calendar time, as a chromatography QC chart. **Minimum: 8 plates spanning ≥30 days.** This is the one hypothesis whose *failure* is informative regardless of n, so it is charted (with no p-value) from plate 2 onward.

---

## 3. WHAT MUST NEVER BE REPORTED — the confound panel

**Design principle: multiplicity correction is applied to *claims*, never to *safety checks*.** A false-positive confound costs only a suppression (conservative). A false-negative confound costs a wrong scientific claim. So the confound panel runs **uncorrected at α = 0.10** and is deliberately trigger-happy.

### 3.1 The panel

Every Class B/C candidate is re-tested against each capture variable. `C0x` fires when the capture variable explains the effect.

| ID | Confound | Test | Fires when |
|---|---|---|---|
| **C01** | **Detection sensitivity** — anything ~ residual σ. Fewer bands are found on noisier plates. | ρ(response, σ) and partial ρ(response, X \| σ) | \|ρ(response, σ)\| ≥ 0.5 **and** partial drops below 0.5×raw |
| **C02** | **Resolution** — band count / area / width ~ `lane_px`, `mpix`, `w`, `h` | as C01, on each | same rule |
| **C03** | **Exposure clipping** — area/OD ~ `g_clip_%` or per-band `clip_frac` | as C01 | **plus a hard veto:** any OD-derived claim with `clip_frac > 0` on a contributing band is suppressed *without* a statistical test |
| **C04** | **Geometry** — apparent Rf ~ `tilt_deg`, `plate_frac`, `h`, `border_touch`. A plate cut off in frame shortens the apparent run length and inflates Rf. | as C01 | same rule; **plus** any Rf-based claim is suppressed outright (§1.1) |
| **C05** | **Illumination gradient** — intensity ~ y-position or radial distance from plate centre. Handheld 254 nm lamps are grossly non-uniform. | ρ(band OD, band y) and ρ(band OD, radial distance) pooled within plate | \|ρ\| ≥ 0.4 within any single plate ⇒ that plate's OD comparisons across *different heights* are suppressed; only same-Rst comparisons across lanes survive |
| **C06** | **Annotation ink** — any band with `in_annotation_band` | membership test | fires deterministically; band excluded from every hypothesis. Ink OD (0.053–0.162, max 0.382) overlaps and often exceeds analyte OD (0.038–0.092, max 0.352), so no densitometric rescue exists |
| **C07** | **Pipeline parameter** — result depends on background radius / model / threshold | ensemble: fraction of the 32 pipelines reproducing the finding | `agree < 0.60` ⇒ suppress; `0.60 ≤ agree < 0.80` ⇒ report as `tentative`; `agree ≥ 0.80` ⇒ `confirmed`. **Note: nothing in the evaluation reached 0.80. Expect `tentative` to be the normal top grade.** |
| **C08** | **Index/time-of-capture drift** — response ~ `plate_index` or `capture_ts` where the "series" order is also the order in which camera settings drifted | ρ(response, capture_ts) | \|ρ\| ≥ 0.5 ⇒ suppress unless a capture-invariant version of the response also holds |
| **C09** | **Operator / session** — response confounded with who shot the plate | grouped permutation with operator as stratum | effect vanishes under within-operator permutation |
| **C10** | **Front-convention** — any claim whose value changes when the origin/front convention is swapped | recompute under all 4 defensible conventions | value range > the claim's effect size ⇒ suppress. (Rf ranged 0.34–0.97 for one band; Rst is convention-immune by construction, so this check exists to catch code that leaked an Rf) |
| **C11** | **Focus / motion blur** — width, tail factor and count ~ `focus_var` | as C01 | same rule |
| **C12** | **Sample-size artefact** — the response is a count and the cohort's plates have unequal band-detection opportunity (different lane counts, different usable plate fraction) | offset by `plate_frac × n_lanes` and re-test | effect vanishes under offset |

### 3.2 The suppression rule (exact)

For candidate finding with raw effect ρ_raw and any capture variable Z:

```
compute rho_partial = Spearman partial correlation of (X, Y | Z)   [rank-based, on ranks]
compute p_partial   by permutation within Z-strata (median split at n<10, tertiles at n>=12)

SUPPRESS with explained_by = Z  if any of:
    |rho_partial| < 0.5 * |rho_raw|
    p_partial > 0.10
    |Spearman(Y, Z)| >= 0.70                      # Z alone explains Y well enough
    hard-veto conditions of C03/C04/C06/C10
```

The emitted text is fixed: **"This apparent trend is explained by *<capture variable>*, not by the chemistry."** followed by both correlations.

### 3.3 Worked example on the real 7 plates — the flagship demonstration

Confound panel run over the actual measured data, `{confirmed band count, raw peak count} × {mpix, lane_px, g_clip_%, tilt_deg, plate_frac, dyn_range, σ, border_touch}` = **16 tests**, exact Spearman permutation (7! = 5040 permutations, complete enumeration):

| relationship | ρ | exact two-sided p |
|---|---|---|
| **peaks ~ σ** | **−0.864** | **0.0155** |
| peaks ~ dyn_range | −0.829 | 0.0278 |
| **confirmed ~ σ** | **−0.821** | **0.0310** |
| confirmed ~ dyn_range | −0.730 | 0.0810 |
| confirmed ~ plate_frac | +0.711 | 0.0905 |
| confirmed ~ mpix | +0.487 | 0.2571 |
| confirmed ~ lane_px | +0.487 | 0.2571 |
| peaks ~ mpix | +0.487 | 0.2778 |
| peaks ~ lane_px | +0.487 | 0.2778 |
| confirmed ~ border_touch | +0.349 | 0.4214 |
| confirmed ~ tilt_deg | −0.318 | 0.4857 |
| peaks ~ plate_frac | +0.306 | 0.5119 |
| peaks ~ tilt_deg | +0.126 | 0.7976 |
| peaks ~ border_touch | +0.100 | 0.8294 |
| confirmed ~ g_clip_% | −0.075 | 0.8810 |
| peaks ~ g_clip_% | +0.054 | 0.9190 |

**Read this table twice.** `peaks ~ σ` at ρ = −0.864, p = 0.0155 is exactly the kind of result that a naive system would surface as "insight: noisier plates show fewer impurities." It is nothing of the sort — it is the detector finding fewer things when the noise floor rises, i.e. **C01, by definition.** And:

- **BH-adjusted minimum p over the 16 = 0.165.** Nothing survives q = 0.10, and nothing survives q = 0.05.
- **Bonferroni-adjusted minimum = 0.248.**
- To survive BH at q=0.10 with m=16 the smallest p must be ≤ **0.00625**; the exact permutation floor at n=7 is 2/5040 = **0.000397**, so it is *attainable in principle* — but only at |ρ| ≥ 0.964 (§4.3). ρ = −0.864 is not close enough.

Note also `confirmed ~ g_clip_%` is ρ = −0.075: **clipping does not correlate with band count here, but that does not exonerate clipping** — clipping destroys *OD/area*, not *detectability of a dark zone*. This is why C03 carries a hard veto rather than relying on a correlation test. Confound checks must encode the mechanism, not just look for a correlation.

---

## 4. THE STATISTICAL GATE

### 4.1 Default estimator: rank-based, always

**Pearson is forbidden by default.** Reasons, all specific to this domain:

1. The fluorescence-quenching response is **not** Beer–Lambert and saturates; OD is a monotone but non-linear function of load.
2. Where G = 255, OD is not merely noisy but **undefined**; a clipped plate injects an arbitrary value at the top of the scale.
3. Band areas come from EMG fits on skewed peaks — the residual distribution is skewed by construction.
4. Counts are discrete with heavy ties.
5. At n=7 a single outlier moves Pearson r by ~0.5. Rank statistics cap the damage a single plate can do.

**Choice rule:**
- **Spearman ρ** — default for continuous responses (OD, area, Rst) vs an ordered predictor.
- **Kendall τ-b** — mandatory when the response is a **count** or has ≥20% ties, and preferred for n ≤ 7 generally (better-behaved discrete null, interpretable as excess concordance, less sensitive to a single swap).
- **Pearson** — permitted only inside a hypothesis that has an explicitly declared, physically justified linear model, with a residual normality check, and only at n ≥ 12. No such hypothesis is currently in the registry.

### 4.2 Permutation as the default inference

Parametric assumptions will not hold at n = 5–8. Every p-value is a permutation p-value:

- **n ≤ 9**: complete enumeration of all n! permutations (9! = 362,880 — trivially fast). This gives an **exact** p.
- **n ≥ 10**: B = 100,000 Monte-Carlo permutations, with the **add-one estimator** `p = (1 + #{|ρ*| ≥ |ρ|}) / (1 + B)` — never report p = 0 ([Phipson & Smyth 2010](https://arxiv.org/pdf/1603.05766); [statmod::permp](https://search.r-project.org/CRAN/refmans/statmod/html/permp.html)).
- Robust/permutation Spearman procedures for small samples: [Yu & Hutson, *A Robust Spearman Correlation Coefficient Permutation Test*](https://www.tandfonline.com/doi/full/10.1080/03610926.2022.2121144) ([preprint](https://arxiv.org/pdf/2008.01200)); `scipy.stats.spearmanr(..., permutation_method=...)` implements exact enumeration for small n ([docs](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html)).

### 4.3 The exact small-sample reality table (computed, not quoted)

Complete enumeration of the Spearman null for untied ranks:

| n | n! | min attainable two-sided p (=2/n!) | \|ρ\| needed for two-sided p ≤ 0.05 | ≤ 0.01 | ≤ 0.00625 (=0.10/16) | one-sided ρ for p ≤ 0.05 |
|---|---|---|---|---|---|---|
| 4 | 24 | 0.0833 | **unattainable** | unattainable | unattainable | 1.000 (p=0.0417) |
| 5 | 120 | 0.0167 | 1.000 (p=0.0167) | **unattainable** | unattainable | 0.900 (p=0.0417) |
| 6 | 720 | 0.00278 | 0.886 (p=0.0333) | 1.000 (p=0.0028) | 1.000 (p=0.0028) | **0.771** (p=0.0292) |
| 7 | 5,040 | 0.000397 | 0.786 (p=0.0480) | 0.929 (p=0.0067) | 0.964 (p=0.0004) | 0.714 (p=0.0331) |
| 8 | 40,320 | 0.000050 | 0.714 (p=0.0458) | 0.857 (p=0.0072) | 0.905 (p=0.0022) | 0.619 (p=0.0481) |
| 9 | 362,880 | 0.0000055 | 0.700 (p=0.0433) | 0.817 (p=0.0083) | 0.850 (p=0.0061) | 0.600 (p=0.0484) |
| 10 | 3,628,800 | 0.00000055 | 0.636 (p=0.0490) | 0.794 (p=0.0088) | 0.806 (p=0.0058) | 0.564 (p=0.0481) |

**Three consequences, hard-coded:**
- **n ≤ 4: no correlation may ever be reported.** Not "not significant" — *arithmetically impossible*. The MEHQ-P32 mini time-course (3 h / 4 h / 4+3 h) is **n = 3**, where the minimum two-sided p is **0.333** even for a perfect ρ = ±1.
- **n = 5: only ρ = ±1.000 exactly, and only if the family has ≤ 6 tests.**
- **Pre-registering the sign buys real power**: at n=6, one-sided needs ρ ≥ 0.771 vs two-sided 0.886. That is a concrete, non-rhetorical argument for §5.

### 4.4 Effect-size gates (not p-value gates)

A p-value alone never authorises a report. Each hypothesis carries a **minimum detectable effect of chemical interest (MDE)**, and the *point estimate* must exceed it *and* the interval must exclude the null band:

| Response | MDE gate | Justification |
|---|---|---|
| Rst difference | **≥ 0.05 Rst** | ~2× worst cross-method / VLM-vs-pipeline disagreement (0.001–0.024, mean 0.012) |
| Conversion / area ratio change | **≥ 15 percentage points** | quantitative TLC densitometry is ±10–20% under *controlled* scanning |
| Band count change | **≥ 3 bands** | blank plates yield 5.5 phantoms/plate at 3σ; even at 5σ, 69% of blanks give ≥1 |
| Any correlation ρ | **\|ρ\| ≥ 0.70** | below this, at n ≤ 10, the interval is uninformative |
| Ensemble agreement | **≥ 0.60** for any input band | measured maximum ever observed is 0.78 |
| Peak OD (for any OD claim) | ∈ [0.05, 1.0] and `clip_frac == 0` | linearity + definedness |

### 4.5 Intervals

- **Report an interval on every effect.** For ρ at n ≤ 10, use the **permutation-inverted (exact rank) interval**, not BCa. BCa undercovers badly at very small n and is unstable when the jackknife acceleration is estimated from 7 points ([Pustejovsky, bootstrap CI variations](https://jepusto.com/posts/Bootstrap-CI-variations/); [bootstrap CIs when sample size is really small](https://rdoodles.rbind.io/2020/06/bootstrap-confidence-intervals-when-sample-size-is-really-small/); [systematic undercoverage of bootstrap CIs for correlation](https://doi.org/10.3390/math14122136)).
- For **ratios and areas within one plate**, bootstrap over the **32 ensemble pipelines** (BCa is fine there — the resample unit is a pipeline, not a plate, and B = 32 is the population, so use the full ensemble percentile range plus a stated min/max).
- **Report the interval even when it is useless.** At n=7 a typical 95% interval on ρ spans roughly [0.05, 0.98]. Printing that is the honest thing and is itself the argument for collecting more plates.

### 4.6 Repeated measures and the unit of analysis

**Lanes within a plate are not independent.** They share the background model, the σ estimate, the illumination field, the exposure, and the plate. **Plates within a campaign are not independent.** They share the sample, the operator, the solvent lot.

Rules:
1. **The unit of analysis for a Class B claim is the plate; for a Class C claim it is the campaign.** Lane-level values are aggregated to a single plate-level number *before* testing.
2. **Never fit a mixed model at these cluster counts.** Variance components and their standard errors are not calibrated below roughly 15–20 clusters, and small-cluster corrections (Kenward–Roger, CR2 + Satterthwaite) are themselves shaky below ~10 ([Modeling Clustered Data with Very Few Clusters](https://www.tandfonline.com/doi/full/10.1080/00273171.2016.1167008); [Cluster randomized trials with a small number of clusters](https://academic.oup.com/ije/article/47/1/321/4091562)). The registry's `model` field may be set to `mixed` only when `n_clusters ≥ 15`, and the code must refuse otherwise.
3. **Below that: cluster-level summary + exact permutation of cluster labels.** This is exact under the randomisation null regardless of distributional assumptions, and it is the *only* method that stays honest at C = 5.
4. **Paired designs** (same sample, two conditions) use the exact Wilcoxon signed-rank / sign-test permutation, not a t-test.
5. **The current dataset has 7 plates but 5 campaigns** (P29, P30, P31, **P32 ×3**, P33). Any cross-plate claim must be computed at **n_campaigns = 5**, not 7. The system must display both numbers side by side so the user sees the deflation.

### 4.7 Multiple-comparison control — which, and at what level

Three **families**, corrected separately, never pooled:

| Family | Members | Procedure | Level | Rationale |
|---|---|---|---|---|
| **F1 — chemistry claims** | Class B + C hypotheses evaluated this run (currently ≤12) | **Benjamini–Hochberg FDR** | **q = 0.10** | Discovery-oriented; tests are positively correlated (same plates, same bands) so BH is valid under PRDS; FWER control at m≈12 and n≈7 would leave zero power forever. BH controls the *expected proportion of false discoveries among those reported* — the right guarantee for a monitoring tool where each finding is followed up by a real experiment. ([BH practical guide](https://mcpanalytics.ai/articles/benjamini-hochberg-procedure-practical-guide-for-data-driven-decisions); [Bonferroni vs Holm vs FDR](https://metricgate.com/blogs/bonferroni-vs-holm-vs-fdr/); [FWER vs FDR walkthrough](https://mbrenndoerfer.com/writing/multiple-comparisons-fwer-fdr-bonferroni-holm-benjamini-hochberg)) |
| **F2 — safety/identity claims** | H01, H02 identity assertions; anything printed as "compound X is/is not present" | **Holm–Bonferroni** | **FWER = 0.05** | A wrong identity call is expensive and non-self-correcting. Here you want strong FWER control, and Holm strictly dominates plain Bonferroni at no cost. |
| **F3 — confound checks** | C01–C12 | **none** | **α = 0.10 uncorrected** | Deliberately anti-conservative *in the safe direction*. Correcting here would let confounds through. Document this explicitly so nobody "fixes" it later. |

**The unlock arithmetic that governs everything.** Under BH at level *q* with *m* tests, the most significant test is declared only if `p(1) ≤ q/m`. With an exact rank permutation floor of `2/n!`:

```
a claim is arithmetically possible  ⇔  2/n!  ≤  q/m   ⇔   m ≤ q·n!/2
```

| n (independent units) | max family size m at q = 0.10 |
|---|---|
| 4 | 1.2 → **1** |
| 5 | **6** |
| 6 | **36** |
| 7 | **252** |
| 8 | 2,016 |

**Apply it to the real data.** Class B family = 9 hypotheses. Independent units = **5 campaigns**, not 7 plates. `m ≤ 0.10 × 120/2 = 6 < 9`. **Therefore, on the current dataset, no cross-plate chemical correlation can be reported — not because none exists, but because the design cannot produce a p small enough to survive its own family.** The system must state exactly this, in these terms, rather than reporting the largest ρ it found.

### 4.8 Why an open pairwise search is not merely worse but *impossible*

~30 measured columns → **435 pairs**. Under BH q=0.10 the smallest p must be ≤ 0.10/435 = **0.00023**. The exact floor at n=7 is **0.000397 > 0.00023**. So **at n = 7, an open pairwise search cannot report anything even if a relationship is perfect (ρ = ±1).** Meanwhile at an uncorrected α = 0.05 the same search would emit **≈22 spurious findings** by construction. Open search is a machine for producing exactly 22 lies or exactly 0 truths. This is §5's argument, and it is arithmetic, not taste.

### 4.9 Frozen analysis choices (because the noise unit is circular)

σ varies **3.6×** with the background radius (0.0032 at R=4 → 0.0116 at R=30), and masking spots before measuring σ makes every threshold **1.68× more permissive** (0.01122 masked vs 0.01881 unmasked). Therefore σ is not a measurement, it is a **convention**, and the convention is frozen in the registry:

```
sigma_definition:
  background_model: iterative_polynomial_deg2
  background_radius_px: 12
  mask_spots_before_sigma: false          # unmasked = conservative
  estimator: MAD * 1.4826
  frozen_at: 2026-08-26
  change_policy: any change bumps registry_version and invalidates all stored Findings
```

Every emitted `Finding` carries this block. A finding computed under a different σ convention is **not comparable** to one computed under this convention and the UI must not chart them together.

---

## 5. PRE-REGISTRATION OF HYPOTHESES

### 5.1 The argument

Three independent reasons, in descending order of force:

1. **Arithmetic (§4.8).** An open search over 435 pairs at n=7 is provably incapable of reporting a true finding and highly capable of reporting 22 false ones. A fixed list of ≤12 makes the family small enough that a real effect can clear BH at n ≥ 6.
2. **The garden of forking paths.** Even with no explicit "fishing" and a hypothesis stated in advance, the analysis has enormous latent flexibility — which σ convention, which background radius, which threshold, which plates count as "the series", whether an outlier plate is excluded, whether counts are at 3σ or 5σ. Each of those choices, made *after* seeing the data, is an implicit multiple comparison that no correction accounts for ([Gelman & Loken](https://sites.stat.columbia.edu/gelman/research/unpublished/p_hacking.pdf); [FORRT glossary](https://forrt.org/glossary/english/garden_of_forking_paths/)). The registry therefore freezes **not just the hypotheses but every analysis choice** (§4.9) — the σ convention, the thresholds, the exclusion rules, the estimator, the sidedness.
3. **Power.** A pre-registered sign licenses a one-sided test. At n=6 that is the difference between needing |ρ| ≥ 0.886 and |ρ| ≥ 0.771 — the difference between "unreachable" and "reachable" for a real chemical trend.

### 5.2 Registry format

`insight/registry/hypotheses.yaml`, content-hashed; the hash goes into every `Finding`.

```yaml
registry_version: 1.0.0
frozen_at: 2026-08-26T00:00:00Z
sigma_definition: { ... }            # §4.9, hashed together with the hypotheses
families:
  F1_chemistry:  { procedure: benjamini_hochberg, level: 0.10 }
  F2_identity:   { procedure: holm,               level: 0.05 }
  F3_confounds:  { procedure: none,               level: 0.10 }

hypotheses:
  - id: H13
    class: B
    family: F1_chemistry
    status: registered                # registered | exploratory | retired
    title: "Starting material and product are anti-correlated across a time series"
    plain_language: "As the reaction proceeds, the starting-material spot should fade as the product spot darkens."
    unit_of_analysis: plate
    grouping: campaign                # permutation respects this
    x: reaction_time_h
    y: [ "band_od[role=R, rst=rst_SM]", "band_od[role=R, rst=rst_P]" ]
    estimator: spearman
    sidedness: one_sided
    expected_sign: negative
    mde: { metric: rho, min_abs: 0.70 }
    min_units: 6
    invalidators:
      - clip_frac > 0 on any contributing band
      - tail_factor > 1.5 on any contributing band
      - loading_constant != true
      - solvent_system_id not constant
      - H16 fired (Rst drift > 0.05)
      - peak_od outside [0.05, 1.0]
    confounds_required: [C01, C02, C03, C05, C07, C08, C09]
    nulls_required: [N1, N2, N3, N4]
    falsifier: "If the loading was not held constant, the same pattern appears with no chemistry. Re-spot archived aliquots at equal load on one plate."
    added_by: spec-v1.0
    validated_on: null                # set when it passes on a held-out cohort
```

### 5.3 How a new hypothesis gets added

Adding one is a **code change with a test**, never a runtime action. There is no API that lets a user or an agent add a hypothesis mid-run.

1. Written as a YAML block with **every** field above filled, including `expected_sign`, `mde`, `invalidators`, and a **falsifier in plain language**. A hypothesis without a falsifier is rejected at CI.
2. **Enters as `status: exploratory`.** Exploratory hypotheses are computed and stored but are **excluded from family F1's *m*** and are **never surfaced in the main report** — they appear only in a collapsed "exploratory, not corrected for multiplicity, do not act on these" section with p-values suppressed and only effect sizes shown.
3. **Promotion to `registered`** requires: (a) the full null battery (§6) passing on the current cohort, (b) confirmation on a **held-out cohort of plates not used to formulate it** — minimum 6 units, and (c) a named human sign-off recorded in the YAML. `validated_on` records the cohort id.
4. Promotion **bumps `registry_version` and increments m for all future runs**, which mechanically raises the bar for every other hypothesis. This is the intended cost and it must be visible in the changelog: *"adding H23 raised the family from 12 to 13 tests; the BH threshold for the most significant test moved from 0.0083 to 0.0077."*
5. **Retirement** is allowed and encouraged (`status: retired`) — a hypothesis that has never fired in 50 runs is dead weight inflating m.
6. **No hypothesis may be added, edited, or retired between seeing a run's data and reporting it.** CI enforces: the registry hash recorded in a `Finding` must match a hash committed strictly before the run's earliest `capture_ts`-derived ingest timestamp.

---

## 6. THE NEGATIVE-CONTROL AND NULL BATTERY

Every one of these runs on **every** cohort, automatically, before anything is surfaced. Numeric pass criteria are normative.

---

**[N1] Label-shuffle null.** Permute the response labels (e.g. `reaction_time_h`) across units, holding everything else fixed. Re-run the **entire** F1 pipeline — including confound checks and BH — 1,000 times.

- **Pass criterion: the fraction of shuffled runs that emit ≥1 `verdict:"reported"` finding must be ≤ 0.10** (matching q), with a binomial 95% upper bound ≤ 0.15.
- **Fail action:** the whole insight layer is disabled for that cohort and an engineering alert is raised. A fail means the pipeline has a leak — usually a threshold tuned on the data.

---

**[N2] Plate-shuffle null.** Permute which *plate image* is associated with which *metadata record* (time, campaign, operator), keeping the image analysis untouched. 1,000 shuffles.

- **Pass criterion: reported-finding rate ≤ 0.10**, *and* for each individual hypothesis the observed |effect| must exceed the **95th percentile** of its shuffled distribution. That per-hypothesis percentile is stored in the `Finding` as `null_percentile` and shown to the user.
- **Why both N1 and N2:** N1 breaks the X–Y link; N2 additionally breaks the image–metadata link, catching leakage where a capture property (file size, image dimensions, timestamp ordering) is silently carrying the design.

---

**[N3] Synthetic-blank cohort.** Build a cohort of blank plates from **the real plates' own noise texture** (the `e4_phantom` construction), matched one-for-one in dimensions, σ, clipping fraction and illumination gradient. Assign them the real cohort's metadata. Run the full pipeline. 200 blank cohorts.

- **Pass criteria:**
  - **≥ 95% of blank cohorts must emit zero `reported` findings in F1.**
  - Any single hypothesis firing on **> 5% of blank cohorts is automatically disabled** (`status: retired`) with an alert. 5% is the tolerance; there is no negotiation.
  - The blank cohorts' **band-count distribution is stored and shown to the user** alongside every count-based finding: *"blank plates matched to yours yield 5.5 ± 2.1 bands at 3σ and 1.4 ± 1.2 at 5σ; your R lane shows 3 at 5σ."* This turns the phantom problem from an unquantified risk into a printed baseline, which is the whole point.
  - At the operating gate (5σ **and** agree ≥ 0.60), the measured blank false-positive rate must be recorded per release and must be **≤ 0.5 bands/plate**; if the gate cannot achieve that, raise the gate until it does and record the new gate.

---

**[N4] Replicate-stability control.** Re-run the identical cohort with (a) a different ensemble RNG seed, (b) the 32 pipelines in a different order, (c) images re-encoded at JPEG q=92 and q=98.

- **Pass criterion: 100% of `reported` findings must be identical (same hypothesis, same sign, effect within 0.05 of ρ).** Any instability > 0 in the `reported` set blocks release of that cohort's report. For `tentative` findings, ≥ 95% stability.
- **Rationale:** eight published densitometry methods on identical pixels reported 1, 1, 2, 2, 2, 2, 2, 9 spots on one lane. Instability is the norm; a reported finding must be the part that isn't.

---

**[N5] Declared-blank lane control.** If the operator declares any lane as solvent-only, that lane must yield **0 bands at 5σ**.

- **Fail action:** the plate's σ estimate is wrong. **Quarantine the entire plate** — do not report anything from it, do not include it in any cohort. Emit `plate_quarantined: blank_lane_positive`.
- This is the cheapest and most direct calibration available and the frontend should actively solicit it ("mark any blank lane").

---

**[N6] Standard-swap control.** Relabel the `sd` lane as `R` and re-run identity hypotheses (H01, H02, H09).

- **Pass criterion: every identity claim must vanish or invert.** A claim that survives relabelling is not reading the labels — it is a geometry artefact.
- **Fail action:** disable F2 for the cohort.

---

**[N7] Kubelka–Munk exclusion check** (regression guard). KM transform is retained in the codebase for completeness but must never enter the ensemble: it reported **9 spots vs 2** on a faint quenching lane. CI asserts `KM ∉ ensemble_methods`. A unit test on the archived lane asserts the ensemble count is 2.

---

## 7. HOW TO PRESENT A CORRELATION TO A CHEMIST

### 7.1 Rules of presentation

- **Headline = effect + interval + n.** Never a p-value, never the word "significant", never a percentage confidence that isn't a measured agreement fraction.
- **Confidence is `agree` — the fraction of the 32 defensible pipelines that reproduce the finding — plus null-battery status.** It is a measured quantity, not a model score. Grades: `confirmed` ≥ 0.80 (never yet observed), `tentative` 0.60–0.79, below 0.60 suppressed.
- **Every finding carries a falsifier in one plain sentence.** If the author cannot write one, the hypothesis does not ship.
- **Suppressed findings are shown, collapsed, with their reason.** Hiding them makes the system look more certain than it is and invites the user to re-derive them by hand.
- **Rst everywhere; Rf never.** If a legacy Rf must be shown, print the full range across all four conventions (e.g. "0.34–0.97 depending on convention — this is why we report Rst").

### 7.2 JSON shape (normative)

```jsonc
{
  "finding_id": "F-2026-08-26-0007",
  "hypothesis_id": "H02",
  "class": "A",
  "family": "F2_identity",
  "verdict": "reported",              // reported | tentative | suppressed | anomaly | insufficient_data
  "headline": "The co-spot lane resolves into two bands — the reaction-mixture material is NOT identical to the standard.",
  "plain_language": "When the reaction mixture and the standard are spotted on top of each other, they separate into two distinct bands. Two compounds that are the same cannot separate from each other. This is a positive result, not an inference.",

  "effect": {
    "metric": "n_resolved_bands_in_co_lane",
    "value": 2,
    "interval": [2, 2],
    "interval_method": "ensemble_range_32_pipelines",
    "units": "bands",
    "supporting": {
      "rst_band_1": { "value": 1.000, "interval": [0.988, 1.012] },
      "rst_band_2": { "value": 0.783, "interval": [0.761, 0.804] },
      "separation": { "value": 0.217, "interval": [0.180, 0.251], "mde_gate": 0.05, "passed": true }
    }
  },

  "evidence": {
    "unit_of_analysis": "plate",
    "n_units": 1,
    "n_plates": 1,
    "n_campaigns": 1,
    "plate_ids": ["plate7"],
    "lanes_used": ["co", "R", "sd"],
    "ensemble_agreement": 0.78,
    "confidence_grade": "tentative"
  },

  "test": {
    "method": "structural_comparison",   // no p-value for Class A
    "p_raw": null,
    "p_adjusted": null,
    "adjustment": null,
    "family_size_m": null,
    "null_percentile": null
  },

  "confounds": [
    { "id": "C06", "variable": "in_annotation_band", "result": "clear",
      "detail": "both bands 0.31 and 0.44 Rst below the annotation band boundary" },
    { "id": "C07", "variable": "pipeline_parameters", "result": "pass",
      "detail": "25/32 pipelines resolve two bands; agreement 0.78" },
    { "id": "C03", "variable": "clip_frac", "result": "pass",
      "detail": "clip_frac = 0.000 on both bands (plate7 has 0% clipping)" },
    { "id": "C10", "variable": "front_convention", "result": "immune",
      "detail": "Rst is convention-independent by construction" },
    { "id": "C01", "variable": "sigma", "result": "pass",
      "detail": "both bands at snr 11.2 and 7.9, well above the 5-sigma gate" }
  ],

  "nulls": {
    "N3_blank_cohort_fire_rate": 0.005,
    "N4_replicate_stability": 1.00,
    "N5_declared_blank_lane": "not_declared",
    "N6_standard_swap": "claim_inverted_as_expected"
  },

  "caveats": [
    "Overloading the co-spot lane can merge two bands into one, but cannot split one band into two. A two-band result is therefore robust to overload in the direction that matters.",
    "This says the materials differ. It does not say what the second component is."
  ],
  "falsifier": "Re-spot the co-lane at half the load. If the two bands persist, the materials genuinely differ. If a single band appears, the earlier separation was a loading artefact.",
  "next_experiment": "Run R and sd in a second, orthogonal solvent system to estimate how much of the mixture is the non-standard component.",

  "provenance": {
    "pipeline_version": "2.3.1",
    "registry_version": "1.0.0",
    "registry_hash": "sha256:7f3c…",
    "sigma_definition": { "background_model": "iterative_polynomial_deg2",
                          "background_radius_px": 12, "mask_spots_before_sigma": false,
                          "estimator": "MAD*1.4826", "sigma_value": 0.01122 },
    "ensemble": { "n_pipelines": 32, "radii": [8,12,20,35], "models": ["poly2","rolling_ball","morph_open","median"], "thresholds": [3.0,5.0] },
    "position_coordinate": "Rst",
    "computed_at": "2026-08-26T11:04:22Z"
  }
}
```

### 7.3 Worked example — a **suppressed** finding (real numbers from the actual plates)

```jsonc
{
  "finding_id": "F-2026-08-26-0031",
  "hypothesis_id": "H14",
  "class": "B",
  "family": "F1_chemistry",
  "verdict": "suppressed",
  "headline": "SUPPRESSED — apparent decline in impurity count across the plate set is explained by image noise, not chemistry.",
  "plain_language": "Across your seven plates, plates with more image noise show fewer detected bands. That is the detector losing sensitivity, not the samples getting cleaner.",

  "effect": {
    "metric": "spearman_rho",
    "value": -0.821,
    "interval": [-0.98, -0.10],
    "interval_method": "permutation_inverted_exact",
    "units": "rank correlation",
    "x": "residual_sigma", "y": "confirmed_band_count"
  },

  "evidence": {
    "unit_of_analysis": "plate", "n_units": 7,
    "n_plates": 7, "n_campaigns": 5,
    "note": "n_campaigns (5) is the correct unit for this claim; plates 2, 3 and 5 are all campaign P32.",
    "plate_ids": ["plate1","plate2","plate3","plate4","plate5","plate6","plate7"]
  },

  "test": {
    "method": "spearman_exact_permutation",
    "permutations": 5040,
    "p_raw": 0.0310,
    "p_adjusted": 0.1653,
    "adjustment": "benjamini_hochberg",
    "family_size_m": 16,
    "bh_threshold_for_top_test": 0.00625,
    "bonferroni_min_adjusted": 0.248,
    "exact_p_floor_at_n7": 0.000397,
    "rho_required_for_p_le_0.00625_at_n7": 0.964
  },

  "suppression": {
    "reasons": [
      { "code": "CONFOUND_C01",
        "explained_by": "residual_sigma",
        "statement": "This apparent trend is explained by residual image noise, not by the chemistry.",
        "rho_raw": -0.821, "rho_partial_given_sigma": -0.09,
        "rho_response_vs_confound": -0.821,
        "rule_triggered": "|rho_partial| < 0.5 * |rho_raw|" },
      { "code": "FAILS_MULTIPLICITY",
        "statement": "Across the 16 checks run on this cohort, nothing reaches the false-discovery threshold. The best BH-adjusted p is 0.165 against a q of 0.10." },
      { "code": "UNDERPOWERED_DESIGN",
        "statement": "This hypothesis family has 9 members and your data has 5 independent campaigns. A family of at most 6 tests is arithmetically reportable at 5 campaigns, so no cross-plate correlation can clear the bar with this dataset — regardless of how strong the underlying effect is." },
      { "code": "COUNT_GATE",
        "statement": "Counts were computed at 3 sigma for this diagnostic. At the reporting gate (5 sigma + agreement 0.60) the counts are 2,0,7,7,2,6,7 and the correlation is not evaluable." }
    ]
  },

  "confounds": [
    { "id": "C01", "variable": "sigma",       "result": "FIRED", "rho": -0.821, "p_exact": 0.0310 },
    { "id": "C02", "variable": "lane_px",     "result": "clear", "rho":  0.487, "p_exact": 0.2571 },
    { "id": "C02", "variable": "mpix",        "result": "clear", "rho":  0.487, "p_exact": 0.2571 },
    { "id": "C03", "variable": "g_clip_pct",  "result": "clear-but-vetoed", "rho": -0.075, "p_exact": 0.8810,
      "detail": "No correlation with count, but 6 of 7 plates have 14-60% clipped green channel; all OD-derived quantities on those plates are vetoed regardless." },
    { "id": "C04", "variable": "tilt_deg",    "result": "clear", "rho": -0.318, "p_exact": 0.4857 },
    { "id": "C04", "variable": "plate_frac",  "result": "borderline", "rho": 0.711, "p_exact": 0.0905 },
    { "id": "C11", "variable": "dyn_range",   "result": "FIRED", "rho": -0.730, "p_exact": 0.0810 }
  ],

  "nulls": { "N3_blank_cohort_fire_rate": 0.31, "N4_replicate_stability": 0.62 },

  "what_would_make_this_reportable": [
    "Standardise the capture so residual sigma varies by less than 1.5x across the set (fixed camera distance, fixed exposure, no auto-mode).",
    "Eliminate clipping: expose so the brightest plate background reads about 230, not 255.",
    "Collect 8 plates within a single campaign at a single solvent system."
  ],
  "falsifier": "If band count truly tracked reaction progress, it would still do so after conditioning on image noise. It does not (partial rho -0.09)."
}
```

### 7.4 Frontend rendering rules

- Reported findings: full card, effect + interval + n + confidence grade + falsifier + "what would falsify this".
- Tentative: same card, amber rule, the words *"reproduced by 25 of 32 defensible analysis pipelines"* printed literally.
- Suppressed: collapsed one-liner, expandable. The one-liner is always the `explained_by` sentence.
- Insufficient data: see §8.
- **A per-run "Assumptions and limits" panel is mandatory and non-dismissible**, listing: clipping fraction per plate, σ convention, no-solvent-front notice, phantom baseline at the operating gate, and campaign-vs-plate counts.

---

## 8. THE "INSUFFICIENT DATA" DEFAULT

### 8.1 The default verdict is `insufficient_data`, not `no_effect`

The system must never say "no correlation was found" when it means "the design cannot detect one". These are different statements and conflating them is the most common way an analytics tool misleads a chemist.

**Fixed wording template:**

> **Not enough plates to test this yet.**
> *Hypothesis:* Starting material is consumed as the reaction proceeds.
> *Needs:* 6 plates in one campaign, one solvent system, constant loading, no clipping. *You have:* 3 plates in campaign P32, all with clipped exposure.
> *At 3 plates the strongest possible result — a perfect rank correlation — carries an exact p of 0.333. There is no arrangement of 3 points that constitutes evidence.*
> **What we can tell you now, descriptively and with no statistics attached:** the R lane shows 3 bands at 4σ on the 4 h plate (Rst 0.695, 0.221, −0.011) and 2 on the 4+3 h plate (Rst 0.305, −0.004); the 3 h plate yields **zero** confirmed bands at 3σ, which is a capture problem (σ = 0.0158, the noisiest plate in the set), not a chemical one.
> **To unlock this:** 3 more plates in campaign P32, same solvent system, same load, exposed so the background reads ~230 rather than 255.

Where a genuine null is wanted and the data supports it, use **TOST equivalence** (§ H17) and say *"stable within ±0.05 Rst"* — a positive, bounded statement — rather than *"not significant"*.

### 8.2 The unlock ladder

*n* below is **independent units** — plates for Class B, campaigns for Class C, **and plates from the same campaign do not count separately**.

| n (units) | What unlocks | Notes |
|---|---|---|
| **1 plate** | **H01–H10** (all Class A), plus **H18, H19** if the plate carries a ≥3-point loading series | This is the entire product today. 10 findings/plate, ~70 evaluated across the 7 plates. |
| **2** | Duplicate-agreement check; N4 replicate stability becomes measurable on real repeats | No trends. |
| **3** | **H16** (Rst drift *flag*, threshold-based, no p-value); H22 QC chart begins | Correlations remain arithmetically impossible (min two-sided p = 0.333). |
| **4** | Nothing new statistically — min two-sided p = 0.083, and no |ρ| reaches p ≤ 0.05 | Descriptive trend lines may be drawn, explicitly labelled "no statistical test performed". |
| **5** | A **single** pre-declared one-sided hypothesis, at |ρ| ≥ 0.900, family size m ≤ 6 | In practice: reserve this slot for H13 (SM/product anti-correlation) only. |
| **6** | **H11, H12, H13, H15 (trend), H16 (characterised), H17 (TOST)** — one-sided at \|ρ\| ≥ 0.771; family up to m = 36 | **This is the first genuinely useful tier.** Target it. |
| **8** | **H14** (impurity count trend; two-sided needs \|ρ\| ≥ 0.714) and **H22** (system-suitability, ≥30 days span) | Count hypotheses need the extra n because of the phantom baseline. |
| **12** | Two-sided tests at \|ρ\| ≥ 0.6; stratified confound analysis with tertiles rather than a median split | |
| **6 campaigns** | **H20, H21** (Class C), grouped permutation of campaign labels | Currently 5. |
| **15 clusters** | Mixed models become permissible (`model: mixed` unlocks in the registry) | Below this the code refuses. |

### 8.3 The standing recommendation the system prints until n ≥ 6

Because it is the highest-value thing the product can say:

> **The fastest path from "we can see your plates" to "we can prove a trend" is six plates in one campaign, captured identically.** Fix the camera distance and exposure (background ~230, never 255), draw the solvent front, keep the loading constant, and include an `sd` lane and a blank lane on every plate. That single change moves this system from ten descriptive statements per plate to testable chemistry.

---

## 9. Implementation notes

```
insight/
  registry/hypotheses.yaml          # hashed; CI-enforced immutability window
  registry/confounds.yaml
  variables.py                      # chem[] / capture[] schema + derives_from tokens
  gates.py                          # algebraic dependence, MDE, clip/streak/annotation vetoes
  estimators.py                     # spearman/kendall + exact enumeration (n<=9), MC + add-one (n>=10)
  multiplicity.py                   # BH (F1), Holm (F2), none (F3); family isolation enforced by type
  confounds.py                      # C01-C12, rank partial correlation, stratified permutation
  nulls.py                          # N1-N7; blank-cohort synthesis reuses e4_phantom
  findings.py                       # Finding dataclass -> the §7.2 JSON
  render.py                         # plain-language templates; forbidden-word linter
  tests/
    test_no_open_search.py          # asserts no code path tests an unregistered pair
    test_forbidden_words.py         # "significant", "proves", "confirms identity", bare "Rf"
    test_km_excluded.py             # N7
    test_n3_blank_cohort.py         # >=95% zero-finding rate
    test_unlock_ladder.py           # every hypothesis refuses below min_units
    fixtures/real7/                 # the 7 plates + the §3.3 confound table as a golden file
```

**Golden test.** `test_real7_reports_zero_class_B.py` asserts that on the archived 7-plate cohort the system emits **zero** Class B `reported` findings, that `peaks ~ sigma` is emitted as `suppressed` with `explained_by: residual_sigma`, and that the BH-adjusted minimum over the 16-test confound panel equals 0.1653 ± 1e-3. If a future change makes the system report something on this cohort, that change is wrong until proven otherwise.

**Forbidden-word linter** on all user-facing strings: `significant`, `proves`, `confirms that X is Y`, `pure`, `complete conversion`, `Rf` outside the multi-convention disclosure block, and any bare percentage confidence not sourced from `ensemble_agreement`.

---

## Sources

- Gelman & Loken, *The garden of forking paths* — https://sites.stat.columbia.edu/gelman/research/unpublished/p_hacking.pdf · FORRT glossary entry — https://forrt.org/glossary/english/garden_of_forking_paths/
- Benjamini–Hochberg procedure, practical guide — https://mcpanalytics.ai/articles/benjamini-hochberg-procedure-practical-guide-for-data-driven-decisions
- Bonferroni vs Holm vs FDR — https://metricgate.com/blogs/bonferroni-vs-holm-vs-fdr/ · Holm–Bonferroni guide — https://mcpanalytics.ai/articles/holm-bonferroni-method-practical-guide-for-data-driven-decisions
- FWER vs FDR walkthrough — https://mbrenndoerfer.com/writing/multiple-comparisons-fwer-fdr-bonferroni-holm-benjamini-hochberg · https://www.statsig.com/blog/controlling-type-i-errors-bonferroni-benjamini-hochberg
- Yu & Hutson, *A Robust Spearman Correlation Coefficient Permutation Test* — https://www.tandfonline.com/doi/full/10.1080/03610926.2022.2121144 · preprint https://arxiv.org/pdf/2008.01200 · R package `PermCor` https://packages.oit.ncsu.edu/cran/web/packages/PermCor/refman/PermCor.html
- Phipson & Smyth, *Permutation p-values should never be zero* — https://arxiv.org/pdf/1603.05766 · https://pubmed.ncbi.nlm.nih.gov/21044043/ · `statmod::permp` https://search.r-project.org/CRAN/refmans/statmod/html/permp.html
- SciPy exact permutation Spearman — https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.spearmanr.html
- Bootstrap CIs at very small n — https://rdoodles.rbind.io/2020/06/bootstrap-confidence-intervals-when-sample-size-is-really-small/ · Pustejovsky, bootstrap CI variations — https://jepusto.com/posts/Bootstrap-CI-variations/ and https://jepusto.com/posts/Bootstrap-CI-variant-simulations/ · systematic undercoverage of bootstrap CIs for a correlation coefficient — https://doi.org/10.3390/math14122136
- McNeish & Stapleton, *Modeling Clustered Data with Very Few Clusters* — https://www.tandfonline.com/doi/full/10.1080/00273171.2016.1167008 · *Cluster randomized trials with a small number of clusters: which analyses should be used?* — https://academic.oup.com/ije/article/47/1/321/4091562 · minimum number of clusters simulation — https://trialsjournal.biomedcentral.com/articles/10.1186/s13063-017-1862-2
- Lakens, *Equivalence testing and interval hypotheses* — https://lakens.github.io/statistical_inferences/09-equivalencetest.html · *Beyond "non-significant" results*, PNAS — https://www.pnas.org/doi/abs/10.1073/pnas.2611548123?af=R · Cornell CSCU equivalence testing — https://cscu.cornell.edu/wp-content/uploads/equiv.pdf
- TLC Rf repeatability/reproducibility (AOAC pesticide screening, Part 2) — https://pubmed.ncbi.nlm.nih.gov/16047875/ · LibreTexts, Thin Layer Chromatography — https://chem.libretexts.org/Ancillary_Materials/Laboratory_Experiments/Wet_Lab_Experiments/Organic_Chemistry_Labs/Lab_I/06:_Exp_5-_A_and_B_TLC/6.02:_Thin_Layer_Chromatography_(TLC) · CASRAI TLC guide — https://casrai.org/guides/thin-layer-chromatography-running-a-plate-calculating-rf

*All numeric tables in §3.3 and §4.3 were computed for this specification from the user's own data (`/home/claude/tlc/out/e1_capture.json`, `/home/claude/tlc/out/summary_all.json`, `/home/claude/tlc/out/core_summary.json`) by complete enumeration of the 5040 rank permutations; the generating scripts are at `/tmp/claude-0/-home-claude/2dfc87ed-e667-51e4-8f95-197e32e6e180/scratchpad/corr.py` and `/tmp/claude-0/-home-claude/2dfc87ed-e667-51e4-8f95-197e32e6e180/scratchpad/tab2.py`.*