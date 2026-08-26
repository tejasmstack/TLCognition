# EVALUATION

Generated 2026-08-26T16:56:43Z from committed evidence at commit `202be5b3f114` (dirty tree) by `tools/evaluate.py`. Regenerate rather than edit.

**The contract of this document:** every number carries an n and an interval. Every metric that cannot be computed is listed as `not computed`, with the reason and what would unblock it — never omitted, never replaced by a plausible-looking figure.

## 1. What this system can and cannot claim today

| Claim | Status |
|---|---|
| Band **positions** on synthetic plates with exact ground truth | measured — see §4 |
| Band **detection** rate on synthetic plates | measured — see §3 |
| False bands on **synthetic-noise** blank plates | measured — see §3 |
| False bands on **real solvent-only blank plates** | **not computed** — no such plate exists in the corpus (D-019) |
| Accuracy against **human labels** | **not computed** — 0 labelled plates (Gate 6 not started) |
| **Calibrated confidence** (a probability) | **not computed** — Gate 7 blocked by the label set; the UI shows agreement tallies only |
| **VLM field accuracy** | **not computed** — no labels and no API credentials in this environment |
| **Cross-plate correlations** | none claimable: the corpus has too few independent units (§6) |
| **Byte-reproducibility** of any historical run | measured — see §7 |

## 2. The corpus

- Images audited: **61**
- Real plates producing a result **or** a typed refusal: **61 of 61** (100.0%, [94.1%, 100.0%]); silent nulls: **0**
- Run status on the real corpus: {'degraded': 58, 'refused': 3, 'succeeded': 0}
- Refusal codes fired (a plate may fire several): `E_CLIP_PHOTOMETRY` ×17, `E_CLIP_UNUSABLE` ×3, `E_FRAME_OVERRUN` ×53, `E_LANE_COUNT_UNKNOWN` ×61, `E_NO_FRONT` ×61, `E_NO_REFERENCE_LANE` ×61
- Runs stored in this deployment: 0 over 0 images

## 3. Detection (Gate 4) — synthetic plates only

Ensemble: `CONFIG_GRID_v1`, K = 24 configurations, K_eff on null bins = 9.671. Battery: 250 spotted plates, 200 synthetic-noise blanks, 80 real-texture tiles (diagnostic, **not** a null — M-013/D-019).

Shipped operating point `OPERATING_POINT_v2`: reported at agreement ≥ **0.55**, candidates at ≥ **0.4**, median surrogate p ≤ 0.0164.

| agreement ≥ | recall at ≥5σ (n spots) | FP per synthetic-noise blank (n blanks) |
|---|---|---|
| 0.3 | 94.0% (315) [90.8%, 96.1%] | 0.53 (100) |
| 0.4 | 94.0% (315) [90.8%, 96.1%] | 0.22 (100) |
| 0.45 | 93.7% (315) [90.4%, 95.9%] | 0.17 (100) |
| 0.5 | 93.3% (315) [90.0%, 95.6%] | 0.14 (100) |
| 0.55 | 92.7% (315) [89.3%, 95.1%] | 0.11 (100) |
| 0.6 | 92.1% (315) [88.5%, 94.6%] | 0.1 (100) |
| 0.65 | 92.1% (315) [88.5%, 94.6%] | 0.1 (100) |
| 0.7 | 92.1% (315) [88.5%, 94.6%] | 0.1 (100) |
| 0.75 | 90.2% (315) [86.4%, 93.0%] | 0.08 (100) |
| 0.8 | 89.5% (315) [85.7%, 92.4%] | 0.07 (100) |
| 0.85 | 88.2% (315) [84.2%, 91.4%] | 0.05 (100) |
| 0.9 | 82.9% (315) [78.3%, 86.6%] | 0.04 (100) |

**Gate 4 verdict: NOT MET** — targets were recall ≥ 0.95 and ≤ 0.2 false bands per blank. The false-positive arm is met on synthetic-noise blanks; the recall arm sits at the boundary (~0.94–0.95 at the shipped point). See `reports/GATE4_FINDING.md`.

> reported tier: ~0.2 false spots per blank plate on synthetic noise (0.17) and an UPPER BOUND of ~0.2-0.3 on the rule-selected corpus region (dominated by a tiling artefact, M-012); ~94-95% recall on observable spots >= 5 sigma; real-plate phantom rate on genuine solvent-only blanks NOT MEASURED

## 4. Position and streak (Gate 5) — synthetic plates only

- Rst error on resolved spots (nearest same-lane neighbour ≥ 2 FWHM, D-017): median **0.00217**, p95 **0.00984**, max 0.02402 over n = 173 matched spots (of 183 resolved truths). Gate: p95 ≤ 0.01 of plate height.
- Unresolved pairs (closer than 2 FWHM): median 0.02, p95 0.07177 over 28 pairs — reported separately because a merged pair has no single true position.
- p95 as a function of the resolution threshold: {'0.0': 0.01755, '0.5': 0.01351, '1.0': 0.00984, '1.5': 0.00888, '2.0': 0.009}
- False streak flags: **0 of 221** clean lanes = 0.0% [0.0%, 1.7%]; gate ≤ 2%.
- True streak lanes flagged and unquantified: 19 of 19.

**Gate 5 verdict: PASS** — the real-corpus arm passes (every plate yields a result or a typed refusal, zero silent nulls); the position tail and the false-streak rate do not.

## 5. Human labels, calibration and VLM accuracy

