"""Materialise a reviewer's TRUTH for an image: ``truth = apply_ops(result, ops)``.

§7.7.3 step 1. Pure function over a plain result dict (the ``Result`` schema dumped
with ``model_dump()`` — python or json mode both work) and a list of ops.

Interpretation choices (spec ambiguities, resolved here):

* **Silence is not assent.** A machine spot the reviewer neither confirmed, moved nor
  rejected goes to ``unreviewed_machine_spots``; it is NOT in ``spots``.
* Which machine spots are reviewable: every result spot whose status is not
  ``"rejected"`` (the machine already dropped those). Suppressed-streak and candidate
  spots are still offered to the reviewer because the reviewer is the authority.
* Machine spots carry no strength in the result schema; confirmed/moved machine spots
  get ``strength="strong"`` unless the reviewer also adds a spot (which carries its
  own strength). This is documented in ASSUMPTIONS-style comment here; a later
  amplitude-based strength can replace it without changing the truth shape.
* ``spot.move`` implies confirmation (source ``"machine_moved"``). A confirm followed
  by a reject (or vice versa) applies in op order; the last op wins.
* ``front_y_frac``: ``None`` means "not reviewed / unknown"; the string ``"absent"``
  means confirmed absent (``OpFrontSet(y_frac=None)`` or machine
  ``front_provenance == "absent"``).
* ``lane.set_count`` truncates or extends ``lane_labels`` (new lanes get
  ``"UNREADABLE"``); spots in lanes beyond the new count are dropped from ``spots``
  and recorded under ``rejected`` with reason ``"lane_removed"``.
* ``aided`` is ``not blind``: whether the reviewer saw the machine's overlay.
"""

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tlc.labels.corrections import (
    CorrectionDoc,
    CorrectionOp,
    CorrectionOpAdapter,
    OpBandSet,
    OpFrontSet,
    OpLaneRelabel,
    OpLaneSetCount,
    OpLaneStreak,
    OpOriginSet,
    OpPlateReject,
    OpSampleId,
    OpSpotAdd,
    OpSpotConfirm,
    OpSpotMove,
    OpSpotReject,
    Strength,
)

SpotSource = Literal["machine_confirmed", "reviewer_added", "machine_moved"]
UNREADABLE = "UNREADABLE"


class TruthSpot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lane_index: int
    y_frac: float
    strength: Strength
    source: SpotSource
    spot_id: str | None = None  # machine id when the spot derives from a machine spot


class UnreviewedSpot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spot_id: str
    lane_index: int
    y_frac: float
    machine_status: str


class RejectedSpot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    spot_id: str
    lane_index: int
    y_frac: float
    reason: str
    note: str | None = None


class TruthBand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    y0_frac: float
    y1_frac: float


