# EVAL REPORT EXTRACT — "Turning TLC plate photographs into numbers"

Source: `/Users/tejas.ghatule/Documents/research/TLC_method_evaluation.pdf` (40 pages, Mstack Chemicals, dated 25 August 2026).
Purpose of this extract: sole surviving porting source for the lost code bundle. Every quantitative method detail transcribed verbatim, with page references `[p.N]`.

Plates studied: MEHQ-P29, P30, P31, P32, P32-1, P33 — 7 unique photographs [p.1]. Environment of the original code: Python 3, numpy 2.4.4, scipy 1.17.1, scikit-image 0.26.0, OpenCV 4.13.0, Tesseract 5.3.4 (leptonica 1.82.0), EasyOCR 1.7, Ultralytics 8.4.128 (YOLO11n); all single-threaded CPU [p.39].

Code bundle file map (lost, but names & contents listed) [p.39]:
- `tlclib.py` — reference implementations: plate detection, rectification, **seven background models**, OD and Kubelka–Munk, noise estimation, stroke and text-band masks, lane finding, origin-dot and front-line detection, Gaussian and EMG peak fitting.
- `common.py` — per-plate preparation, hand-supplied annotation bands, reference transcriptions.
- `e1_capture.py` (capture audit), `e2_walkthrough.py`, `e3_methods.py` (8 methods), `e4_phantom.py` (radius sweep + null test A), `e4b_null.py` (null test B + noise-masking bias), `e7_rf.py` (Rf convention sensitivity), `e8_geometry.py` (tilt + resolution), `e10_ocr.py` (756 Tesseract + 63 EasyOCR configs), `e10b_trocr.py` (unrun), `e11_alt.py` (zero-shot YOLO, six segmenters, retrieval, 32-pipeline ensemble), `e12_vlm.py` (VLM-vs-pipeline, contains the VLM read as a literal block), `e13_figs.py`, `e14_problems.py`, `e15_temporal.py`, `out/*.json` (raw numeric outputs behind every table).

---

## 1. The 32-pipeline agreement ensemble (Approach 14, §3.8) [p.19–20]

Grid, run on plate7:
- **4 background radii: 12, 20, 35, 55 px**
- **× 4 background models: iterative (M5), Gaussian (M4), rolling-ball (M3), polynomial (M6)**
- **× 2 thresholds: 3σ, 4σ**
- = 32 pipelines; spot placements are **histogrammed** — "histogrammed where they placed spots" [p.19]. The output is an "agreement profile" per lane: at each position, the fraction of the 32 pipelines that report a spot there.
- Figure 11: bars coloured at the **80% and 40% agreement levels** [p.19].
- Results on plate7: maximum agreement anywhere = **0.78 (lane 3)**; lane maxima **0.78, 0.75, 0.66, 0.56 for lanes 3, 2, 1, 4** respectively. No feature reaches 80% on the cleanest plate [p.19].
- Runtime: full 32-pipeline sweep on one plate = **18 seconds** as written; **~4.5 seconds** if the background is cached rather than recomputed inside the lane loop [p.20].
- Fully deterministic given a fixed config list; no training data. Downside: someone must choose the agreement cut-off [p.20].
- Recommended usage (Part 8): pass the **agreement profile**, not a flat spot list, to any downstream interpretation layer [p.30, p.36].

Note: the ensemble radii (12/20/35/55) are NOT the same as the null-test radii (8/16/35/60) — see §4.

---

## 2. The eight densitometry methods (§3.1, Figure 9, table on p.16)

All deterministic variants share the same eight-stage skeleton and differ only in **stage 4** (I0 estimation + signal conversion) [p.14]. Stage list from Figure 8 [p.14]: (1) as photographed; (2) plate located and perspective-corrected, hand-supplied annotation bands marked; (3) inferred illumination surface; (4) optical density with detected lanes and origin row; (5) everything above 3× plate noise; (6) lane finding by column projection; (7) densitograms with fitted peaks.

