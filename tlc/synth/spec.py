"""PlateSpec and GroundTruth for the synthetic plate generator (spec 05 §12.2).

Built in Phase 1, BEFORE the pipeline, so the generator cannot be unconsciously shaped to
flatter the pipeline. All defaults are calibrated to reports/corpus_stats.json and
reference/EVAL_REPORT_EXTRACT.md §8 (per-plate statistics of the real corpus).

Coordinates: plate coordinates are (x right, y down); row 0 is the TOP of the plate (solvent
front end), the origin line sits near the bottom. GroundTruth positions are exact, in rectified
plate coordinates; corner positions are in output-image coordinates (may lie outside the frame
when frame overrun cuts the plate).
"""

from dataclasses import asdict, dataclass, field
from enum import Enum


class SpotShape(str, Enum):
    GAUSSIAN = "gaussian"
    EMG = "emg"
    STREAK = "streak"
    HALO = "halo"


class Overrun(str, Enum):
    NONE = "none"
    TOP = "top"
    BOTTOM = "bottom"
    BOTH = "both"


class Handwriting(str, Enum):
    NONE = "none"
    HEADER = "header"
    HEADER_LABELS = "header_labels"


@dataclass(frozen=True)
class SpotSpec:
    lane: int                    # 0-based lane index
    y_frac: float                # 0 = origin row, 1 = front row (Rst-like position)
    amplitude_sigma: float       # peak OD in units of the analytic noise sigma (1-30)
    shape: SpotShape = SpotShape.GAUSSIAN
    width_frac: float = 0.18     # sigma_x as a fraction of lane pitch
    aspect: float = 1.0          # sigma_y / sigma_x
    tau_frac: float = 1.5        # EMG tail constant as a multiple of sigma_y (EMG only)
    x_offset_frac: float = 0.0   # lane-centre offset as a fraction of lane pitch


@dataclass(frozen=True)
class PlateSpec:
    # Geometry (real corpus: 71x130 ... 158x299; spec extends to 400x760 "good capture")
    plate_w: int = 120
    plate_h: int = 200
    n_lanes: int = 4
    tilt_deg: float = 2.0                  # 0-12
    frame_overrun: Overrun = Overrun.NONE
    margin_frac: float = 0.06              # dark background margin around the plate

    # Illumination (corpus_stats: swing 0.095-0.244 median 0.147; base 0.78-0.95 median 0.91)
    base_green: float = 0.91               # normalised green of the plate's bright level
    illum_swing: float = 0.15              # (max-min)/max of the true illumination surface
    hotspot_strength: float = 0.5          # fraction of the swing carried by the Gaussian hotspot

    # Noise (corpus_stats: empty-band sd_od 0.003-0.025 median 0.0077 under the A-007 estimator)
    noise_sd: float = 0.016                # per-pixel sd in normalised intensity units
    noise_corr_px: float = 0.7             # correlation length (0.4-1.0)

    # Exposure / clipping (corpus: 0-0.54 observed; knob must reproduce 0.14-0.60)
    clip_fraction: float = 0.0             # target fraction of in-plate green pixels at 255

    # Content
    spots: tuple[SpotSpec, ...] = field(default_factory=tuple)
    empty_lanes: tuple[int, ...] = ()
    origin_dots: bool = True
    front_line: bool = False               # absent in the whole real corpus
    handwriting: Handwriting = Handwriting.HEADER_LABELS
    header_frac: float = 0.16              # header band height as fraction of plate height
    origin_row_frac: float = 0.86          # origin row as fraction of plate height
    front_row_frac: float = 0.20           # front row as fraction of plate height (drawn or not)

    # Colour model (corpus_stats medians: R/G 0.288, B/G 0.770; background = measured teal
    # bench, median RGB (13, 96, 115), G range 71-124 — Gate 1 reviewer flag #5)
    red_over_green: float = 0.29
    blue_over_green: float = 0.77
    background_rgb: tuple[float, float, float] = (13.0, 96.0, 115.0)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["frame_overrun"] = self.frame_overrun.value
        d["handwriting"] = self.handwriting.value
        d["spots"] = [{**asdict(s), "shape": s.shape.value} for s in self.spots]
        return d


@dataclass(frozen=True)
class SpotTruth:
    lane: int
    x: float                     # exact centre, plate coords (px)
    y: float                     # EMG/Gaussian mu (shape parameter)
    y_mode: float                # darkest row of the rendered spot at its column (D-014 position)
    shape: str
    amplitude_od: float          # exact peak OD
    amplitude_sigma: float       # peak OD / analytic sigma_od
    sigma_x: float
    sigma_y: float
    tau: float                   # 0 for non-EMG
    area_od: float               # exact integral of this spot's OD contribution (OD*px^2)
    quantifiable: bool           # False for streaks: their "position" is not well defined


@dataclass(frozen=True)
class GroundTruth:
    # Scene geometry
    corners_xy: tuple[tuple[float, float], ...]   # TL, TR, BR, BL in output-image coords
    plate_w: int
    plate_h: int
    tilt_deg: float
    image_w: int
    image_h: int

    # Structure (plate coords)
    lane_centres_x: tuple[float, ...]
    lane_pitch: float
    origin_row: float
    front_row: float | None                        # None when no drawn front line
    header_band: tuple[int, int]                   # (row0, row1) or (0, 0)
    label_band: tuple[int, int]
    origin_dot_xy: tuple[tuple[float, float], ...]

    # Content truth
    spots: tuple[SpotTruth, ...]
    empty_lanes: tuple[int, ...]
    streak_lanes: tuple[int, ...]

    # Photometric truth
    sigma_od_analytic: float                       # noise_sd / (base_green * ln 10)
    noise_sd: float
    illum_swing_true: float                        # measured on the true surface
    base_green: float
    exposure_scale: float
    clip_fraction_actual: float                    # measured on the emitted image, in-plate

    def to_dict(self) -> dict:
        d = asdict(self)
        d["spots"] = [asdict(s) for s in self.spots]
        return d