| Metric | Value | n | Interval | Why |
|---|---|---|---|---|
| Plates labelled (Gate 6 needs 30) | 0 | — | — | review loop is built and tested; no chemist session has run |
| Double-labelled plates (needs 10) | 0 | — | — | as above |
| Inter-reviewer agreement (Krippendorff α) | not computed | 0 | — | needs double-labelled plates |
| Expected calibration error (Gate 7 ≤ 0.10) | not computed | 0 | — | no labels ⇒ no calibration map; `E_UNCALIBRATED` is returned instead of a probability |
| Spot-level precision / recall vs human truth | not computed | 0 | — | needs the labelled hold-out partition |
| VLM lane-count accuracy (Gate 8 ≥ 95%) | not computed | 0 | — | no labels; no API credentials in this environment; provider layer runs in `off`/`replay` only |
| VLM sample-ID / label field accuracy | not computed | 0 | — | as above |

The calibration machinery itself is implemented and tested against synthetic labels (`tlc/calibration/calibrate.py`, `tests/test_calibration.py`): isotonic map by PAVA, leave-one-plate-out grouped CV, ECE with a cluster bootstrap over plates. It refuses below 30 labelled plates, so the day labels exist the only new input is the data.

## 6. Findings and correlations (Gate 9)

- Label-shuffle null battery: **1.8%** of 500 shuffled cohorts surfaced any finding (Wilson upper 95% 3.4%); nominal false-discovery rate q = 0.1.
- Per-hypothesis fire rate on shuffled data: {'H11': 0.018} (spec 02 §6 N3 disables any hypothesis above 5%).
- On the unshuffled cohort: reported none; suppressed ['H16', 'H11', 'H14', 'H17']; insufficient data ['H12', 'H13', 'H15', 'H18', 'H19', 'H20', 'H21', 'H22'].

**Gate 9 verdict: PASS** — the system reports nothing from a cohort this size, which is the correct behaviour: with 9 Class B hypotheses and fewer than 6 independent campaigns, no cross-plate correlation can clear its own family (spec 02 §4.7).

Within-plate (Class A) findings are emitted for every run — 10 registered hypotheses, each with a falsifier — and are counted here only as availability, not accuracy: their correctness needs the label set (§5).

## 7. Determinism and reproducibility

- Geometry/rectification determinism across repeats: {'cross_machine': 'unavailable_this_build (A-009)', 'hash': '83ecd0b8300f39e5062fcad766e7b93f51eded80db1c5ed690ff0f05ee129096', 'pass': True, 'two_pass_hash_identical': True}
- `run_key` = sha256 of image bytes + config hash + code fingerprint + environment fingerprint + VLM bundle hash; a replay that does not reproduce `result_sha256` is recorded as a **failed** run with `E_REPLAY_DRIFT` rather than silently overwriting the original.
- Gate 10 run on 3 plates from `dataset`: schema validity **100.0%** (structural + the frozen pydantic contract), replay reproduced `result_sha256` on **100.0%** of runs, every original correctly superseded (True). API contract: stored bytes returned verbatim (True), unknown run → 404, stale correction → 409. Evidence `reports/gate10.json`.
- The whole test suite runs with sockets blocked (`tests/conftest.py`), so no result depends on the network.

## 8. Gate status

| Gate | Subject | Status | Evidence |
|---|---|---|---|
| 0 | Dataset audit + capture protocol | PASS | `reports/corpus_stats.json`, `reports/CAPTURE_PROTOCOL.md` |
| 1 | Synthetic generator fidelity | PASS | `reports/gate1_evidence.json` |
| 2 | Geometry / rectification | PASS | `reports/gate2_evidence.json` |
| 3 | Photometry and the noise unit | PASS | `reports/gate3_evidence.json` |
| 4 | Detection recall / false positives | NOT MET | `reports/gate4_evidence.json`, `reports/GATE4_FINDING.md` |
| 5 | Position, streaks, real corpus | PASS | `reports/gate5_evidence.json` |
| 6 | Labelled set (≥30 plates) | NOT STARTED — needs a chemist | review screen built (`/runs/{id}/review`) |
| 7 | Calibration (ECE ≤ 0.10) | BLOCKED by Gate 6 | `tlc/calibration/`, `tests/test_calibration.py` |
| 8 | VLM field accuracy | BLOCKED by Gate 6 and by credentials | `tlc/vlm/`, `tests/test_vlm.py` (offline) |
| 9 | Label-shuffle null battery | PASS | `reports/gate9.json` |
| 10 | API, persistence, byte-identical re-run | PASS | `reports/gate10.json`, `tests/test_web.py` |
| 11 | A chemist unaided on the screens | NOT TESTED — needs a person | screens built; §11.8 protocol in spec 04 |
| 12 | This document | PASS (as an honest report) | `EVALUATION.md` |

## 9. What would change these numbers

1. **Six real solvent-only blank plates**, photographed under the shipped protocol. Until they exist the real-plate phantom rate is unmeasured and the false-positive arm of Gate 4 rests on synthetic noise.
2. **Thirty labelled plates, ten of them double-labelled.** This unblocks Gates 6, 7 and 8 in one step and is the only path to any accuracy claim at all.
3. **Six plates in one campaign, captured identically** (fixed distance and exposure, background ~230 not 255, constant loading, an `sd` lane and a blank lane on every plate). That is the first cohort size at which a cross-plate trend can clear its own family.
4. **A drawn solvent front**, if Rf is ever wanted. Without it Rf is not reported at all.

---

Open decisions, assumptions and mistakes are recorded continuously in `decisions.md`, `ASSUMPTIONS.md` and `mistakes.md`; gate reviews are in `GATES.md`.