| Method | Definition (verbatim in substance) | Result / spot counts | Verdict |
|---|---|---|---|
| **M1 · Naive grayscale** | Convert to grey, invert luminance, average down each lane. No correction. | plate7 lane 3: 1 peak; plate4 lane 2: 0. Lighting gradient swamps everything; luminance dilutes green with red/blue noise. | Reject |
| **M2 · Global reference** | Take the **90th percentile of the plate** as "blank" and subtract, linear difference. No spatial model. | plate7: 1 peak; plate1: 4. | Reject (only defensible with flat-field calibration frame) |
| **M3 · ImageJ rolling-ball (Sternberg) + OD** | Roll a ball of radius R under the inverted surface; what it cannot reach is background. The Fiji Analyze > Gels standard. | plate7: 2 peaks at rows **161.5 and 245.2**. First peak sits **3.3 px** from the M4–M7 cluster (ball erodes the leading edge of a broad band). | Use — sensible default baseline |
| **M4 · Gaussian illumination** | Blur heavily; call that the lighting. **Single pass** + OD. | plate7: 2 peaks (**165.0, 247.5**). Spots pull the estimate down under themselves → under-reports strong spots. | Partial — fine for detection, biased for quantitation |
| **M5 · Iterative masked illumination** | Blur to guess the lighting, mark pixels much darker than the guess as spots, replace them with the guess, **repeat ×3**, then OD. Converges to "what the plate would look like empty". The proposed pipeline. | plate7: 2 peaks (**164.8, 246.4**) — within 0.3 px of M4/M6/M7, 3.3 px from M3. Cost: masking lowers the noise estimate **1.68×** (see §3). | Use — best of the eight, **provided the noise unit is measured before masking** |
| **M6 · Polynomial surface** | **3rd-order 2-D polynomial fit** to the whole plate + OD. **10 coefficients** you can print in an SOP. | plate7: 2 peaks (**164.9, 247.1**). Fewest free parameters. | Use — strongest candidate for a validated method |
| **M7 · Median filter** | **Large median** filter + OD; morphological cousin of M4, edge-preserving. | plate7: 2 peaks (**165.1, 246.6**). plate4: 2, one of which (row 90) no other method found. | Partial — equivalent to M4/M5, slower |
| **M8 · Kubelka–Munk** | **KM = (1−R)²/2R** on the **iterative background**. Physically correct reflectance transform for a scattering layer; what commercial HPTLC densitometers use. | **9 peaks on plate7 lane 3** vs 2 for the other seven; **9 on plate4 lane 2** vs 0–2. KM diverges as R→1, amplifying noise on faint quenching zones. | Reject here — right physics, wrong regime |

Headline numbers [p.16]: on one lane of one plate the eight methods reported **1, 1, 2, 2, 2, 2, 2 and 9** spots; on plate1 they reported **1, 4, 4, 1, 1, 1, 1 and 1**. Strong-spot positions agree to about **1 px across M3–M7**; **M4–M7 cluster to within 0.3 px on the first peak and 1.1 px on the second; M3 sits 3.3 px outside**. [Also restated p.37: "six of the eight methods place the strong spots within a third of a pixel of one another, and a fifth within three and a half pixels".]

Recommended photometry for the build: **M6 polynomial or M5 iterative, OD**; "M5 agrees with it to 0.1 px and avoids self-bias" [p.36].

Peak acceptance convention throughout: **candidates at 3σ prominence (a shape test, not an amplitude test); confirmed at >5σ amplitude** [p.7, p.17]. Across all seven plates: 4–12 candidate peaks per plate at 3σ prominence, of which 0–7 exceed 5σ amplitude and are confirmed; plate2 confirmed nothing [p.17].

---

## 3. Sigma (noise-unit) measurement protocol (P5) [p.9]

- The noise is measured **on the residual after background subtraction** — hence it inherits the background model's arbitrariness ("the noise unit is circular").
- Measured on **plate7**: **σ ranges from 0.0032 at R=4 to 0.0116 at R=30 — a factor of 3.6**. So "3σ" is three different absolute thresholds depending on the radius.
- **Second circularity**: the iterative background model (M5) masks out pixels it has decided are spots, and the noise is then measured on what remains. Excluding the most variable pixels lowers the estimate. Measured: **σ with spot-masking = 0.01122; without = 0.01881. Masking makes every downstream threshold 1.68× more permissive than it appears** [p.9].
- Consequent design rule: **measure the noise unit BEFORE masking** [p.16, M5 verdict].
- Units: σ is in the OD residual domain (OD = log10(I0/I)); exact estimator (sd vs robust), and the exact pixel region (whole plate vs blank band vs per-lane) are **not stated** — see UNSTATED.
- Related sweep [p.9]: sweeping R from **4 px to 80 px** on one lane of plate7, the reported spot count goes **6 → 4 → 4 → 4 → 4 → 3 → 2 → 2 → 3 → 3 → 2** (11 sweep points). A feature at row **~105** is a spot for **R ≤ 16 px** and absorbed into background for **R ≥ 22 px**.

---

## 4. The two null tests (P5) [p.9–10]

Question answered: how many spots does the identical detector find on a plate with provably no spots?

**Null A (synthetic)** [p.9]: "Take the fitted illumination surface from plate7 and add noise resampled from a visually blank band of the real plate. **40 realisations.**"

**Null B (real texture)** [p.9]: "Take the actual residual field from a blank band of plate7 — preserving its true spatial correlation, JPEG texture and all — and **tile it mirror-wise over the illumination surface**. **13 independent phase shifts.** This one cannot be accused of getting the noise statistics wrong, because it is the plate's own noise."

