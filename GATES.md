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
