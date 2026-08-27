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
**Adversarial review (2026-08-26): numbers reproduced exactly; diagnosis REFUTED** — the
real-texture phantoms are P33 spot halos in the gutter tile (100% of a ≥ 0.5 phantoms in two
10-row bands), so the null violated spec 05 §12.3's "blank band" premise (M-010). Also found: the
agreement scale was compressed to [0.25, 0.75] (D-015), and a 20σ spot was rejected by BH
because the MC p-floor exceeded q/m on a clipped lane (fixed: adaptive surrogate count).
**Attempt 3 is a legitimate third attempt (no contract weakening): screened null, corrected
scale, BH/floor fix, 250 spotted plates.** Gate 4 remains OPEN pending attempt 3.

---
**Attempt 3 (2026-08-26, honest null + D-015 scale + BH/floor fix + 250 spotted plates):**
evidence reports/gate4_evidence.json, curve reports/gate4_tradeoff_curve.png.
- Eval split (100 blanks: 60 synthetic-noise + 40 rule-selected real texture from MEHQ-P44
  rows 56–149; 315 observable ≥5σ spots): a ≥ 0.50 → FP 0.22 (synth 0.17 / textured 0.30),
  recall 0.952; a ≥ 0.55 → FP 0.19, recall 0.949. The arms cross between the two. Pooled over
  all 200 blanks / 631 spots at a ≥ 0.5: FP 0.19, recall 0.943.
- Tuning split: no point meets both arms strictly; one eval point (a ≥ 0.55, p ≤ 4/61) meets
  both but was not selected on tuning — not claimed.
- Sampling SE at these n: ±0.045 (FP), ±0.012 (recall).
**Verdict: BOUNDARY.** The tuning-selected point is a ≥ 0.50 (eval: FP 0.22, recall 0.952;
pooled 200 blanks/631 spots: FP 0.19, recall 0.943). The shipped reported tier a ≥ 0.55 was
fixed AFTER inspecting the eval curve (it restates the human's D-013 two-tier choice on the
D-015 scale), so its eval figures (FP 0.19, recall 0.949) are in-sample and optimistic; the
recall arm is the genuine boundary (tuning 0.930, pooled 0.940 — ~1 SE below 0.95). Three
genuine attempts, none weakening the contract. Final independent review (2026-08-26): numbers
reproduce exactly; the null screen was found blind at its band edge with 16/18 remaining textured
phantoms on a mirror seam (M-012) and the committed evidence predated M-011 — the screen is
fixed, the cache is now code-fingerprinted, and the evidence is being regenerated at HEAD (the
seam artefact inflated FP, so its removal can only help the FP arm). "Real texture under an
honest null" is WITHDRAWN as a description of the textured FP until the regenerated evidence
shows a flat phantom tile-row histogram.

---
## Gate 5 · Peak modelling, Rst, refusal
**Self-check:** 2026-08-26, reports/gate5_evidence.json (60 synthetic plates, seeds 11000-11059;
61 unique real images). Independent review requested.
- Position (D-014 mode convention; D-017 resolved-spot scoring): 142 matched resolved confirmed
  spots — Rst error median 0.0004, **p95 0.00846 (< 0.01)**, max 0.023. Unresolved pairs
  (same-lane truths < 2 FWHM apart; 52 truths, 14 matched detections) reported separately:
  median 0.019, p95 0.076 — merged blobs, not localisation error.
- Streak: **19/19** synthetic streak lanes flagged AND unquantified (fraction / contiguous-run /
  plateau / tail-ratio rules).
- Real corpus: **61/61** produce a schema-valid Result or typed refusal; **0 silent nulls**
  (every null-valued Q carries provenance=refused + refusal). Statuses: 58 degraded, 3 refused,
  0 succeeded (pre-VLM: lane count/labels refused on every plate → E_LANE_COUNT_UNKNOWN,
  E_NO_REFERENCE_LANE; E_NO_FRONT on all; E_FRAME_OVERRUN on 53; clipping gates on 20).
