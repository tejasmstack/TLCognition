"""Correction document models — §7.7.2 of ``specs/03_backend.md``, verbatim.

All positions are ``y_frac`` of the RECTIFIED plate (``y_px / H``); never source
pixels. Models are pydantic v2 with ``extra="forbid"`` so an unknown key or an
unknown ``op`` is rejected at parse time.
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

RejectReason = Literal["artefact", "handwriting", "background", "duplicate", "edge", "other"]
Strength = Literal["strong", "faint", "trace"]
BandKind = Literal["header", "label_row", "footer"]
PlateRejectReason = Literal["not_a_plate", "unreadable", "wrong_experiment", "other"]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpSpotConfirm(_Op):
    op: Literal["spot.confirm"]
    spot_id: str


class OpSpotReject(_Op):
    op: Literal["spot.reject"]
    spot_id: str
    reason: RejectReason
    note: str | None = None


class OpSpotMove(_Op):
    op: Literal["spot.move"]
    spot_id: str
    y_frac: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class OpSpotAdd(_Op):
    op: Literal["spot.add"]
    lane_index: int = Field(ge=0)
    y_frac: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    strength: Strength
    note: str | None = None


class OpLaneRelabel(_Op):
    op: Literal["lane.relabel"]
    lane_index: int = Field(ge=0)
    label: str


class OpLaneSetCount(_Op):
    op: Literal["lane.set_count"]
    n_lanes: int = Field(ge=0)
    x_centers_frac: list[float] | None = None


class OpLaneStreak(_Op):
    op: Literal["lane.mark_streaking"]
    lane_index: int = Field(ge=0)
    streaking: bool


class OpBandSet(_Op):
    op: Literal["band.set"]
    kind: BandKind
    y0_frac: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    y1_frac: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class OpOriginSet(_Op):
    op: Literal["origin.set"]
    y_frac: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class OpFrontSet(_Op):
    op: Literal["front.set"]
    y_frac: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)
    """``None`` means the reviewer CONFIRMED the front is absent (F2)."""


class OpSampleId(_Op):
    op: Literal["sample.set_id"]
    sample_id: str
    conditions: str | None = None


class OpPlateReject(_Op):
    op: Literal["plate.reject"]
    reason: PlateRejectReason
    note: str | None = None


CorrectionOp = Annotated[
    Union[  # noqa: UP007 - explicit Union keeps the discriminator readable
        OpSpotConfirm,
        OpSpotReject,
        OpSpotMove,
        OpSpotAdd,
        OpLaneRelabel,
        OpLaneSetCount,
        OpLaneStreak,
        OpBandSet,
        OpOriginSet,
        OpFrontSet,
        OpSampleId,
        OpPlateReject,
    ],
    Field(discriminator="op"),
]

#: Parse a single op from a plain dict: ``CorrectionOpAdapter.validate_python({...})``.
CorrectionOpAdapter: TypeAdapter[CorrectionOp] = TypeAdapter(CorrectionOp)


class CorrectionDoc(BaseModel):
    """The body of ``POST /api/v1/runs/{run_id}/corrections``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    viewed_result_sha256: str
    blind: bool = False
    ops: list[CorrectionOp]
    review_seconds: int | None = Field(default=None, ge=0)
    reviewer_comment: str | None = None
