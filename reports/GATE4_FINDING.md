# Gate 4 finding: blank-plate FP ≤ 0.2 and recall ≥ 0.95 at ≥5σ cannot both be met at one operating point

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