- Fixed on the way (all logged): M-011 rectified-frame off-by-one (origin bias −0.9 → −0.1 px);
  D-016 VIF abstention suspended (estimator uncalibrated); band ends at the detected origin.

---
**Attempt 4 (2026-08-26, D-019 structure):** after M-012/M-013 the real-noise-texture variant is
declared not constructible from this corpus of reaction plates (four constructions each carried
chemistry or tiling artefacts at the ensemble's sensitivity). The FP arm is measured on 200
synthetic-noise blanks (Null A, ≥200 realisations); the 80 textured plates are a labelled
diagnostic (`diagnostic_not_null`) with their own phantom rate and tile-row histogram, excluded
from the gate number. Recall arm unchanged (250 spotted plates). Evidence regenerating at HEAD
with a code-fingerprinted cache; numbers follow.
**Gate 5 independent review (2026-08-26): FAIL (conditional) on the self-check above.** Numbers
reproduced exactly and determinism verified (8/8 regenerated cache files byte-identical), but:
20/221 non-streak lanes (9.0%) were falsely flagged as streaking, suppressing 33 real detections;
scoring those lifts the resolved-spot p95 from 0.0085 to 0.0117. Mechanisms: the run rule merged
two adjacent spots into one > 2.5-FWHM run; the tail rule fired on degenerate EMG fits
(sigma 2.7 / tau 37 on a plain Gaussian). D-017's 2-FWHM threshold was post hoc (1 FWHM gives
0.0098). Also confirmed: real-corpus arm clean (0 silent nulls, no numeric rf, E_NO_FRONT
everywhere); adversarial streak plate handled exactly; mode-convention residual +0.26 px on
heavy-tailed spots (≈0.002 Rst, immaterial). **Fixes applied (M-014):** AIC selection between an
explicit Gaussian fit and the EMG; run rule ignores runs containing ≥ 2 tiered peaks unless
flat-topped; tail rule uses only the dominant non-degenerate peak; false-streak rate is now a
gated metric (≤ 2%); D-017 threshold 1 FWHM with the sensitivity table in the evidence; gate5
cache fingerprinted. Re-run in progress; re-review follows.

---
**Attempt 4 result (HEAD 5980967 code, D-019 structure) — Gate 4 final for this iteration: NOT MET
(recall arm).** Eval split (100 synthetic-noise blanks, 315 observable ≥5σ spots): a ≥ 0.50 →
FP **0.14** (≤ 0.2 ✓), recall **0.933** (< 0.95 ✗); a ≥ 0.55 → FP 0.11, recall 0.927. Pooled
(200 blanks / 631 spots): a ≥ 0.50 → FP 0.105, recall 0.924. Textured diagnostic family: ~1.4
phantoms/plate spread uniformly over tile rows (real material at the ensemble's sensitivity,
M-013) — an upper bound, not the gate number. Recall is the open arm: ~0.93 at a ≥ 0.5.
Shipped point stays OPERATING_POINT_v2 (reported 0.55 / candidate 0.40).

## Gate 5 · current state (after M-014, evidence at HEAD): NOT MET (position arm; streak FP)
Position p95 **0.0132** (< 0.01 ✗) on 156 resolved (1 FWHM) confirmed spots, median 0.0005;
sensitivity: 0.0227 (0 FWHM) … 0.0112 (2 FWHM). Streak: 19/19 true streaks flagged; **false-streak
rate 5.0%** (11/221; target ≤ 2%) — residual mechanisms: two spots ~20-30 px apart forming one
> 2.5-FWHM run when only one of them is tiered, and EMG fits whose window spans a neighbouring
peak (tau/sigma 10.8). Real corpus: 61/61 typed, 0 silent nulls ✓.

## Owner decision (2026-08-26 20:40, D-020): fast-forward to a working end-to-end system
Gates 4 and 5 are recorded above as NOT MET with evidence; the build proceeds to Phases 6-12 to
deliver a usable system (upload → result/refusal → review), then returns to the open arms in
iteration. No gate is lowered; every accuracy-bearing field stays refused/uncalibrated where the
evidence says so.

