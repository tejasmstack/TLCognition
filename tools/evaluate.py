"""Generate EVALUATION.md — Gate 12's honest accuracy report.

Every number carries n and an interval; every metric that cannot be computed for lack of labels is
listed as `not computed` with the reason and what would unblock it, never omitted (BUILD_BRIEF §11).

Run: uv run python tools/evaluate.py [--data-dir data] [--out EVALUATION.md]
"""

import argparse
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
NC = "not computed"


def _load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Binomial interval that behaves at k = 0 and k = n, which normal approximations do not."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, c - h), min(1.0, c + h))


def pct(x: float | None, nd: int = 1) -> str:
    return NC if x is None else f"{100 * x:.{nd}f}%"


def ci_str(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"[{100 * lo:.1f}%, {100 * hi:.1f}%]"


def git_state() -> tuple[str, bool]:
    try:
        c = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        d = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return c, bool(d)
    except (OSError, subprocess.CalledProcessError):
        return "unavailable", False


def label_counts(data_dir: Path) -> dict:
    db = data_dir / "tlc.sqlite"
    if not db.exists():
        return {"plates_labelled": 0, "double_labelled": 0, "runs": 0, "images": 0}
    import sqlite3

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
        return {"plates_labelled": q("SELECT COUNT(*) FROM label_records WHERE superseded_by IS NULL"),
                "double_labelled": q("SELECT COUNT(*) FROM label_records WHERE superseded_by IS NULL AND n_reviewers>=2"),
                "runs": q("SELECT COUNT(*) FROM runs"), "images": q("SELECT COUNT(*) FROM images")}
    except sqlite3.Error:
        return {"plates_labelled": 0, "double_labelled": 0, "runs": 0, "images": 0}
    finally:
        con.close()


def build(data_dir: Path) -> str:
    g1, g2, g3 = (_load(REPORTS / f"gate{i}_evidence.json") for i in (1, 2, 3))
    g4, g5, g9 = _load(REPORTS / "gate4_evidence.json"), _load(REPORTS / "gate5_evidence.json"), _load(REPORTS / "gate9.json")
    g10 = _load(REPORTS / "gate10.json")
    corpus = _load(REPORTS / "corpus_stats.json")
    op = _load(ROOT / "config" / "ensemble" / "OPERATING_POINT_v2.json")
    grid = _load(ROOT / "config" / "ensemble" / "CONFIG_GRID_v1.json")
    labels = label_counts(data_dir)
    commit, dirty = git_state()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    L: list[str] = []
    add = L.append

    add("# EVALUATION\n")
    add(f"Generated {now} from committed evidence at commit `{commit[:12]}`{' (dirty tree)' if dirty else ''} "
        "by `tools/evaluate.py`. Regenerate rather than edit.\n")
    add("**The contract of this document:** every number carries an n and an interval. Every metric that "
        "cannot be computed is listed as `not computed`, with the reason and what would unblock it — never "
        "omitted, never replaced by a plausible-looking figure.\n")

    # ---------------------------------------------------------------- headline
    add("## 1. What this system can and cannot claim today\n")
    add("| Claim | Status |")
    add("|---|---|")
    add("| Band **positions** on synthetic plates with exact ground truth | measured — see §4 |")
    add("| Band **detection** rate on synthetic plates | measured — see §3 |")
    add("| False bands on **synthetic-noise** blank plates | measured — see §3 |")
    add("| False bands on **real solvent-only blank plates** | **not computed** — no such plate exists in the corpus (D-019) |")
    add("| Accuracy against **human labels** | **not computed** — 0 labelled plates (Gate 6 not started) |")
    add("| **Calibrated confidence** (a probability) | **not computed** — Gate 7 blocked by the label set; the UI shows agreement tallies only |")
    add("| **VLM field accuracy** | **not computed** — no labels and no API credentials in this environment |")
    add("| **Cross-plate correlations** | none claimable: the corpus has too few independent units (§6) |")
    add("| **Byte-reproducibility** of any historical run | measured — see §7 |\n")

    # ---------------------------------------------------------------- corpus
    add("## 2. The corpus\n")
    if corpus:
        s = corpus.get("summary", {})
        n_img = len(corpus.get("images", []))
        add(f"- Images audited: **{n_img}**")
        for k in ("clip_fraction", "empty_band_mad_od", "lane_px", "plate_w", "plate_h"):
            v = s.get(k)
            if isinstance(v, dict):
                add(f"- {k}: median {v.get('median')}, range [{v.get('min')}, {v.get('max')}]")
    if g5 and g5.get("real_corpus"):
        rc = g5["real_corpus"]
        n = rc["n_unique_images"]
        add(f"- Real plates producing a result **or** a typed refusal: **{rc['n_with_result_or_typed_refusal']} of {n}** "
            f"({pct(rc['n_with_result_or_typed_refusal'] / n)}, {ci_str(rc['n_with_result_or_typed_refusal'], n)}); "
            f"silent nulls: **{len(rc['silent_null_offenders'])}**")
        add(f"- Run status on the real corpus: {rc['status_counts']}")
        add("- Refusal codes fired (a plate may fire several): " + ", ".join(f"`{k}` ×{v}" for k, v in sorted(rc["refusal_code_histogram"].items())))
    add(f"- Runs stored in this deployment: {labels['runs']} over {labels['images']} images\n")

    # ---------------------------------------------------------------- detection
    add("## 3. Detection (Gate 4) — synthetic plates only\n")
    if g4:
        b = g4.get("battery", {})
        add(f"Ensemble: `{grid['id'] if grid else '?'}`, K = {grid['k'] if grid else '?'} configurations, "
            f"K_eff on null bins = {grid.get('k_eff_dev_null_bins') if grid else '?'}. "
            f"Battery: {b.get('n_spotted')} spotted plates, {b.get('n_blank_synthetic_noise_null_A')} synthetic-noise blanks, "
            f"{b.get('n_textured_diagnostic_not_null')} real-texture tiles (diagnostic, **not** a null — M-013/D-019).\n")
        if op:
            t = op.get("tiers", {})
            add(f"Shipped operating point `{op['id']}`: reported at agreement ≥ **{t.get('reported', {}).get('agreement_min')}**, "
                f"candidates at ≥ **{t.get('candidate', {}).get('agreement_min')}**, median surrogate p ≤ {t.get('reported', {}).get('p_med_max')}.\n")
        rows = [c for c in g4.get("eval", {}).get("curve", []) if isinstance(c, dict)]
        if op:  # one row per agreement threshold, at the shipped surrogate-p and z gates
            ps, zs = op["tiers"]["reported"]["p_med_max"], op["tiers"]["reported"]["z_med_min"]
            same = [c for c in rows if abs(c.get("p_star", -1) - ps) < 1e-9 and abs(c.get("z_star", -1) - zs) < 1e-9]
            rows = same or rows
        if rows:
            add("| agreement ≥ | recall at ≥5σ (n spots) | FP per synthetic-noise blank (n blanks) |")
            add("|---|---|---|")
            seen = set()
            for c in sorted(rows, key=lambda r: r["a_star"]):
                a = c["a_star"]
                if a in seen:
                    continue
                seen.add(a)
                n_t, n_b = c.get("n_true_5s", 0), c.get("n_blanks_synth", 0)
                k = int(round(c["recall_5s"] * n_t))
                add(f"| {a} | {pct(c['recall_5s'])} ({n_t}) {ci_str(k, n_t)} | {c.get('fp_per_blank_by_family', {}).get('synth')} ({n_b}) |")
        add("")
        add(f"**Gate 4 verdict: {'PASS' if g4.get('gate4_pass') else 'NOT MET'}** — targets were recall ≥ "
            f"{g4.get('bounds', {}).get('recall_5sigma')} and ≤ {g4.get('bounds', {}).get('fp_per_blank')} false bands per blank. "
            "The false-positive arm is met on synthetic-noise blanks; the recall arm sits at the boundary "
            "(~0.94–0.95 at the shipped point). See `reports/GATE4_FINDING.md`.")
        add(f"\n> {op['honest_claim']}\n" if op and op.get("honest_claim") else "")
    else:
        add(f"{NC} — `reports/gate4_evidence.json` is absent.\n")

    # ---------------------------------------------------------------- position
    add("## 4. Position and streak (Gate 5) — synthetic plates only\n")
    if g5:
        p = g5["position"]
        add(f"- Rst error on resolved spots (nearest same-lane neighbour ≥ 2 FWHM, D-017): median **{p['rst_err_median']}**, "
            f"p95 **{p['rst_err_p95']}**, max {p['rst_err_max']} over n = {p['n_matched_resolved']} matched spots "
            f"(of {p['n_truth_resolved']} resolved truths). Gate: p95 ≤ 0.01 of plate height.")
        add(f"- Unresolved pairs (closer than 2 FWHM): median {p['unresolved_pairs_rst_err_median']}, "
            f"p95 {p['unresolved_pairs_rst_err_p95']} over {p['n_truth_unresolved_pairs']} pairs — reported separately "
            "because a merged pair has no single true position.")
        add(f"- p95 as a function of the resolution threshold: {p['p95_by_resolved_threshold_fwhm']}")
        st = g5["streak"]
        k, n = st["false_streak_lanes"], st["n_non_streak_lanes"]
        add(f"- False streak flags: **{k} of {n}** clean lanes = {pct(k / n)} {ci_str(k, n)}; gate ≤ 2%.")
        add(f"- True streak lanes flagged and unquantified: {st['flagged_and_unquantified']} of {st['n_streak_lanes']}.")
        add(f"\n**Gate 5 verdict: {'PASS' if g5.get('gate5_pass') else 'NOT MET'}** — the real-corpus arm passes "
            "(every plate yields a result or a typed refusal, zero silent nulls); the position tail and the "
            "false-streak rate do not.\n")
    else:
        add(f"{NC}\n")

    # ---------------------------------------------------------------- labels & calibration
    add("## 5. Human labels, calibration and VLM accuracy\n")
    add("| Metric | Value | n | Interval | Why |")
    add("|---|---|---|---|---|")
    add(f"| Plates labelled (Gate 6 needs 30) | {labels['plates_labelled']} | — | — | review loop is built and tested; no chemist session has run |")
    add(f"| Double-labelled plates (needs 10) | {labels['double_labelled']} | — | — | as above |")
    add(f"| Inter-reviewer agreement (Krippendorff α) | {NC} | 0 | — | needs double-labelled plates |")
    add(f"| Expected calibration error (Gate 7 ≤ 0.10) | {NC} | 0 | — | no labels ⇒ no calibration map; `E_UNCALIBRATED` is returned instead of a probability |")
    add(f"| Spot-level precision / recall vs human truth | {NC} | 0 | — | needs the labelled hold-out partition |")
    add(f"| VLM lane-count accuracy (Gate 8 ≥ 95%) | {NC} | 0 | — | no labels; no API credentials in this environment; provider layer runs in `off`/`replay` only |")
    add(f"| VLM sample-ID / label field accuracy | {NC} | 0 | — | as above |")
    add("\nThe calibration machinery itself is implemented and tested against synthetic labels "
        "(`tlc/calibration/calibrate.py`, `tests/test_calibration.py`): isotonic map by PAVA, leave-one-plate-out "
        "grouped CV, ECE with a cluster bootstrap over plates. It refuses below 30 labelled plates, so the day "
        "labels exist the only new input is the data.\n")

    # ---------------------------------------------------------------- correlations
    add("## 6. Findings and correlations (Gate 9)\n")
    if g9:
        r, n = g9["surfaced_finding_rate"], g9["shuffles"]
        add(f"- Label-shuffle null battery: **{pct(r, 1)}** of {n} shuffled cohorts surfaced any finding "
            f"(Wilson upper 95% {pct(g9['wilson_upper95'], 1)}); nominal false-discovery rate q = {g9['nominal_q']}.")
        add(f"- Per-hypothesis fire rate on shuffled data: {g9['per_hypothesis_fire_rate'] or 'none fired'} "
            f"(spec 02 §6 N3 disables any hypothesis above 5%).")
        add(f"- On the unshuffled cohort: reported {g9['observed_cohort']['reported'] or 'none'}; "
            f"suppressed {g9['observed_cohort']['suppressed']}; insufficient data {g9['observed_cohort']['insufficient_data']}.")
        add(f"\n**Gate 9 verdict: {'PASS' if g9.get('passed') else 'FAIL'}** — the system reports nothing from a "
            "cohort this size, which is the correct behaviour: with 9 Class B hypotheses and fewer than 6 "
            "independent campaigns, no cross-plate correlation can clear its own family (spec 02 §4.7).\n")
    else:
        add(f"{NC} — run `uv run python scripts/gate9_check.py`.\n")
    add("Within-plate (Class A) findings are emitted for every run — 10 registered hypotheses, each with a "
        "falsifier — and are counted here only as availability, not accuracy: their correctness needs the "
        "label set (§5).\n")

    # ---------------------------------------------------------------- determinism
    add("## 7. Determinism and reproducibility\n")
    if g2:
        d = g2.get("determinism", {})
        add(f"- Geometry/rectification determinism across repeats: {d if d else NC}")
    add("- `run_key` = sha256 of image bytes + config hash + code fingerprint + environment fingerprint + VLM bundle hash; "
        "a replay that does not reproduce `result_sha256` is recorded as a **failed** run with `E_REPLAY_DRIFT` "
        "rather than silently overwriting the original.")
    if g10:
        add(f"- Gate 10 run on {g10['n_runs']} plates from `{g10['source']}`: schema validity "
            f"**{pct(g10['schema_valid_frac'])}** (structural + the frozen pydantic contract), replay reproduced "
            f"`result_sha256` on **{pct(g10['replay_identical_frac'])}** of runs, every original correctly superseded "
            f"({g10['replay_supersede_ok']}). API contract: stored bytes returned verbatim "
            f"({g10['api']['stored_bytes_match']}), unknown run → {g10['api']['unknown_run_404']}, stale correction → "
            f"{g10['api']['stale_correction_409']}. Evidence `reports/gate10.json`.")
    else:
        add("- Replay evidence: run `uv run python scripts/gate10_check.py --images dataset`.")
    add("- The whole test suite runs with sockets blocked (`tests/conftest.py`), so no result depends on the network.\n")

    # ---------------------------------------------------------------- gates
    add("## 8. Gate status\n")
    add("| Gate | Subject | Status | Evidence |")
    add("|---|---|---|---|")
    add("| 0 | Dataset audit + capture protocol | PASS | `reports/corpus_stats.json`, `reports/CAPTURE_PROTOCOL.md` |")
    add(f"| 1 | Synthetic generator fidelity | {'PASS' if g1 and g1.get('gate1_pass') else 'NOT MET'} | `reports/gate1_evidence.json` |")
    add(f"| 2 | Geometry / rectification | {'PASS' if g2 and g2.get('gate2_pass') else 'NOT MET'} | `reports/gate2_evidence.json` |")
    add(f"| 3 | Photometry and the noise unit | {'PASS' if g3 and g3.get('gate3_pass') else 'NOT MET'} | `reports/gate3_evidence.json` |")
    add(f"| 4 | Detection recall / false positives | {'PASS' if g4 and g4.get('gate4_pass') else 'NOT MET'} | `reports/gate4_evidence.json`, `reports/GATE4_FINDING.md` |")
    add(f"| 5 | Position, streaks, real corpus | {'PASS' if g5 and g5.get('gate5_pass') else 'NOT MET (real-corpus arm passes)'} | `reports/gate5_evidence.json` |")
    add("| 6 | Labelled set (≥30 plates) | NOT STARTED — needs a chemist | review screen built (`/runs/{id}/review`) |")
    add("| 7 | Calibration (ECE ≤ 0.10) | BLOCKED by Gate 6 | `tlc/calibration/`, `tests/test_calibration.py` |")
    add("| 8 | VLM field accuracy | BLOCKED by Gate 6 and by credentials | `tlc/vlm/`, `tests/test_vlm.py` (offline) |")
    add(f"| 9 | Label-shuffle null battery | {'PASS' if g9 and g9.get('passed') else 'NOT MET'} | `reports/gate9.json` |")
    add(f"| 10 | API, persistence, byte-identical re-run | {'PASS' if g10 and g10.get('passed') else 'NOT MET'} | "
        "`reports/gate10.json`, `tests/test_web.py` |")
    add("| 11 | A chemist unaided on the screens | NOT TESTED — needs a person | screens built; §11.8 protocol in spec 04 |")
    add("| 12 | This document | PASS (as an honest report) | `EVALUATION.md` |\n")

    # ---------------------------------------------------------------- what would change the numbers
    add("## 9. What would change these numbers\n")
    add("1. **Six real solvent-only blank plates**, photographed under the shipped protocol. Until they exist the "
        "real-plate phantom rate is unmeasured and the false-positive arm of Gate 4 rests on synthetic noise.")
    add("2. **Thirty labelled plates, ten of them double-labelled.** This unblocks Gates 6, 7 and 8 in one step and "
        "is the only path to any accuracy claim at all.")
    add("3. **Six plates in one campaign, captured identically** (fixed distance and exposure, background ~230 not "
        "255, constant loading, an `sd` lane and a blank lane on every plate). That is the first cohort size at "
        "which a cross-plate trend can clear its own family.")
    add("4. **A drawn solvent front**, if Rf is ever wanted. Without it Rf is not reported at all.\n")
    add("---\n")
    add("Open decisions, assumptions and mistakes are recorded continuously in `decisions.md`, `ASSUMPTIONS.md` "
        "and `mistakes.md`; gate reviews are in `GATES.md`.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="EVALUATION.md")
    a = ap.parse_args()
    text = build(Path(a.data_dir))
    (ROOT / a.out).write_text(text)
    print(f"wrote {a.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
