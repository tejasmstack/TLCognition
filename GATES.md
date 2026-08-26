# Gate reviews

Per spec 05 §12.9: each phase gate is checked by an agent that did not write the code, working
only from the committed evidence. Pass/fail with specific numbers, appended here. A gate that
"passes" without committed numeric evidence has not passed.

---
## Gate 0 · Audit and orientation
**Reviewed:** 2026-08-26, independent agent, at commit db77eb1.
**Initial verdict: FAIL** on item 4 (CI red: ruff I001 in `scripts/dataset_audit.py`, committed
without re-running CI — see mistakes.md M-001; the auto-fix then introduced M-002, a silent
reorder of the determinism import below numpy, now guarded by
`tests/test_determinism_import_order.py`).
**Items 1–3, 5–6 PASSED with evidence:**
- audit.json: 66/66 PNGs covered, all five required fields non-null, 3 records spot-checked
  against pixels (dims + sha256 match). Verdicts: 35 photometry_ok / 15 partial / 16
  positions_only (per-file, duplicates included).
- INDEX.md: 7/7 PDFs.
- decisions.md D-005: explicit MIXED keep/port/discard with file:line reasons.
- Determinism: audit.json sha256 byte-identical across two re-runs
  (27eb4fd7… before, after run 1, after run 2).
- Reviewer caveat: evidence files cited by D-005 (`reference/*.json`, `GATES.md`) were untracked
  at review time — remediated by committing them with the fix.
**Remediation commit:** (see git log — lint fix, import-order guard test, reference files
tracked). Re-check of item 4 by the same reviewer follows below.
**Re-check at 44bf51d: PASS (final).** CI exit 0 (ruff clean, 7 passed); all four evidence files
tracked; committed import order determinism-before-numpy (line 17 vs 21+) behind `# isort: split`
— reviewer verified ruff --fix reorders the OLD version but not the committed one, and that the
guard test fails on a deliberately violated copy. Gate 0 closed.

---
## Gate 1 · Synthetic plate generator
**Reviewed:** 2026-08-26, independent agent, at commit 2c17e5b. **Verdict: PASS.**
- CI exit 0 (20 tests). gate1_check.py deterministic: evidence json byte-identical across two
  runs and vs committed (sha256 efd05ddd…).
- Strict criteria: swing knobs 0.11–0.25 measure 0.09945–0.22901, all inside the observed real
  range [0.09469, 0.24448]; noise default-knob median 0.00728 OD = 0.943× real median 0.00772
  (±20% required); clip requested 0.14/0.25/0.40/0.60 → emitted 0.1233/0.2189/0.3648/0.5562
  (max |diff| 0.0438 ≤ 0.05).
- Calibration targets verified against reports/corpus_stats.json (61 images, 35 low-clip).
- Spec 05 §12.2 conformance: signature and every GroundTruth field present; amplitude range
  1–30σ spans below the detection floor (trap avoided).
- Adversarial: amplitude truth verified honest to ~1% by a differential same-seed measurement
  (reviewer's own first annulus measurement was off 0.62× — illumination hotspot, not a
  generator error); corner truth verified bright-inside/dark-outside.
- Side-by-side judged fit for purpose; six flags recorded outside gate coverage:
  (1) noise target is estimator-relative (A-007); (2) random_spec swing floor vs sweep floor —
  fixed; (3) EMG centre = mu convention — adopted build-wide (D-006); (4) no spot hue signal in
  synthetics; (5) background contrast unrealistically dark — fixed to measured teal (D-006);
  (6) clip knob delivers ~0.02–0.04 low on emitted images (effective 0.12–0.56).

---
## Gate 2 · Deterministic geometry (+ frozen result schema)
**Reviewed:** 2026-08-26, independent agent, at commit f009a61. **Verdict: PASS, two conditions
recorded.**
- CI exit 0 (72 tests). gate2_check.py byte-identical across two fresh-process runs and vs
  committed (sha256 71116c5e…).
- Corner sweep: p95 0.2078 px (bound 1.5), max 0.7725 px, 60/60 detected, tilt ≤0.48°.
  Reviewer's independent 8-plate probe: p95 0.2446 px — consistent.
- Real corpus 61/61 detected; all 4 known overrun cases flagged (0.26–0.31 vs 0.02).
- Idempotency 0.0 px worst — **by construction via the 2 px frame snap** (see A-010 amendment);
  raw re-detection repeatability ~0.8–1.5 px; 20/61 plates have no checkable corners (honest).