Results table (mean false spots per blank plate) [p.10]:

| Threshold | R=8 px | R=16 px | R=35 px | R=60 px |
|---|---|---|---|---|
| **Null A** 2σ | 42.7 | 38.2 | 20.5 | 3.9 |
| **Null A** 3σ | 15.5 | 13.4 | 5.4 | 0.50 |
| **Null A** 5σ | 2.9 | 1.8 | 0.23 | 0.00 |
| **Null B** 2σ | 12.2 | 11.5 | 6.0 | 6.1 |
| **Null B** 3σ | 11.5 | 11.2 | 5.5 | 3.0 |
| **Null B** 5σ | 11.5 | 5.7 | 0.69 | 0.62 |
| **Null B** % of blank plates with ≥1 false spot at 3σ | 100% | 100% | 100% | 100% |

Headline operating point [p.10]: "At the conventional operating point (**R = 35 px, 3σ**) the pipeline reports **5.5 spots** on a plate that contains none, and **100% of blank plates** produce at least one. Even at 5σ … **69% of blank plates still yield a false spot at that radius**." (Note: 69% at 5σ corresponds to the Null B mean 0.69 at R=35 — the text renders the same figure both as a mean count and a percentage.)

Figure 6 example [p.10]: on one Null-B blank plate the standard 3σ detector reports **five spots — at rows 88, 127, 160, 199 and 232**.

Mitigations listed [p.10]: larger radius, stricter threshold, **minimum spot width**, requirement that a feature appear in more than one lane or in a repeat photograph, and above all reporting pipeline agreement (Approach 14).

---

## 5. Geometry: plate detection, rectification, tilt, frame overrun, lanes, origin, front

### Plate detection (P3) [p.7]
- Plate = "the largest bright, strongly-green region; a **hue-plus-value mask** followed by **largest-connected-component** finds it on all seven plates, covering **79.5–93.2% of the frame**."
- Hue alone fails (dark teal bench surface under UV falls in the same hue band); an **Otsu threshold on brightness** added to the hue mask separates them.

### Frame-overrun check (P3) [p.7]
- "We measure what fraction of the **three-pixel band along each image border** still falls inside the plate mask; if most of it does, the plate continues past the frame." On 4/7 plates that figure exceeds **0.77** on at least one edge.
- Per-edge fractions:

| Plate | top | bottom | left | right | Consequence |
|---|---|---|---|---|---|
| plate3 | 0.00 | 0.85 | 0.20 | 0.33 | origin end cropped |
| plate4 | 0.85 | 0.93 | 0.00 | 0.00 | both ends cropped |
| plate5 | 0.00 | 0.93 | 0.22 | 0.19 | origin end cropped |
| plate7 | 0.77 | 0.53 | 0.19 | 0.00 | front end cropped |

### Rectification and tilt (P4) [p.8]
- "Take the **four corners of the detected plate** and apply a **projective warp** so a vertical distance means the same thing everywhere." Tilt across plates: **0.4–4.1°**.
- Synthetic tilt study on plate7, rotations **0° to 12°** (rows shown 0/1/2°):

| Tilt | Plate height after warp | Confirmed peaks (rows) | Reported apparent Rf | Rf of same physical band |
|---|---|---|---|---|
| 0° | 299 | 164.8, 246.4 | 0.456 | 0.456 |
| 1° | 300 | 139.9, 166.0, 246.7 | 0.596 | 0.453 |
| 2° | 301 | 140.6, 166.7, 247.5 | 0.594 | 0.452 |

- The 0.14 Rf step is a threshold-crossing artefact of a "highest confirmed spot" reporting rule (a marginal peak at row ~140 crossed the 5σ bar); the physical band moved 0.456 → 0.453 → 0.452, **stable to 0.004**. Rule: report spots by matched position across a plate series, never by rank [p.8].

### Lanes (P6) [p.10]
- "Lanes are located by **projecting the OD field down each column and finding the strongest vertical bands**." Lane count was **supplied** (4), not discovered [p.17].
- Failure mode: an empty lane has no signal, so the grid slides toward loaded lanes. plate7 detected lane centres **x = 16, 53, 92, 130**; the near-empty "S" lane centre sits visibly left of the true spotting column.
- Lane collapse for the densitogram: **lane width = 55% of nominal lane pitch** [p.17]; mean-vs-sum choice acknowledged but the one used is not stated.

### Origin (P7) [p.11]
- Spotting dots found with **a small-scale blob detector**: 4/4 lanes on five plates, **3/4 on plate1, 1/4 on plate2**. **Origin = median dot row.** Rules: origin must never fall below the label row; **refuse rather than guess when fewer than two dots are found** (the "two-dot rule"; under it plate2 has no usable origin).

