# Gate 4 finding — final (attempt 3): the detector sits on the gate boundary

**Attempt 3 result (eval split, 100 blanks incl. 40 real-texture from a pre-registered clean
region of MEHQ-P44; 315 observable ≥5σ spots):** at agreement ≥ 0.50, FP = 0.22/blank and
recall = 0.952; at ≥ 0.55, FP = 0.19 and recall = 0.949. Sampling SE ±0.045 / ±0.012. The tuning-selected
point is a ≥ 0.50 (eval 0.22 / 0.952; pooled 0.19 / 0.943); a ≥ 0.55 was fixed after inspecting
the eval curve and its figures are in-sample. Recall is the genuine boundary (tuning 0.930,
pooled 0.940). **Verdict: boundary, not lowered.** The final review also found the remaining
textured phantoms were a mirror-seam artefact of a screen blind at its band edge (M-012) — the
FP arm can only improve when that is removed; evidence is being regenerated at HEAD. The
shipped operating point is config/ensemble/OPERATING_POINT_v2.json (two-tier: reported ≥ 0.55,
candidates ≥ 0.40 — the human's D-013 choice on the corrected scale). What would settle it:
(i) real solvent-only blank plates (the real phantom rate is still NOT MEASURED), (ii) Phase 7
calibration, which replaces raw agreement by a fitted p_spot that uses z_med and the MC
p-value as well (spec 01 §2.4) — the resolution raw agreement lacks at the top (§2.5).

# Attempt 2 write-up — DIAGNOSIS REFUTED BY INDEPENDENT REVIEW (kept for the record)

> **Status update (2026-08-26, after adversarial review):** the numbers below are correct, but the
> diagnosis in §Diagnosis 2-3 is wrong. Mapping every real-texture phantom back to tile
> coordinates showed 100% (at a ≥ 0.5) sit in two 10-row bands that are **P33's own spot halos**
> leaking into the gutter strips (rectified rows ~168 and ~226) — not ambiguous texture (M-010).
> The §10 forced stop was therefore premature: a third attempt with a pre-registered signal-free
> screen on the null, the corrected agreement scale (D-015), the BH/floor fix, and n ≥ 300
> observable spots is spec-consistent and weakens nothing. The human-chosen interim operating
> point (D-013) stands as interim until attempt 3 reports. The claim "0.3–1.2 FP on real
> texture" is WITHDRAWN; "≤ 0.1 on synthetic noise; real-plate phantom rate not measured" stands.
> The observability filter excluded 0 spots (inert here); the residual gap is the recall arm.

# (original attempt-2 write-up follows, kept for the record)

**Status:** §10 forced-stop condition invoked after two genuine attempts. This is a finding, not a
failure to try; the gate is NOT lowered. A human chooses the operating point (recommendation below).

## What was measured (attempt 2, `reports/gate4_evidence.json`, eval split: 100 blanks = 60
synthetic-noise + 40 real-texture, 50 spotted plates, 128 observable true spots ≥5σ)

| operating point (a ≥ a*, z_med ≥ z*, p ≤ 1/61) | FP/blank pooled | synthetic-noise | real-texture | recall ≥5σ |
|---|---|---|---|---|
| a ≥ 0.3 | 2.95 | 2.03 | 4.33 | 0.977 |
| a ≥ 0.4 | 1.22 | 0.40 | 2.45 | 0.961 |
| a ≥ 0.4, z ≥ 6 | 1.14 | 0.27 | 2.45 | 0.930 |
| **a ≥ 0.5** | **0.54** | **0.08** | **1.23** | **0.938** |
| a ≥ 0.6 | 0.31 | 0.05 | 0.70 | 0.883 |
| a ≥ 0.7 | 0.16 | 0.05 | 0.33 | 0.742 |
| a ≥ 0.8 | 0.00 | 0.00 | 0.00 | 0.000 (Jeffreys cap ≈ 0.75) |

Curve: `reports/gate4_tradeoff_curve.png`. Operating point chosen on the tuning split (150
disjoint plates); the table is the evaluation split.

## Diagnosis

1. **On noise, the detector meets both arms almost exactly:** synthetic-noise blanks give 0.08
   FP/plate at a ≥ 0.5 with recall 0.938; at a ≥ 0.4 recall is 0.961 with 0.40 FP. The
   remaining gap on noise is a few percent of recall on 5σ spots in narrow lanes.
2. **The pooled FP arm is decided by the real-texture blanks.** Their phantoms have z_med 10–30
   and majority agreement across the 24-config grid — coherent structure in P33's gutter residual
   that every defensible pipeline finds. Attempt 1's tile carried lane-ghost structure (M-009,
   fixed); attempt 2's gutter-strip tile still carries row-coherent and sub-lane structure. A
   z_med threshold cannot separate it (it is strong), and higher agreement thresholds pay in
   recall 1:1.
3. Whether that structure is "noise texture" (a fair null) or "real features" (lane bleed,
   sensor/compression rows, halo) is not decidable from pixels — it is the F7/F9 class of
   ambiguity. Only human adjudication on real plates decides it, and that is the Phase 6
   labelled set (spec 01 §3.6: "20 solvent-only plates is cheap and directly measures the
   phantom rate").

## Recommendation for the human decision

- **Interim two-tier operating point (spec 01 §2.6):** `spots_reported` at **a ≥ 0.6**
  (pooled FP 0.31; 0.05 on noise; recall 0.88) and `spots_candidate` at **a ≥ 0.4** (recall
  0.96; rendered greyed, "possible, not confirmed"). Every emitted spot carries `a`, `z_med`,
  `p_med` and `n_hit/n_total` so the review screen can show the evidence, and confidence stays
  `refused` (E_UNCALIBRATED) until Gate 7.
- **What resolves the gate:** 20 real solvent-only blank plates photographed under the
  CAPTURE_PROTOCOL (the cheapest experiment in this build) replace the P33-derived texture null
  with a real one and settle whether the real-texture phantoms are phantoms. Until then, the
  FP claim that can be made honestly is: "≤ 0.1 false spots per plate on synthetic noise;
  0.3–1.2 on real texture depending on threshold; real-plate phantom rate NOT MEASURED."
- Any of these becomes the shipped point only by a `decisions.md` entry recording what
  accuracy is traded (brief §9 item 14).