class ReviewerTruth(BaseModel):
    """Canonical reviewer truth (§7.7.3 step 1), deterministic ordering everywhere."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    n_lanes: int
    lane_labels: list[str]
    lane_streaking: list[bool]
    bands: list[TruthBand]
    origin_y_frac: float | None
    front_y_frac: float | Literal["absent"] | None
    anchor_y_frac: float | None = None  # Rst anchor row (machine), if any; for Rst-scaled deltas
    spots: list[TruthSpot]
    unreviewed_machine_spots: list[UnreviewedSpot]
    rejected: list[RejectedSpot]
    plate_rejected: bool
    plate_reject_reason: str | None = None
    sample_id: str | None
    conditions: str | None = None
    aided: bool
    correction_id: str | None = None
    reviewer_id: str | None = None
    review_seconds: int | None = Field(default=None, ge=0)

    def strong_spots(self) -> list[TruthSpot]:
        return [s for s in self.spots if s.strength == "strong"]


# ---------------------------------------------------------------------------
# result-dict readers (tolerant of Q envelopes and bare values)
# ---------------------------------------------------------------------------


def _val(x: Any) -> Any:
    if isinstance(x, Mapping) and "value" in x:
        return x["value"]
    return x


def _plate_height(result: Mapping[str, Any]) -> float | None:
    shape = (result.get("geometry") or {}).get("rectified_shape")
    if shape and shape[0]:
        return float(shape[0])
    return None


def _px_to_frac(px: Any, h: float | None) -> float | None:
    px = _val(px)
    if px is None or h is None:
        return None
    return float(px) / h


def _machine_spots(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for s in result.get("spots") or []:
        if s.get("status") == "rejected":
            continue
        y = _val(s.get("y_frac"))
        if y is None:
            continue
        out.append(
            {
                "spot_id": str(s["id"]),
                "lane_index": int(s["lane_index"]),
                "y_frac": float(y),
                "status": str(s.get("status", "candidate")),
            }
        )
    out.sort(key=lambda d: (d["lane_index"], d["y_frac"], d["spot_id"]))
    return out


# ---------------------------------------------------------------------------
# apply_ops
# ---------------------------------------------------------------------------


def _coerce_ops(ops: Iterable[CorrectionOp | Mapping[str, Any]]) -> list[CorrectionOp]:
    return [o if isinstance(o, BaseModel) else CorrectionOpAdapter.validate_python(o) for o in ops]


def apply_ops(
    result_dict: Mapping[str, Any],
    ops: Iterable[CorrectionOp | Mapping[str, Any]],
    *,
    blind: bool = False,
    correction_id: str | None = None,
    reviewer_id: str | None = None,
    review_seconds: int | None = None,
) -> ReviewerTruth:
    """Compose ``result ⊕ ops`` into a :class:`ReviewerTruth`. Never mutates the input."""
    ops_list = _coerce_ops(ops)
    h = _plate_height(result_dict)

    lanes = sorted(result_dict.get("lanes") or [], key=lambda ln: int(ln["index"]))
    labels = [str(ln.get("label", UNREADABLE)) for ln in lanes]
    streaking = [bool(_val(ln.get("is_streaking")) or False) for ln in lanes]

    bands: dict[str, TruthBand] = {}
    for b in result_dict.get("annotation_bands") or []:
        y0, y1 = _val(b.get("y0_frac")), _val(b.get("y1_frac"))
        if y0 is not None and y1 is not None:
            bands[str(b["kind"])] = TruthBand(kind=str(b["kind"]), y0_frac=y0, y1_frac=y1)

    ref = result_dict.get("reference") or {}
    origin = _px_to_frac(ref.get("origin_row_px"), h)
    front: float | Literal["absent"] | None
    if ref.get("front_provenance") == "absent":
        front = "absent"
    else:
        front = _px_to_frac(ref.get("front_row_px"), h)
    anchor = None
    if ref.get("rst_anchor"):
        anchor = _px_to_frac(ref["rst_anchor"].get("y_px"), h)

    machine = {m["spot_id"]: m for m in _machine_spots(result_dict)}
    # spot_id -> ("confirmed"|"moved", y_frac) | ("rejected", reason, note)
    decisions: dict[str, tuple[Any, ...]] = {}
    added: list[TruthSpot] = []
    plate_rejected = False
    plate_reason: str | None = None
    sample_id: str | None = None
    conditions: str | None = None

    for op in ops_list:
        if isinstance(op, OpSpotConfirm):
            if op.spot_id not in machine:
                raise ValueError(f"spot.confirm: unknown spot_id {op.spot_id!r}")
            decisions[op.spot_id] = ("confirmed", machine[op.spot_id]["y_frac"])
        elif isinstance(op, OpSpotMove):
            if op.spot_id not in machine:
                raise ValueError(f"spot.move: unknown spot_id {op.spot_id!r}")
            decisions[op.spot_id] = ("moved", op.y_frac)
        elif isinstance(op, OpSpotReject):
            if op.spot_id not in machine:
                raise ValueError(f"spot.reject: unknown spot_id {op.spot_id!r}")
            decisions[op.spot_id] = ("rejected", op.reason, op.note)
        elif isinstance(op, OpSpotAdd):
            added.append(
                TruthSpot(
                    lane_index=op.lane_index,
                    y_frac=op.y_frac,
                    strength=op.strength,
                    source="reviewer_added",
                )
            )
        elif isinstance(op, OpLaneRelabel):
            while len(labels) <= op.lane_index:
                labels.append(UNREADABLE)
                streaking.append(False)
            labels[op.lane_index] = op.label
        elif isinstance(op, OpLaneSetCount):
            labels = (labels + [UNREADABLE] * op.n_lanes)[: op.n_lanes]
            streaking = (streaking + [False] * op.n_lanes)[: op.n_lanes]
        elif isinstance(op, OpLaneStreak):
            while len(streaking) <= op.lane_index:
                labels.append(UNREADABLE)
                streaking.append(False)
            streaking[op.lane_index] = op.streaking
        elif isinstance(op, OpBandSet):
            bands[op.kind] = TruthBand(kind=op.kind, y0_frac=op.y0_frac, y1_frac=op.y1_frac)
        elif isinstance(op, OpOriginSet):
            origin = op.y_frac
        elif isinstance(op, OpFrontSet):
            front = "absent" if op.y_frac is None else op.y_frac
        elif isinstance(op, OpSampleId):
            sample_id, conditions = op.sample_id, op.conditions
        elif isinstance(op, OpPlateReject):
            plate_rejected, plate_reason = True, op.reason
        else:  # pragma: no cover - the union is closed
            raise TypeError(f"unhandled op {type(op).__name__}")

    n_lanes = len(labels)
    spots: list[TruthSpot] = []
    unreviewed: list[UnreviewedSpot] = []
    rejected: list[RejectedSpot] = []
    for sid, m in machine.items():
        d = decisions.get(sid)
        if d is None:
            unreviewed.append(
                UnreviewedSpot(
                    spot_id=sid,
                    lane_index=m["lane_index"],
                    y_frac=m["y_frac"],
                    machine_status=m["status"],
                )
            )
        elif d[0] == "rejected":
            rejected.append(
                RejectedSpot(
                    spot_id=sid,
                    lane_index=m["lane_index"],
                    y_frac=m["y_frac"],
                    reason=d[1],
                    note=d[2],
                )
            )
        else:
            spots.append(
                TruthSpot(
                    lane_index=m["lane_index"],
                    y_frac=float(d[1]),
                    strength="strong",
                    source="machine_confirmed" if d[0] == "confirmed" else "machine_moved",
                    spot_id=sid,
                )
            )
    spots.extend(added)

    # lanes removed by lane.set_count take their spots with them
    kept: list[TruthSpot] = []
    for s in spots:
        if s.lane_index < n_lanes:
            kept.append(s)
        else:
            rejected.append(
                RejectedSpot(
                    spot_id=s.spot_id or "reviewer_added",
                    lane_index=s.lane_index,
                    y_frac=s.y_frac,
                    reason="lane_removed",
                )
            )

    key = lambda s: (s.lane_index, s.y_frac, s.spot_id or "")  # noqa: E731
    return ReviewerTruth(
        n_lanes=n_lanes,
        lane_labels=labels,
        lane_streaking=streaking,
        bands=sorted(bands.values(), key=lambda b: b.kind),
        origin_y_frac=origin,
        front_y_frac=front,
        anchor_y_frac=anchor,
        spots=sorted(kept, key=key),
        unreviewed_machine_spots=sorted(unreviewed, key=key),
        rejected=sorted(rejected, key=key),
        plate_rejected=plate_rejected,
        plate_reject_reason=plate_reason,
        sample_id=sample_id,
        conditions=conditions,
        aided=not blind,
        correction_id=correction_id,
        reviewer_id=reviewer_id,
        review_seconds=review_seconds,
    )


def truth_from_doc(
    result_dict: Mapping[str, Any],
    doc: CorrectionDoc,
    *,
    correction_id: str | None = None,
    reviewer_id: str | None = None,
) -> ReviewerTruth:
    """Convenience: materialise a truth from a full :class:`CorrectionDoc`."""
    return apply_ops(
        result_dict,
        doc.ops,
        blind=doc.blind,
        correction_id=correction_id,
        reviewer_id=reviewer_id,
        review_seconds=doc.review_seconds,
    )