### Solvent front (P8) [p.11]
- "A front-line detector requiring a **thin, dark, full-width feature** was run on all seven plates. It returned nothing, on every plate" — correctly (no pencil line drawn; 4 plates cut off at the top).
- Rf convention sensitivity: one migrated spot per plate × **three defensible origin conventions × four defensible front conventions** (12 combos; 9 yield values because the pencil-line front is absent everywhere; on plate5 six yield values, three give negative Rf and are discarded; plate2 absent — no spot cleared threshold):

| Plate | Spot row | Lowest Rf | Highest Rf | Spread |
|---|---|---|---|---|
| plate1 | 99.0 | 0.039 | 0.840 | 0.80 |
| plate3 | 113.8 | 0.150 | 0.539 | 0.39 |
| plate4 | 108.0 | 0.251 | 0.709 | 0.46 |
| plate5 | 149.4 | 0.078 | 0.852 | 0.77 |
| plate6 | 90.9 | 0.420 | 0.937 | 0.52 |
| plate7 | 164.8 | 0.336 | 0.971 | 0.64 |

- Fix: report **Rst** (migration distance ÷ migration distance of the reference spot in the standard lane on the same plate) instead of Rf [p.12].

### Valid-mask handling
- "what to do where the lane mask is partly outside the plate" is acknowledged as a choice that changes the curve [p.17]; the actual handling is **not stated** (UNSTATED).

---

## 6. Peak modelling: EMG, streaks, deconvolution

- Spot shapes on these plates are not Gaussian: plate1 has T-shaped streaks running the full height of two lanes; plates 3, 4, 5 comet-tail from overloaded origin spots; plate7 has a solvent halo around the R-lane origin dot; plate2's spots so diffuse the pipeline confirmed **zero spots at 5σ** [p.12].
- "the chromatographic literature uses the **exponentially-modified Gaussian** for tailing, and even that will not fit a streak" [p.12]. `tlclib.py` contains "Gaussian and EMG peak fitting" [p.39]. Recommended architecture: "**EMG fit to raw corrected pixels, seeded by the ensemble**" [p.36]. **No EMG parameterisation, seeding rule, bounds, or goodness-of-fit criterion is given anywhere in the report** (UNSTATED).
- Streak detection: "the **ratio of the fitted width to the lane width**, or the **second moment of the lane profile**" — easy, and a streaking lane should be **flagged and not quantified** [p.12]. No numeric threshold stated (UNSTATED).
- Deconvolution warning (P10) [p.12]: sum-of-Gaussians error bars describe fit quality, not model correctness; two-peak and three-peak fits to the same merged band are often statistically indistinguishable; the number of components is a choice presented as a measurement.
- Graph edges from measured positions with an explicit tolerance, "e.g. **edges where |ΔRst| < 0.03**" [p.29].

---

## 7. Photometry: channel, I0, OD, clipping, Kubelka–Munk

- "Under 254 nm the F254 indicator emits green. The **green channel is therefore the only channel carrying the quenching signal**; red and blue carry noise." [p.5]
- **OD = log10(I0/I)**, where I0 is "what the plate would have read with nothing on it" [p.3, p.6]. The 2-D OD field is the primary archived record; the densitogram is a rendering [p.17].
- I0 is inferred (not measured) from the same pixels as the spots, with one free parameter: a **radius in pixels** [p.8].
- Illumination on plate7: "the fitted illumination surface runs from **0.753 to 0.891 in normalised green — a 15.5% swing** from the bright centre-right to the dark corners" [p.8; restated p.14].
- Clipping: "Red marks every pixel where the green channel reads **255**. In those pixels the true brightness could have been 255 or 400 or 4000 — the sensor saturated" [p.6]. Clipping is non-uniform (happens where the plate is brightest); if I0 is clipped the denominator of every ratio is wrong by an unknown amount. **No clipped-pixel exclusion/repair procedure is described** (UNSTATED) — the report's stance is that intensities/areas on clipped plates are uninterpretable; positions of strong spots survive.
- Kubelka–Munk: **KM = (1−R)²/2R** [p.12, p.16]; numerically hostile where R→1 (faint quenching zones); on these plates OD is the better-behaved choice.
- Concentration response of a fluorescence-quenching plate is non-linear and saturates; origin spots on plates 3, 4, 5 are visibly in that regime — their areas cannot be converted to amounts even in principle [p.13].
- Capture recommendation: lock exposure so the green channel peaks **~220–240, never 255** [p.36].

---

## 8. Per-plate statistics (generator-calibration data)

### Master capture table (P1) [p.5]