---

## Gate 9 — Correlations / null battery: PASS (2026-08-26)

`uv run python scripts/gate9_check.py --shuffles 500 --plates 8` → `reports/gate9.json`.

- 500 metadata shuffles (N1 breaks the X–Y link, N2 breaks the image–metadata link) over an 8-plate
  synthetic cohort carrying no chemistry: **1.8%** of shuffled cohorts surfaced any finding, Wilson
  upper 95% **3.4%**, against the nominal q = 0.10. Per-hypothesis rate: H11 1.8%, everything else 0
  (spec 02 §6 N3 disables any hypothesis above 5%).
- On the unshuffled cohort: **nothing reported**; H16/H11/H14/H17 suppressed (confound or
  underpowered-design), the rest `insufficient_data` with the §8.1 wording and the unlock ladder.
- Every confound check runs (C01–C04, C08, C11 against the capture matrix; C03/C06/C07/C10 as hard
  vetoes). The brief's failure condition — "if it reports a confident finding from 7 plates, the gate
  has failed" — does not fire: the family arithmetic (m ≤ q·n!/2) refuses before any statistic is read.

## Gate 10 — API, persistence, byte-identical re-run: PASS (2026-08-26)

`uv run python scripts/gate10_check.py --images dataset --n 3` → `reports/gate10.json`.

- Schema validation **3/3 (100%)**: structural check against `schemas/result_v1.schema.json` plus the
  frozen pydantic model (`extra="forbid"`).
- Replay **3/3 byte-identical**: each run re-executed at its recorded pipeline version reproduced
  `result_sha256`, and each original row was superseded by its replay (never edited).
- API contract: `GET /api/v1/runs/{id}` returns the stored bytes verbatim; unknown run → typed 404;
  a correction against a stale `result_sha256` → typed 409.

## Gate 7 / 8 — BLOCKED, not failed (2026-08-26)

Gate 7 (calibration, ECE ≤ 0.10) and Gate 8 (VLM field accuracy) both require the labelled set from
Gate 6, which requires a chemist. Per BUILD_BRIEF §10 the correct response is to build against
synthetic ground truth and mark every accuracy field as not computed:

- `tlc/calibration/calibrate.py` implements the map (isotonic/PAVA), leave-one-plate-out grouped CV,
  ECE and a cluster bootstrap over plates; it **refuses** below 30 labelled plates. Verified on
  synthetic labels: ECE 0.034, 95% interval [0.027, 0.068] over 40 plates × 12 bands.
- No calibration map is shipped in `config/`, and a test asserts that (a shipped map would imply a
  passed Gate 7). Confidence remains `provenance: refused` with `E_UNCALIBRATED` in every result.
- The VLM layer runs in `off`/`replay` only; no API credentials exist in this environment.

## Gate 5 — Position, streaks, real corpus: PASS (2026-08-26, re-run after D-026/D-027)

`uv run python scripts/gate5_check.py` → `reports/gate5_evidence.json` (cache rebuilt at the new code
fingerprint).

| arm | before | now | gate |
|---|---|---|---|
| Rst error p95, resolved spots | 0.0132 | **0.00984** (median 0.0022, max 0.0240, n = 173) | ≤ 0.01 |
| false streak flags on clean lanes | 5.0% (11/221) | **0.0%** (0/221) | ≤ 2% |
| true streak lanes flagged and unquantified | 19/19 | **19/19** | all |
| real corpus: result or typed refusal | 61/61, 0 silent nulls | **61/61, 0 silent nulls** | all |

What changed: D-026 (position = the ensemble consensus row, chosen on the tuning half) and D-027
(a streak is unexplained, flat-topped elevation; four measured changes to the rule). M-015 records the
dominant-peak bug the streak work exposed; M-016 records that the rule was designed on the gate's own
seeds and re-measured on a fresh range (12000-12059: 1.32% false, 13/13 true streaks caught,
`reports/exp_streak_rule_heldout.json`).

