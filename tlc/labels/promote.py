"""The promoter — §7.7.3 steps 2-5 as pure functions.

Input: the list of NON-superseded reviewer truths for one image (the repository
layer decides supersession; this module never sees a database).

Ambiguities resolved here:

* With three or more truths the spec's pairwise metric is applied to every pair; the
  image is ``agreed`` only if every pair agrees (worst pair wins), and the reported
  ``agreement`` is the worst pair's report (ordered disputed < trace_dissent < agreed).
* Merge (``agreed``): truths are ordered by ``correction_id`` (then reviewer_id) and
  the first is the reference; every other truth's spots are greedily matched to the
  reference at the §7.7.4 tolerance; each reference spot's ``y_frac`` becomes the
  MEDIAN of its own and its matched partners' positions. Reference spots with no
  partner are kept (Jaccard ≥ 0.8 tolerates them); non-reference spots that matched
  nothing are ALSO kept (union) so a dissenting faint/trace spot is not silently
  lost. Strength = the reference spot's strength. Labels/lanes/origin/front/bands
  come from the reference (labels are equal by construction when agreed; origin is
  the median across truths when all have one).
* ``adjudicated``: payload is the adjudicator's truth verbatim; ``agreement`` is the
  ORIGINAL disagreement report, never overwritten.
* Partition assignment (step 6) is deliberately NOT done here: it depends on the
  repository knowing whether the image was promoted before. See ``partition.py``.
"""

from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict

from tlc.labels.agreement import AgreementReport, compare_truths, match_spots
from tlc.labels.truth import ReviewerTruth, TruthSpot

LabelStatus = Literal["provisional", "agreed", "disputed", "adjudicated"]
_RANK = {"disputed": 0, "agreed_with_trace_dissent": 1, "agreed": 2}


class AdjudicationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    derived_from: list[str]  # correction ids in dispute
    agreement: AgreementReport
    truths: list[ReviewerTruth]  # what the adjudicator must see side by side


class LabelRecordDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: LabelStatus
    n_reviewers: int
    agreement: AgreementReport | None
    payload: ReviewerTruth | None  # None while disputed (no truth is authoritative)
    derived_from: list[str]
    adjudication: AdjudicationDraft | None = None
    partition: Literal["tune", "calibrate", "holdout"] | None = None  # set by repository


def _sort_key(t: ReviewerTruth) -> tuple[str, str]:
    return (t.correction_id or "", t.reviewer_id or "")


def _ids(truths: list[ReviewerTruth]) -> list[str]:
    return [t.correction_id for t in truths if t.correction_id is not None]


def worst_pairwise_agreement(truths: list[ReviewerTruth]) -> AgreementReport:
    reports = [
        compare_truths(truths[i], truths[j])
        for i in range(len(truths))
        for j in range(i + 1, len(truths))
    ]
    return min(reports, key=lambda r: _RANK[r.verdict])


def merge_truths(truths: list[ReviewerTruth]) -> ReviewerTruth:
    """Median-merge of agreeing truths; see module docstring for the rule."""
    ref, others = truths[0], truths[1:]
    positions: list[list[float]] = [[s.y_frac] for s in ref.spots]
    extras: list[TruthSpot] = []
    for t in others:
        pairs, _, only_t = match_spots(ref.spots, t.spots)
        idx = {id(s): i for i, s in enumerate(ref.spots)}
        for p, q in pairs:
            positions[idx[id(p)]].append(q.y_frac)
        extras.extend(only_t)
    merged = [
        s.model_copy(update={"y_frac": float(median(ps)), "spot_id": None})
        for s, ps in zip(ref.spots, positions, strict=True)
    ]
    # de-duplicate extras against each other and the merged set
    for e in sorted(extras, key=lambda s: (s.lane_index, s.y_frac)):
        pairs, _, _ = match_spots(merged, [e])
        if not pairs:
            merged.append(e.model_copy(update={"spot_id": None}))
    merged.sort(key=lambda s: (s.lane_index, s.y_frac, s.strength))

    origins = [t.origin_y_frac for t in truths if t.origin_y_frac is not None]
    origin = float(median(origins)) if len(origins) == len(truths) else ref.origin_y_frac
    unreviewed = sorted(
        {u.spot_id: u for t in truths for u in t.unreviewed_machine_spots}.values(),
        key=lambda u: (u.lane_index, u.y_frac, u.spot_id),
    )
    rejected = sorted(
        {(r.spot_id, r.reason): r for t in truths for r in t.rejected}.values(),
        key=lambda r: (r.lane_index, r.y_frac, r.spot_id),
    )
    return ref.model_copy(
        update={
            "spots": merged,
            "origin_y_frac": origin,
            "unreviewed_machine_spots": unreviewed,
            "rejected": rejected,
            "aided": any(t.aided for t in truths),
            "correction_id": None,
            "reviewer_id": None,
            "review_seconds": None,
        }
    )


def promote(truths: list[ReviewerTruth]) -> LabelRecordDraft:
    """Steps 2-4 of §7.7.3. Raises on an empty list (nothing to promote)."""
    if not truths:
        raise ValueError("promote() needs at least one reviewer truth")
    truths = sorted(truths, key=_sort_key)
    ids = _ids(truths)
    if len(truths) == 1:
        return LabelRecordDraft(
            status="provisional",
            n_reviewers=1,
            agreement=None,
            payload=truths[0],
            derived_from=ids,
        )
    report = worst_pairwise_agreement(truths)
    if report.verdict == "disputed":
        return LabelRecordDraft(
            status="disputed",
            n_reviewers=len(truths),
            agreement=report,
            payload=None,
            derived_from=ids,
            adjudication=AdjudicationDraft(derived_from=ids, agreement=report, truths=truths),
        )
    return LabelRecordDraft(
        status="agreed",
        n_reviewers=len(truths),
        agreement=report,
        payload=merge_truths(truths),
        derived_from=ids,
    )


def resolve_adjudication(
    draft: LabelRecordDraft, adjudicator_truth: ReviewerTruth
) -> LabelRecordDraft:
    """Step 5: adjudicator's truth becomes the payload; the disagreement report is kept."""
    if draft.status != "disputed" or draft.adjudication is None:
        raise ValueError("only a disputed draft with an open adjudication can be resolved")
    derived = list(draft.derived_from)
    if adjudicator_truth.correction_id and adjudicator_truth.correction_id not in derived:
        derived.append(adjudicator_truth.correction_id)
    return draft.model_copy(
        update={
            "status": "adjudicated",
            "payload": adjudicator_truth,
            "agreement": draft.agreement,  # retained, never overwritten
            "derived_from": derived,
            "adjudication": None,
        }
    )