| Plate | ID | Photo size (px) | px per lane (rectified width / 4) | Green channel at 255 | Tonal range (p99−p01) | Tilt | Runs off frame? |
|---|---|---|---|---|---|---|---|
| plate1 | MEHQ-P29 | 71 × 130 | 17.5 | 59.6% | 135 | 2.2° | no |
| plate2 | MEHQ-P32-1 | 77 × 125 | 17.8 | 14.4% | 157 | 3.6° | no |
| plate3 | MEHQ-P32 | 93 × 166 | 22.0 | 35.2% | 116 | 4.1° | yes (85% of edge) |
| plate4 | MEHQ-P30 | 100 × 170 | 23.2 | 22.7% | 118 | 0.8° | yes (93%) |
| plate5 | MEHQ-P32 | 112 × 184 | 27.0 | 24.0% | 137 | 2.0° | yes (93%) |
| plate6 | MEHQ-P31 | 121 × 197 | 27.2 | 20.3% | 78 | 3.0° | no |
| plate7 | MEHQ-P33 | 158 × 299 | 38.0 | 0.0% | 110 | 0.4° | yes (77%) |

(Discrepancy: the resolution table [p.7] lists plate7 at scale 1.00 as **152×299**, vs 158×299 here.)

- Images are **0.009–0.047 megapixels**; lanes 17–38 px wide; handwriting 3–16 px tall [p.6]. Files are ~250× smaller than the camera's native output on the largest plate, ~1,300× on the smallest (inferred, originals unseen) [p.37].
- Plate-mask frame coverage: **79.5–93.2%** of the frame [p.7].
- Practical floor drawn in Figure 3: **20 px per lane** for lane photometry (plates 1–2 below, plate3 marginal); origin dots stop being resolvable **below about 12 px per lane** [p.6–7].
- Illumination swing: plate7 = 0.753–0.891 normalised green (15.5%) — the only plate for which the swing is quantified (UNSTATED for plates 1–6).
- Empty-band residual noise sd (plate7, OD units): **0.01122 (spot-masked) / 0.01881 (unmasked)**; radius-dependent σ **0.0032 (R=4 px) → 0.0116 (R=30 px)** [p.9].
- Time-course subset (P32 series) [p.31]: clipping **14%, 35% and 24%**; sizes 77×125, 93×166, 112×184 px; tilts 3.6°, 4.1°, 2.0° (plate2 @3hr, plate3 @4hr, plate5 @4+3hr). Confirmed spots across all lanes: **4σ: 0, 9, 3; 3σ: 0, 11, 5; 5σ: 0, 7, 2**. (Part 6 table [p.33] says "0, 6 and 2" — internal inconsistency.)
- Handwriting glyph geometry [p.21]: connected components of ink **above 4σ within the header band**; median component height per plate = **3, 4, 5, 6, 6, 9 and 16 px** for plates 1–7 (90th percentile **4–21 px**). Only plate7 has strokes above 10 px.
- Handwriting vs chemistry OD (plate7, all pixels above 3σ) [p.12]: **header band p10–p90 = 0.053–0.162, max 0.382; chemistry band p10–p90 = 0.038–0.092, max 0.352**. Distributions overlap almost entirely; ink reaches higher OD than chemistry.
- Ridge-based stroke mask fired on **47–71% of all plate pixels** (pen strokes are 1–2 px wide at this resolution — the noise scale) [p.12].
- Iterative background at R=35 px absorbs the handwriting into "background" over three iterations, hiding text and depressing the background under it [p.12].
- Annotation bands were **supplied by hand as fractions of plate height** (values in `common.py`, not printed) [p.12].
- Approach-1 output stats [p.17]: lane centres 7/7 (count supplied); ≥2 origin dots 6/7; solvent front 0/7; 4–12 candidates at 3σ prominence per plate; 0–7 confirmed at >5σ amplitude.
- Retrieval signatures [p.20]: each plate reduced to a **4×48 vector** — four lane densitograms resampled onto a common origin-anchored migration axis and normalised — compared by **correlation distance**. Neighbour table: plate3↔plate6 d=0.206 (tightest); plate6→plate1 0.478; plate4→plate3 0.322; plate4→plate7 0.389; plate1→plate5 0.361; plate1→plate3 0.445; plate7→plate4 0.389; plate7→plate6 0.600; plate2→plate3 0.459; plate2→plate6 0.501.

---

## 9. VLM / OCR evaluation

### VLM band placement (Approach 3, §5.1) [p.27–28]
- Model: **Claude Fable 5** (frontier VLM), shown **8× Lanczos upscales** of plate6 and plate7, asked to place bands **before** the pipeline numbers for those plates were computed (provenance caveat: the read is a literal block in `e12_vlm.py`, not a timestamped API log).
- Scored against the deterministic pipeline on the same origin-anchored scale:

| Plate | VLM apparent Rf | Pipeline apparent Rf | Δ |
|---|---|---|---|
| plate6 (mean |Δ| = 0.012) | 0.121 | 0.136 | 0.015 |
| | 0.407 | 0.425 | 0.018 |
| | 0.516 | 0.529 | 0.013 |
| | 0.661 | 0.662 | 0.001 |
| plate7 (mean |Δ| = 0.015 over 3 matched; 0.050 if unmatched band scored to nearest cluster) | 0.104 | 0.086 | 0.018 |
| | 0.337 | 0.334 | 0.003 |
| | 0.460 | 0.436 | 0.024 |
| | 0.589 | — | no pixel support: strongest signal at that row is **1.8σ** |

- Overall agreement range: **0.001–0.024** apparent Rf on matched bands — inside the ±0.02–0.05 manual inter-analyst Rf reproducibility [p.3, p.28].
- **Invented band**: "very faint" band at Rf 0.589 on plate7; pixels peak at **1.8σ** in the strongest lane. Nuance: small-radius background models did report a peak near that row (radius sweep finds it at **R ≤ 16 px**, and M8 finds it) — the 1.8σ figure was computed for the report and sits below the pipeline's recording threshold [p.28].
- **Omitted feature**: origin residue at apparent Rf **0.006**, present in all four lanes; VLM classified it as "the origin" rather than a band [p.28].
- VLM also correctly reported `front_line_visible: false` on both plates, correct lane count, correct lane labels [p.28].
- Worked lane/spot graph (plate7, §5.3) [p.29]: nodes {S:[o, b0.10, b0.46], CO:[o, b0.10, b0.34, b0.46, b0.59?], R:[o, b0.10, b0.34, b0.46, b0.59?], Sd:[o, b0.34]}; edges CO.b0.34—R.b0.34—Sd.b0.34 and S.b0.46—CO.b0.46—R.b0.46.

### OCR benchmark (Track B, §4.2) [p.21–22]
- **Tesseract 5.3.4 (LSTM): 756 runs** = 108 configurations per plate (**3 regions × 3 scales × 3 preprocessings × 4 page-segmentation modes**) × 7 plates.
- **EasyOCR 1.7 (CRAFT+CRNN): 63 runs** (9 per plate). Total 819 runs.
- Reference transcription = frontier VLM reading of 8× Lanczos upscales (caveat: CER measures disagreement with the VLM, not truth).
- Results: Tesseract — 71 empty outputs (9%), mean CER all runs **0.81**, header best **0.59** (avg 0.69), lane labels best **0.11** (avg 0.44), sample codes read correctly **0 of 7**. EasyOCR — 6 empty (10%), mean CER **0.74**, header best 0.50 (avg 0.68), lane labels best 0.44 (avg 0.59), **0 of 7**. Claude Fable 5: 7 runs, 0 empty, 7 of 7 sample codes (self-reported; is also the reference).
- EasyOCR confidence: 203 words emitted, **median 0.16, 81% below 0.5**; 11 words scored ≥0.90 and **five scored exactly 1.00 and were still wrong** ('5 R M (' at conf 1.00) [p.22, p.26].
- 24 of 819 runs contained the plate's numeric code somewhere in output: P32 on plate3 (20 runs), P30 on plate4 (3), P32-1 on plate2 (1), EasyOCR's p33 on plate7 (1) — none recovered the MEHQ prefix [p.22].
- Glyph floor context: measured glyph heights 3–16 px (median 6) vs vendor minima — AWS Textract 15 px, Azure 12 px, Tesseract 10 px x-height [p.21]. TextZoom (ECCV 2020): ASTER word accuracy 81.2% @32 px → 35.7% @16 px; bicubic/Lanczos upscaling ceiling 21–36% word accuracy [p.21].
- socOCRbench handwriting scores [p.25]: Gemini 3 Pro 0.680, Gemini 3.1 Pro 0.649, Qwen3.5-397B 0.614, Claude Sonnet 4.6 0.536, Qwen3-VL-8B 0.480, Qwen3.5-2B 0.431, LightOnOCR-2 0.206, PP-OCRv5 0.193, Tesseract v5 0.135.
- Hallucination [p.25–26]: GPT-5.2 fabricated readings on blurred labels 96% of the time (admitted inability 8%); Gemini 3.1 Pro admitted 71%; open models 5–19%. KIE-HVQA hallucination-free accuracy: Gemini 2.5 Pro 35.0%, GPT-4o 30.2%, Claude 3.5/3.7 24.3–26.6%, Qwen2.5-VL-7B 21.5%. "Respond UNREADABLE if unsure" moved correct-refusal 1.8% → 12.4%; abstention fine-tuning 26–34%. Multi-model agreement predicts OCR correctness at 48.0–51.3 F1 vs 36.1–40.0 for VLM-as-judge (+42% relative); five-model ensembles beat best individual in 91.1% of cases.
- Closed-vocabulary counter-result: Gemini 3.1 Flash-Lite 98.38% accuracy, 0.58% false-negative on single-character closed-vocabulary handwriting vs YOLOv5 90.9% — the lane labels (S/co/R/sd) are that problem shape [p.25].
- Recommended text architecture: frontier VLM, constrained JSON schema with UNREADABLE in every enum, 3–5× self-consistency sampling, human review of disagreement [p.26, p.36].