## Gate 4 — Detection: NOT MET on the recall arm, but decidably so now (2026-08-26, attempt 4)

Two things changed since attempt 3, neither of them a weakening:

1. **M-017** — the gate had been matching detections to the EMG shape parameter `mu` instead of the
   MODE, the position convention the pipeline reports (D-014) and Gate 5 scores. On tailed spots the
   two differ by 3-7 px against a matching tolerance of 4-7 px, so correctly-found bands were scored
   as misses. Every recall figure published before this is understated.
2. **D-029** — the battery grew from 250 to 600 spotted plates (1509 observable >=5 sigma spots)
   because at 250 the 95% interval on recall was [0.918, 0.956] and could not decide a 0.95 bound.

At the tuning-selected point (agreement >= 0.50, p_med <= 0.0656, z >= 0 — now shipped as
`OPERATING_POINT_v3` through `config/pipeline/v0.6.0.toml`, D-030):

| split | recall at >=5 sigma | n | false bands / synthetic-noise blank (n=100) |
|---|---|---|---|
| tuning (even seeds) | 0.9469 | 753 | 0.07 |
| eval (odd seeds, held out) | 0.9630 | 756 | 0.14 |
| **pooled** | **0.9549** | **1509** | — |

**Verdict.** The false-positive arm is **met** (0.07-0.14 against 0.2), on synthetic-noise blanks
only — the phantom rate on genuine solvent-only plates remains NOT MEASURED (D-019, M-013). The
recall arm is **met on the point estimate (0.955) and not met with confidence**: the pooled 95%
interval [0.9433, 0.9643] contains the 0.95 bound, and the tuning split alone reaches only 0.9469.
The gate script therefore reports `gate4_pass: false`, which is the honest reading.

Where the missing recall lives (`reports/exp_recall_miss.json`): 0.83 at 5-6 sigma, 0.89 at 6-8,
0.95 at 8-12, 0.98 above. `reports/exp_decision_rule.json` rules out a cheap fix — re-scoring the
same ensemble evidence with any z-weighted rule reaches at best 0.93 on tuning at ANY false-positive
rate, so the gap is in detection, not in the decision.

## Gate 5 — re-run at OPERATING_POINT_v3 (2026-08-26): still PASS

Changing the reported tier from agreement >= 0.60 (what the config actually resolved to, M-018) to
>= 0.50 admits more bands — 195 confirmed spots instead of 178 — and the arms hold:

| arm | value at v3 | gate |
|---|---|---|
| Rst error p95, resolved spots | **0.00984** (median 0.0022, max 0.0240, n = 174) | ≤ 0.01 |
| false streak flags on clean lanes | **0.0%** (0 / 221) | ≤ 2% |
| true streak lanes flagged and unquantified | **19 / 19** | all |
| real corpus: result or typed refusal | **61 / 61**, 0 silent nulls | all |

`scripts/gate5_check.py` had also named `OPERATING_POINT_v2.json` directly (the same M-018 pattern);
it now resolves the operating point through the shipped pipeline config, as do the two Gate 4
diagnostics and the per-commit sentinel.

## Gate 5 — re-run after the H05 position fix (2026-08-27): PASS

| arm | value | gate |
|---|---|---|
| Rst error p95, resolved spots | **0.00984** (median 0.0022, max 0.0240, n = 174) | ≤ 0.01 |
| false streak flags on clean lanes | **0.0%** (0 / 221) | ≤ 2% |
| true streak lanes flagged and unquantified | **19 / 19** | all |
| real corpus: result or typed refusal | **61 / 61**, 0 silent nulls | all |

Two changes since the last run, one kept and one withdrawn. Kept: D-036, a streaking lane reports its
positions with a widened interval and withholds only areas, which recovered 18 band positions across
the real corpus at no cost to any arm. Withdrawn: D-032, width-aware clustering, which cost the
position arm at every cap tried and bought two counted bands out of 119 — the numbers are in the
decision entry.
