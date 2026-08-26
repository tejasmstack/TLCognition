"""``GET /api/v1/labels/stats`` numbers for Gate 6 — every figure carries its ``n``.

``label_stats(records)`` takes the current :class:`LabelRecordDraft` per image (one
record per image, the repository de-duplicates). Inter-reviewer agreement rate is the
fraction of double-labelled images whose ORIGINAL pairwise agreement verdict was
``agreed`` or ``agreed_with_trace_dissent`` (an adjudicated record still counts as a
disagreement — resolution does not erase it). The bootstrap resamples plates
(records) with replacement, B=2000, ``numpy.random.Generator(PCG64(seed))``,
percentile interval [2.5, 97.5]. With n_double_labelled < 2 the interval is ``None``.
"""

from collections.abc import Sequence

import numpy as np

from tlc.labels.promote import LabelRecordDraft

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20260826


def bootstrap_rate_ci(
    outcomes: Sequence[int], b: int = BOOTSTRAP_B, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float] | None:
    x = np.asarray(outcomes, dtype=float)
    if x.size < 2:
        return None
    rng = np.random.Generator(np.random.PCG64(seed))
    idx = rng.integers(0, x.size, size=(b, x.size))
    means = x[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def label_stats(records: Sequence[LabelRecordDraft]) -> dict:
    double = [r for r in records if r.n_reviewers >= 2 and r.agreement is not None]
    outcomes = [1 if r.agreement.verdict != "disputed" else 0 for r in double]  # type: ignore[union-attr]
    alphas = [
        r.agreement.krippendorff_alpha_positions  # type: ignore[union-attr]
        for r in double
        if r.agreement.krippendorff_alpha_positions is not None  # type: ignore[union-attr]
    ]
    status_counts = {
        s: sum(1 for r in records if r.status == s)
        for s in ("provisional", "agreed", "disputed", "adjudicated")
    }
    partition_counts = {
        p: sum(1 for r in records if r.partition == p) for p in ("tune", "calibrate", "holdout")
    }
    partition_counts["unassigned"] = sum(1 for r in records if r.partition is None)

    rate = float(np.mean(outcomes)) if outcomes else None
    ci = bootstrap_rate_ci(outcomes)
    return {
        "n_labelled": len(records),
        "n_double_labelled": len(double),
        "status_counts": status_counts,
        "inter_reviewer_agreement": {
            "rate": rate,
            "ci95": None if ci is None else [ci[0], ci[1]],
            "n": len(outcomes),
            "bootstrap_B": BOOTSTRAP_B,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "trace_dissent_counted_as_agreement": True,
        },
        "krippendorff_alpha_positions": {
            "mean": float(np.mean(alphas)) if alphas else None,
            "n": len(alphas),
            "grid_step_y_frac": 0.01,
        },
        "partition_counts": partition_counts,
        "match_tolerance_y_frac": 0.015,
    }