---

## 10. Resolution and tilt studies (P2, P4) [p.6–8]

### Downsampling study — plate7, identical pipeline at each scale [p.7]

| Scale | Rectified plate (px) | px/lane | Candidates (3σ prominence) | Confirmed (>5σ amplitude) | Origin dots found | Apparent Rf of confirmed spots |
|---|---|---|---|---|---|---|
| 1.00 | 152×299 | 38.0 | 10 | 7 | 4/4 | 0.010 0.010 0.029 0.116 0.446 0.455 0.456 |
| 0.75 | 113×224 | 28.2 | 10 | 6 | 4/4 | 0.012 0.012 0.037 0.118 0.448 0.457 |
| 0.50 | 76×150 | 19.0 | 9 | 6 | 4/4 | 0.013 0.048 0.117 0.450 0.458 0.459 |
| 0.35 | 55×104 | 13.8 | 9 | 6 | 4/4 | 0.006 0.064 0.115 0.446 0.451 0.452 |
| 0.25 | 40×75 | 10.0 | 8 | 6 | 2/4 | 0.003 0.071 0.117 0.447 0.448 0.453 |

- Same band sits at **Rf 0.446–0.459 across a 15-fold pixel reduction**; sensitivity degrades (peaks 10→8); origin dots fail below ~12 px/lane. The other six plates start between plate7's 0.5× and 0.75× rungs (17.5–27.2 px/lane) [p.7].
- Tilt study: see §5 above (0/1/2° table; rotations run to 12°; band stable to 0.004 Rf).

---

## 11. Other quantitative material

### Segmentation stand-ins (Approach 6) [p.17–18]
- Object counts, same plate, six segmenters: **Otsu 27, 3σ threshold 17, LoG blobs 29, SLIC 4, watershed 11, random forest 17** — 7× spread in object count, **8× in area fraction** [p.17–18].
- Random forest: trained on **six hand-drawn regions — four 5×5 patches on the origin dots and two blank bands**; model is **200 trees over 13 named filter responses**; outputs a probability map [p.17].
- Published zero-shot Dice for foundation segmenters on low-contrast soft-edged objects: 0.4–0.7. micro_sam: most benefit at 2–5% training fractions (10–50 plates to fine-tune). GelGenie: 420 training images → Dice 0.82; neural half gave no quantification advantage over classical densitometry. Mask R-CNN on 16 gels: 76.6% band localisation. Estimated 100–300 annotated plates for a TLC detector [p.18].

### Zero-shot YOLO (Approach 7) [p.18]
- COCO-pretrained YOLO11n, confidence floor 0.01 (25× below default). Complete output: plate1 tv (0.379); plate2 tv (0.345); plate3 person (0.094), toothbrush (0.020), bird (0.011); plate4 person (0.090); plate5 person (0.144); plate6 person (0.131); plate7 person (0.060).

### JPEG artefacts
- Null B deliberately preserves "JPEG texture" in the residual field [p.9]; generative restoration "will cheerfully sharpen our JPEG ringing into crisp, convincing, non-existent spots" [p.19]. No quantitative JPEG-quality figure given.

### Accuracy targets proposed [p.3]
- Spot detection sensitivity ≥95% (chemist-visible), false-positive rate <0.2 spots/plate, position reproducibility ±0.02 in Rst units, explicit "insufficient image quality" verdict. Manual inter-analyst Rf: ±0.02–0.05. TLCyzer benchmark: repeatability 2.79% RSD, intermediate precision 4.46% RSD, recovery 96.8–103.9%.

### Flags / refusal checklist (Approach 15 → fixed rules) [p.31, p.36]
- Clipped / plate cut off / no front (switch to Rst) / streaking / overloaded (saturated origin + solvent halo → suppress area reporting for that lane) / <2 origin dots (refuse origin) / lane empty. Plus: anchor Rst to the standard-lane band; ask "was a front line drawn?".

### Costs (selected) [p.24–25, p.35]
- Anthropic image tokenisation: 28×28-px blocks → 1,296 tokens for a 1000×1000 crop; OpenAI 32×32 patches × 1.2 → 1,229; Google flat 1,120 (280 at low res). VLM cost per 1,000 plates: $0.11 (gemini flash-lite low) to $11.23 (claude-opus-5); recommended range $1–$4.50/1,000. 32-pipeline ensemble ~$0 compute. Self-hosting break-even ~400,000 images/month (~185× the 26k/yr volume).