- Adversarial probes: black image → typed refusal; uniform green → wrong-but-typed full-frame
  plate with overrun flags (Phase 4 input gate carries the screen); rot90 → correct.
- Schema: emit byte-stable; Q validators, closed units, frozen/extra-forbid, rst-anchor rule all
  verified adversarially.
**Conditions:** (1) cross-machine determinism is an OPEN ITEM until CI runs on a second
architecture (A-009); (2) A-010 amended to attribute the idempotency 0.0 to the snap mechanism.
**Flags outside the gate:** detection is not discriminative on uniform-green frames (Phase 4
gate's burden); overrun false-positive sweep not required but cheap (deferred); dead code in
gate2_check.py:170 (removed in the follow-up commit).

---
## Gate 3 · Photometry and the noise unit
**Reviewed:** 2026-08-26, independent agent, at commit 61ab44b (review interrupted once by an
org spend limit and resumed; all items completed). **Verdict: PASS.**
- CI exit 0 (79 tests). gate3_check.py byte-identical across two runs and vs committed.
- Exposure invariance: worst drift 0.00102 ≤ 0.005 (margin 4.9×); reviewer's own plate at
  k=0.75 drifted 0.00479 — still passing.
- Monotonicity: Spearman ρ 0.99554 > 0.98 (n=30); reviewer verified nothing else lies inside
  the measurement window (no leakage).
- σ stability: worst spread 9.7% ≤ 15% (worst = real P33); reviewer's iterative@12 vs @55:
  1.92%. Consumption trace: sigma_od_prespot is the ONLY noise unit; residual-based σ appears
  solely as stability evidence. Kubelka–Munk exists only as prohibitions. Clip mask computed on
  raw source pixels pre-warp; clipped pixels inform no fit (verified at clip=0.5: fit weight
  9092 vs 20903, od_valid ∩ clipped = 0).
**Flags recorded for later phases:**
1. `strongest_peak_row` (explicitly temporary) has metamorphic edge cases: a 1-px argmax flip
   under re-quantisation (drift 0.0072 on one probe plate) and origin-dot leakage for faint
   spots near the band edge. The exposure-invariance property MUST be re-verified at Gate 4/5
   with the real matched-filter/EMG detector.
2. The D-008 unit reads ~0.41× the per-pixel σ under corpus-texture correlation — internally
   consistent, but it drifts if noise correlation varies across captures; spec 01 §2.1's
   autocovariance machinery (Phase 4) is the structural fix.
3. Plate detection has a silent domain hole at base_green ≈ 0.60 (below the corpus 0.78–0.95):
   the Otsu class-ratio guard (1.35) sits in its gap and the whole scene passes as the plate.
   Phase 4's input gate (VIF / NOISE_STRUCTURED, capture QC) carries the screen; noted as a
   known limitation for darker future captures.
4. gate3_check.py's real-P33 analysable band (0.25h–0.80h) is a chosen convention → A-014.

---
## Gate 4 · Ensemble detection and the null battery — NOT MET; §10 forced stop invoked
**Attempts:** two genuine (attempt 1 failed on a contaminated real-texture null — M-009;
attempt 2 with the honest null, row-coherent S1, re-selected grid, spec-anchored matching).
**Evidence:** reports/gate4_evidence.json, reports/gate4_tradeoff_curve.png,
reports/GATE4_FINDING.md (eval split: 100 blanks, 128 observable true spots ≥5σ).
- No operating point over (agreement, z_med, p) satisfies FP ≤ 0.2/blank AND recall ≥ 0.95.
- Synthetic-noise blanks: 0.08 FP/plate at a ≥ 0.5 with recall 0.938 — both arms nearly met
  on noise. Real-texture blanks: 1.23 FP/plate at the same point (phantoms with z_med 10–30,
  majority agreement) — coherent structure in P33's gutter residual, decidable only by human
  adjudication on real plates (Phase 6).
- Grid health G10: K_eff on null bins 9.67 (≥ 4). Per-commit sentinel test added (A-016).
**Disposition:** written up as a finding per brief §6 Phase 4 and §10; the gate is NOT lowered.
Recommended interim two-tier operating point (spec 01 §2.6): reported at a ≥ 0.6 (pooled FP
0.31, noise 0.05, recall 0.88), candidates at a ≥ 0.4 (recall 0.96). **Human decision (2026-08-26): option 1 chosen** —
frozen as config/ensemble/OPERATING_POINT_v1.json; build proceeds to Phase 5 under it, with
confidence refused (E_UNCALIBRATED) and the honest FP claim recorded in the artifact.
Independent adversarial review of the finding's numbers requested.

---