---

## UNSTATED — parameters the port needs that the report never gives numerically

1. **M4/M5 Gaussian blur sigma / kernel size** for the illumination estimate (only "blur heavily"; the sweep radius R is the free parameter, but the blur↔R mapping is not defined).
2. **M5 masking threshold** — how much "much darker than the guess" is, in σ or OD, for marking spot pixels during iteration.
3. **M7 median-filter kernel size** ("large median").
4. **Default/baseline background radius per method** outside the sweeps (the "conventional operating point" R=35 px is stated only in the null-test context).
5. **Noise estimator** — sd vs MAD/robust; and the exact **pixel region** σ is computed on (whole-plate residual vs blank band vs per-lane), and per-plate vs global.
6. **Peak detection specifics** — prominence definition/implementation (presumably scipy find_peaks), minimum spot width, minimum peak separation, amplitude measurement window.
7. **Origin blob detector parameters** — scale(s), threshold, method (LoG?).
8. **Hue band limits** for the plate mask and which value/brightness channel Otsu is applied to.
9. **Corner extraction method** from the plate mask (contour approximation? min-area quadrilateral?) and the rectified target dimensions/scale convention.
10. **Densitogram collapse operator** — mean vs sum across the lane width (acknowledged as a choice; the one used is not stated).
11. **Lane-mask valid-pixel handling** where the lane is partly outside the plate.
12. **EMG parameterisation** (μ, σ, τ form), seeding from the ensemble, parameter bounds, optimiser, and goodness-of-fit acceptance criteria.
13. **Streak-flag numeric thresholds** (fitted-width/lane-width ratio cut; second-moment cut).
14. **Annotation-band fractions** per plate (hand-supplied in `common.py`, values not printed).
15. **Null A resampling scheme** — iid per-pixel resampling vs block bootstrap from the blank band; blank-band location/extent on plate7.
16. **Null B mechanics** — mirror-tiling layout and the definition of the 13 "independent phase shifts".
17. **Agreement-profile binning** — histogram bin width (rows or Rf units) and the position-matching tolerance for "the same spot" across pipelines.
18. **The three origin conventions and four front conventions** used in the Rf-sensitivity study (named as "defensible" but never enumerated).
19. **Downsampling interpolation kernel** used in the resolution study.
20. **Green-channel normalisation** convention for I0/illumination ("normalised green" — presumably /255, not stated) and the channel used for the tonal-range p99−p01 metric.
21. **M2's percentile channel/region** (90th percentile of which channel over which pixels).
22. **Polynomial-fit details for M6** — fit domain (masked? whole plate including handwriting?), loss (plain least squares vs robust), whether iterated.
23. **Clipped-pixel handling in photometry** — whether 255-valued pixels are excluded from background fits and lane averages, or carried through.
24. **Illumination swing values for plates 1–6** (only plate7's 0.753–0.891 is given).
25. **Spot width ranges (px or Rf) and per-spot OD amplitudes** — never tabulated; only peak rows, counts and the header/chemistry OD percentile ranges exist as amplitude proxies.
26. **Tilt-measurement method** — how the tilt angle itself is computed from the detected plate (edge orientation? corner geometry?).
27. **Label-row location method** — used as a constraint on the origin, but its own detection is never described (annotation bands are hand-supplied).
28. **Retrieval axis range** — the origin-anchored migration axis limits for the 4×48 resampling, and the normalisation applied.
29. **JPEG quality / compression parameters** of the source images.
30. **The 4σ threshold's role** — the ensemble uses 3σ/4σ and glyph components use "above 4σ", but 4σ is never defined relative to the pre- or post-masking σ.

---

## Internal inconsistencies / refinements to note

- plate7 dimensions: **158×299** in the P1 capture table [p.5] vs **152×299** at scale 1.00 in the resolution table [p.7].
- Temporal series confirmed-spot counts: §5.6 gives **0, 9, 3 at 4σ (0, 11, 5 at 3σ; 0, 7, 2 at 5σ)** [p.31]; the Part 6 matrix says "0, 6 and 2" [p.33].
- The "69% of blank plates yield a false spot at 5σ, R=35" statement [p.10] re-reads the Null B mean count 0.69 as a percentage.
- `tlclib.py` is described as containing **seven** background models [p.39] though **eight methods** are evaluated (M1 naive-grayscale plausibly has no background model).
- The ensemble grid radii (12/20/35/55) differ from the null-test radii (8/16/35/60); only R=35 is common.
- The report's sample prefix is **MEHQ** (read at 8× magnification), correcting the commissioning brief's **MCHQ** [p.22].
