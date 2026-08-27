"""Reaction reasoning across the four lanes of one plate — the read a chemist performs by eye.

The lab's format (TLC_Analysis.pdf p2-3) is four lanes: **S** starting material, **co** the co-spot
where starting material and reaction mixture are run together, **R** the reaction mixture, **sd** the
authentic product standard. A chemist reads that plate by asking, in order:

    where does the starting material run?          -> the S lane gives the anchor
    where does the product run?                    -> the sd lane gives the anchor
    does this reaction mixture shift things?       -> the co lane says, because it holds both
    what is each band in R, then?                  -> assign against the shifted anchors
    how much is left, how much is made?            -> shares within the R lane only
    is anything here that the reaction did not make? -> impurities carried in from the SM

The seven rules implemented here are the ones in the project's own design document
(TLC_System_Design_Visual.pdf p7), restated:

    R1 matrix shift        measured two independent ways; the ways must agree, or the shift is
                           taken as zero with a widened tolerance
    R2 reference anchoring only confirmed reference bands anchor anything, never the origin zone
    R3 impurity inheritance a band present in S (or co) and absent from sd came in with the starting
                           material — the action is to purify the SM, not to change the route
    R4 co-spot decomposition co ~= alpha*S + beta*R, fitted on the traces; R^2 is the plate's own
                           self-consistency score
    R5 conflict resolution a position matching two anchors is decided on intensity consistency
    R6 streak guard        a streaking lane reports zones, never percentages
    R7 apparent conversion product : SM **within one lane**, never across lanes, with the UV caveat

Everything the module reports is either a measured number with an interval, an inference with its
basis named, or a refusal with a reason. Nothing here is a probability: confidence is an ordinal
grade built from named factors (design doc p9), and a low grade makes the system ask rather than
assert.
"""

from dataclasses import asdict, dataclass, field

import numpy as np

from tlc.insight import variables as V

# --- thresholds, all traceable ------------------------------------------------------------------
RST_MATCH_TOL = 0.05          # spec 02 H01: ~2x the worst observed cross-method disagreement
ORIGIN_ZONE_RST = 0.08        # spec 02 H04: below this is the application point, never an anchor
ANCHOR_MIN_SNR = 3.0          # design doc R2: anchors only from confirmed >= 3 sigma bands
SHIFT_AGREE_TOL = 0.03        # R1: the two shift estimates must agree within this, in Rst units
SHIFT_MAX = 0.25              # a "shift" beyond this is a different band, not a matrix effect
STREAK_LANE_FRACTION = 0.55   # R6, the ported constant
COSPOT_R2_GOOD = 0.70         # R4: below this the plate does not explain itself
CONVERSION_MIN_OD = 0.05      # spec 02 H15: below this the band is inside the noise
CONVERSION_MAX_OD = 1.00      # above this UV quenching saturates and areas stop being linear


@dataclass(frozen=True)
class Value:
    """A number that carries where it came from. `interval` is None only when the underlying
    quantity has none; `refusal` replaces the value entirely when it cannot be had."""

    value: float | int | str | bool | None
    unit: str
    provenance: str                      # measured | inferred | chosen | refused
    basis: str | None = None
    interval: tuple[float, float] | None = None
    refusal: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["interval"] is not None:
            d["interval"] = [round(float(x), 4) for x in d["interval"]]
        if isinstance(d["value"], float):
            d["value"] = round(d["value"], 4)
        return d


def refused(unit: str, code: str, message: str, remedy: str) -> Value:
    return Value(None, unit, "refused", refusal={"code": code, "message": message, "remedy": remedy})


@dataclass(frozen=True)
class Assignment:
    band_id: str
    rst: float | None
    identity: str                 # starting_material | product | impurity | origin_residue | unassigned
    label: str                    # what a non-chemist should read
    basis: str
    confidence: str               # high | medium | low
    factors: list[str]
    share_of_lane: Value
    inherited: bool | None = None
    agreement: float | None = None
    n_hit: int | None = None
    n_total: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["share_of_lane"] = self.share_of_lane.to_dict()
        if isinstance(d["rst"], float):
            d["rst"] = round(d["rst"], 4)
        return d


@dataclass(frozen=True)
class ReactionReport:
    verdict: str                  # complete | in_progress | no_reaction_detected | cannot_conclude
    headline: str
    plain_summary: list[str]      # for someone who has never read a TLC plate
    chemist_summary: list[str]
    confidence: dict
    anchors: dict
    matrix_shift: dict
    cospot: dict
    assignments: list[Assignment]
    quantities: dict
    impurities: list[dict]
    caveats: list[str]
    what_would_change_this: list[str]
    next_experiment: str | None
    refusals: list[dict] = field(default_factory=list)
    glossary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict, "headline": self.headline, "plain_summary": self.plain_summary,
            "chemist_summary": self.chemist_summary, "confidence": self.confidence, "anchors": self.anchors,
            "matrix_shift": self.matrix_shift, "cospot": self.cospot,
            "assignments": [a.to_dict() for a in self.assignments], "quantities": self.quantities,
            "impurities": self.impurities, "caveats": self.caveats,
            "what_would_change_this": self.what_would_change_this, "next_experiment": self.next_experiment,
            "refusals": self.refusals, "glossary": self.glossary,
        }


GLOSSARY = {
    "band": "A dark line in a lane. Each one is a different compound (or a group of compounds that "
            "happen to travel together).",
    "Rst": "How far a band travelled, as a fraction of how far the product standard travelled on the "
           "same plate. 1.00 means it stopped exactly where the standard did; 0.50 means it went half "
           "as far. It is a ratio measured on this plate, so it is comparable within this plate only.",
    "lane": "One vertical track on the plate. This plate has four: the starting material (S), the "
            "co-spot (co), the reaction (R), and the product standard (sd).",
    "co-spot": "A lane where the starting material and the reaction mixture were spotted on top of "
               "each other. It is the control that tells you whether the reaction mixture itself "
               "changes how far things travel — and whether two bands that look identical really are.",
    "share of lane": "How much of that lane's total darkness belongs to this band. It is a share of "
                     "signal, not a share of moles: two compounds do not absorb UV equally.",
}


# --- helpers ------------------------------------------------------------------------------------
def _principal(bands: list[V.Band]) -> V.Band | None:
    """The band that anchors a reference lane: the strongest one that is confirmed, outside the
    origin zone, and above the anchoring SNR (R2)."""
    usable = [b for b in bands
              if b.status == "confirmed" and (b.snr or 0) >= ANCHOR_MIN_SNR
              and not b.in_annotation_band and (b.rst is None or b.rst >= ORIGIN_ZONE_RST)]
    if not usable:
        return None
    return max(usable, key=lambda b: (b.area_od or b.peak_od or 0.0, b.agree or 0.0))


def _intervals_overlap(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool | None:
    if not a or not b:
        return None
    return a[0] <= b[1] and b[0] <= a[1]


def _xcorr_shift(ref: np.ndarray, obs: np.ndarray, max_lag: int) -> tuple[float | None, float]:
    """Whole-curve alignment: the lag that best lines `obs` up with `ref`, and how peaked that
    optimum is. Both traces are mean-removed and unit-scaled first so amplitude cannot masquerade
    as agreement."""
    if ref.size < 16 or obs.size < 16:
        return None, 0.0
    n = min(ref.size, obs.size)
    a = np.asarray(ref[:n], float)
    b = np.asarray(obs[:n], float)
    a = a - a.mean()
    b = b - b.mean()
    if a.std() < 1e-12 or b.std() < 1e-12:
        return None, 0.0
    a /= a.std()
    b /= b.std()
    lags = np.arange(-max_lag, max_lag + 1)
    scores = []
    for lag in lags:
        if lag < 0:
            s = float(np.dot(a[-lag:], b[: n + lag]) / (n + lag))
        elif lag > 0:
            s = float(np.dot(a[: n - lag], b[lag:]) / (n - lag))
        else:
            s = float(np.dot(a, b) / n)
        scores.append(s)
    scores = np.asarray(scores)
    best = int(np.argmax(scores))
    peak = float(scores[best])
    # quality: how much better the optimum is than the typical lag
    quality = float(peak - np.median(scores))
    return float(lags[best]), quality


def _nnls2(design: np.ndarray, target: np.ndarray) -> tuple[float, float, float]:
    """Two-component non-negative least squares (R4). Returns (alpha, beta, r2). Small enough to do
    by projection with a clamp — scipy.optimize.nnls would work too but this keeps the pure-numpy
    dependency surface of the insight layer."""
    a = np.asarray(design, float)
    y = np.asarray(target, float)
    if a.shape[0] != y.shape[0] or a.shape[0] < 8:
        return 0.0, 0.0, 0.0
    coef, *_ = np.linalg.lstsq(a, y, rcond=None)
    coef = np.clip(coef, 0.0, None)
    # one refit on the surviving components keeps a clamped fit honest
    active = coef > 0
    if active.any() and not active.all():
        sub, *_ = np.linalg.lstsq(a[:, active], y, rcond=None)
        coef = np.zeros(2)
        coef[active] = np.clip(sub, 0.0, None)
    pred = a @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 0.0 if ss_tot <= 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return float(coef[0]), float(coef[1]), r2


def _grade(factors: dict[str, bool]) -> tuple[str, list[str]]:
    """Verdict confidence is the weakest link in its evidence chain (design doc p9). Returns the
    grade and the named factors, because a grade without its factors is a score, and scores are
    what this system refuses to produce."""
    named = [name for name, ok in factors.items() if not ok]
    n_bad = len(named)
    grade = "high" if n_bad == 0 else ("medium" if n_bad == 1 else "low")
    return grade, [name for name in factors] if not named else named


# --- the analysis --------------------------------------------------------------------------------
def analyse_reaction(result: dict, profiles: dict[int, np.ndarray] | None = None,
                     meta: dict | None = None) -> ReactionReport:
    """Read one plate the way a chemist reads it. `profiles` are the authoritative per-lane traces
    from the run's HDF5 file; without them the whole-curve rules (R1b, R4) refuse rather than guess."""
    pv = V.extract(result, meta or {})
    refusals: list[dict] = []
    caveats: list[str] = []

    S = pv.bands_in("S")
    R = pv.bands_in("R")
    co = pv.bands_in("co")
    sd = pv.bands_in("sd")
    roles_present = {r for r in pv.lane_roles}
    height = float(result["geometry"]["rectified_shape"][0] or 1)

    def lane_index_of(role: str) -> int | None:
        for i, r in enumerate(pv.lane_roles):
            if r == role:
                return i
        return None

    # ---- R2: anchors ---------------------------------------------------------------------------
    sm_anchor = _principal(S)
    product_anchor = _principal(sd)
    anchors = {
        "starting_material": (None if sm_anchor is None else
                              {"band_id": sm_anchor.id, "rst": round(sm_anchor.rst, 4) if sm_anchor.rst else None,
                               "interval": list(sm_anchor.rst_ci) if sm_anchor.rst_ci else None,
                               "snr": sm_anchor.snr, "agreement": sm_anchor.agree, "lane": "S"}),
        "product": (None if product_anchor is None else
                    {"band_id": product_anchor.id, "rst": round(product_anchor.rst, 4) if product_anchor.rst else None,
                     "interval": list(product_anchor.rst_ci) if product_anchor.rst_ci else None,
                     "snr": product_anchor.snr, "agreement": product_anchor.agree, "lane": "sd"}),
        "rule": "R2: only confirmed reference bands above 3 sigma and outside the origin zone anchor anything",
    }
    if sm_anchor is None:
        refusals.append({"code": "E_NO_SM_ANCHOR",
                         "message": "The starting-material lane has no band strong enough to anchor a comparison.",
                         "remedy": "Spot the starting material in its own lane, labelled S, at a load you can see."})
    if product_anchor is None:
        refusals.append({"code": "E_NO_PRODUCT_ANCHOR",
                         "message": "The product-standard lane has no band strong enough to anchor a comparison.",
                         "remedy": "Run an authentic sample of the expected product in its own lane, labelled sd."})

    # ---- R6: streak guard ----------------------------------------------------------------------
    r_index = lane_index_of("R")
    r_lane = next((L for L in result["lanes"] if L["index"] == r_index), None) if r_index is not None else None
    r_streaking = bool(r_lane and (r_lane.get("is_streaking") or {}).get("value"))
    r_quantified = bool(r_lane and r_lane.get("quantified"))
    if r_streaking:
        caveats.append("The reaction lane is streaking, so shares of the lane are withheld: a smear has no "
                       "single position or amount. Likely causes are overloading, an acidic or basic compound "
                       "on unbuffered silica, or decomposition on the plate.")

    # ---- R1: matrix shift, measured two ways ---------------------------------------------------
    shift = _matrix_shift(pv, S, co, sm_anchor, profiles, lane_index_of, height)
    matrix_delta = shift["applied"]["value"] or 0.0

    # ---- R4: co-spot decomposition -------------------------------------------------------------
    cospot = _cospot_decomposition(profiles, lane_index_of, co, R, sd)

    # ---- assignment of every R band ------------------------------------------------------------
    assignments, impurities, quant = _assign(
        R, S, co, sm_anchor, product_anchor, matrix_delta, shift, r_streaking, r_quantified, pv,
    )

    # ---- verdict -------------------------------------------------------------------------------
    verdict, headline, factors = _verdict(assignments, sm_anchor, product_anchor, R, quant, r_streaking)
    grade, named = _grade(factors)
    if grade == "low":
        headline = headline.rstrip(".") + " — but the evidence for this is weak; treat it as a question, not an answer."

    plain, chem = _narrate(verdict, assignments, quant, shift, cospot, sm_anchor, product_anchor,
                           impurities, r_streaking, roles_present, grade)

    caveats.extend([
        "Position tells you two compounds could be the same; it never proves they are. Two different "
        "compounds running to the same place in one solvent system is ordinary.",
        "Shares are shares of UV darkness, not of moles: this plate cannot know how strongly each "
        "compound absorbs. Treat them as a rank order and a rough size, not a yield.",
    ])
    if not cospot.get("available"):
        caveats.append("The co-spot decomposition did not run, so the plate has not checked itself for "
                       "internal consistency.")

    return ReactionReport(
        verdict=verdict, headline=headline, plain_summary=plain, chemist_summary=chem,
        confidence={"grade": grade, "factors": named,
                    "rule": "the verdict is only as strong as the weakest link in its evidence chain"},
        anchors=anchors, matrix_shift=shift, cospot=cospot, assignments=assignments,
        quantities=quant, impurities=impurities, caveats=caveats,
        what_would_change_this=_falsifiers(verdict, assignments, quant),
        next_experiment=_next_experiment(verdict, assignments, impurities, quant, cospot),
        refusals=refusals, glossary=GLOSSARY,
    )


def _matrix_shift(pv, S, co, sm_anchor, profiles, lane_index_of, height) -> dict:
    """R1. Estimate how far the reaction matrix moves a band, two independent ways, and require the
    two to agree. Disagreement is not averaged away — the shift is set to zero and the tolerance for
    everything downstream is widened, which is the conservative direction."""
    out: dict = {"rule": "R1: two independent estimates that must agree, else no shift and a wider tolerance",
                 "anchor_estimate": None, "curve_estimate": None, "agree": None,
                 "applied": Value(None, "Rst", "refused").to_dict(), "tolerance": RST_MATCH_TOL}
    if sm_anchor is None or sm_anchor.rst is None or not co:
        why = ("the starting-material lane has no band strong enough to anchor it" if sm_anchor is None
               else "the co-spot lane has no band that clears the reporting gate")
        out["applied"] = Value(0.0, "Rst", "chosen",
                               basis=f"no shift could be measured because {why}").to_dict()
        out["tolerance"] = RST_MATCH_TOL * 1.5
        return out

    # (a) anchor-based: the SM band inside the co lane against the SM band in its own lane
    cand = [b for b in co if b.rst is not None and abs(b.rst - sm_anchor.rst) <= SHIFT_MAX]
    anchor_delta = None
    if cand:
        best = min(cand, key=lambda b: abs(b.rst - sm_anchor.rst))
        anchor_delta = float(best.rst - sm_anchor.rst)
        out["anchor_estimate"] = {"delta_rst": round(anchor_delta, 4), "co_band": best.id,
                                  "s_band": sm_anchor.id}

    # (b) whole-curve: align the co trace against the S trace
    curve_delta = None
    if profiles:
        i_s, i_co = lane_index_of("S"), lane_index_of("co")
        if i_s in profiles and i_co in profiles:
            max_lag = int(0.1 * height)
            lag, quality = _xcorr_shift(profiles[i_s], profiles[i_co], max_lag)
            if lag is not None and quality > 0.05:
                # a lag in rows becomes an Rst delta through the same scale the bands use
                scale = _rst_per_row(pv, height)
                if scale:
                    curve_delta = float(lag * scale)
                    out["curve_estimate"] = {"delta_rst": round(curve_delta, 4), "lag_rows": lag,
                                             "alignment_quality": round(quality, 3)}

    if anchor_delta is not None and curve_delta is not None:
        agree = abs(anchor_delta - curve_delta) <= SHIFT_AGREE_TOL
        out["agree"] = bool(agree)
        if agree:
            applied = 0.5 * (anchor_delta + curve_delta)
            out["applied"] = Value(applied, "Rst", "measured",
                                   basis="mean of two agreeing estimates: the co-lane anchor and whole-curve alignment",
                                   interval=(min(anchor_delta, curve_delta), max(anchor_delta, curve_delta))).to_dict()
        else:
            out["applied"] = Value(0.0, "Rst", "chosen",
                                   basis="the two estimates disagreed, so no shift is applied and the matching "
                                         "tolerance is widened instead").to_dict()
            out["tolerance"] = RST_MATCH_TOL * 2.0
    elif anchor_delta is not None:
        out["agree"] = None
        out["applied"] = Value(anchor_delta, "Rst", "measured",
                               basis="co-lane anchor only; the whole-curve check could not run").to_dict()
        out["tolerance"] = RST_MATCH_TOL * 1.5
    else:
        out["applied"] = Value(0.0, "Rst", "chosen",
                               basis="neither estimate was available, so no shift is applied").to_dict()
        out["tolerance"] = RST_MATCH_TOL * 1.5
    return out


def _rst_per_row(pv, height: float) -> float | None:
    """How much Rst one row is worth on this plate — read off the bands that carry both numbers."""
    pts = [(b.y_frac * height, b.rst) for b in pv.bands if b.rst is not None and b.y_frac is not None]
    if len(pts) < 2:
        return None
    pts.sort()
    (y0, r0), (y1, r1) = pts[0], pts[-1]
    if abs(y1 - y0) < 1e-6:
        return None
    return float((r1 - r0) / (y1 - y0))


def _cospot_decomposition(profiles, lane_index_of, co, R, sd) -> dict:
    """R4. The co lane should be a mixture of what the S lane and the R lane hold. Fit it as
    alpha*S + beta*R and report R^2 as the plate's self-consistency score."""
    out = {"rule": "R4: co ~= alpha*S + beta*R fitted on the lane traces; R^2 is the plate's self-consistency",
           "available": False, "reason": None}
    if not profiles:
        out["reason"] = "the authoritative lane traces were not supplied"
        return out
    i_s, i_co, i_r = lane_index_of("S"), lane_index_of("co"), lane_index_of("R")
    if any(i is None or i not in profiles for i in (i_s, i_co, i_r)):
        out["reason"] = "this plate does not carry all three of the S, co and R lanes"
        return out
    s, c, r = (np.asarray(profiles[i], float) for i in (i_s, i_co, i_r))
    n = min(s.size, c.size, r.size)
    design = np.column_stack([s[:n], r[:n]])
    alpha, beta, r2 = _nnls2(design, c[:n])
    out.update({
        "available": True,
        "alpha_S": round(alpha, 3), "beta_R": round(beta, 3), "r_squared": round(r2, 3),
        "self_consistent": bool(r2 >= COSPOT_R2_GOOD),
        "reading": ("The co-spot lane is well explained as a mixture of the starting-material lane and the "
                    "reaction lane, which is what it should be — the plate is internally consistent."
                    if r2 >= COSPOT_R2_GOOD else
                    "The co-spot lane is NOT well explained as a mixture of the starting-material and reaction "
                    "lanes. Something differs between the lanes beyond loading: check that the co lane really "
                    "holds both materials, and that no lane is streaking or clipped."),
    })
    return out


def _assign(R, S, co, sm_anchor, product_anchor, delta, shift, streaking, quantified, pv):
    """Assign every band in the reaction lane, then work out how much of the lane each holds."""
    tol = float(shift.get("tolerance") or RST_MATCH_TOL)
    sm_rst = None if sm_anchor is None or sm_anchor.rst is None else sm_anchor.rst + delta
    pr_rst = None if product_anchor is None or product_anchor.rst is None else product_anchor.rst + delta

    areas = {b.id: (b.area_od or 0.0) for b in R}
    total_area = sum(v for v in areas.values() if v > 0)

    def share(b: V.Band) -> Value:
        if streaking:
            return refused("frac", "E_STREAK_NO_SHARES",
                           "The lane is streaking, so no band in it gets a share of the signal.",
                           "Re-run at a lower load, or with 1% acetic acid in the eluent.")
        if not quantified or b.area_od is None or total_area <= 0:
            return refused("frac", "E_AREA_WITHHELD",
                           "Areas are withheld in this lane, so shares cannot be computed.",
                           "Re-shoot the plate one or two stops darker.")
        return Value(b.area_od / total_area, "frac", "measured",
                     basis="this band's fitted area over the total fitted area of the lane")

    assignments: list[Assignment] = []
    impurities: list[dict] = []
    for b in R:
        d_sm = None if sm_rst is None or b.rst is None else abs(b.rst - sm_rst)
        d_pr = None if pr_rst is None or b.rst is None else abs(b.rst - pr_rst)
        near_sm = d_sm is not None and d_sm <= tol
        near_pr = d_pr is not None and d_pr <= tol
        at_origin = b.rst is not None and b.rst < ORIGIN_ZONE_RST

        factors = {
            "the band is confirmed by the ensemble, not a candidate": b.status == "confirmed",
            "the band is strong enough to trust its position": (b.snr or 0) >= ANCHOR_MIN_SNR,
            "the matrix-shift estimates agreed": shift.get("agree") is not False,
            "the band is clear of the handwriting band": not b.in_annotation_band,
        }
        if near_sm and near_pr:
            # R5: one position, two anchors -> decide on intensity consistency
            identity, label, basis = _resolve_conflict(b, sm_anchor, product_anchor, d_sm, d_pr)
            factors["the two candidate identities were separable"] = False
        elif near_sm:
            identity, label = "starting_material", "unreacted starting material"
            basis = (f"sits {d_sm:.3f} Rst from where the starting material runs "
                     f"(tolerance {tol:.3f}, matrix shift {delta:+.3f})")
        elif near_pr:
            identity, label = "product", "the expected product"
            basis = (f"sits {d_pr:.3f} Rst from where the product standard runs "
                     f"(tolerance {tol:.3f}, matrix shift {delta:+.3f})")
        elif at_origin:
            identity, label = "origin_residue", "material left at the application point"
            basis = "did not move from the origin: very polar material, salts, or simply too much loaded"
        else:
            identity = "impurity"
            above = pr_rst is not None and b.rst is not None and b.rst > pr_rst + tol
            label = "an impurity that runs above the product" if above else "an impurity"
            basis = "matches neither the starting material nor the product"

        inherited = None
        if identity == "impurity" and b.rst is not None:
            in_s = any(x.rst is not None and abs(x.rst - b.rst) <= tol for x in S)
            in_co = any(x.rst is not None and abs(x.rst - (b.rst - delta)) <= tol for x in co)
            inherited = bool(in_s or in_co)   # R3
            impurities.append({
                "band_id": b.id, "rst": None if b.rst is None else round(b.rst, 4),
                "inherited_from_starting_material": inherited,
                "reading": ("This impurity is already present in the starting material, so the reaction did not "
                            "make it. The fix is to purify the starting material, not to change the route."
                            if inherited else
                            "This impurity is not visible in the starting material, so it appeared during the "
                            "reaction or the work-up."),
                "rule": "R3: present in S or co and absent from sd means it came in with the starting material",
            })

        grade, named = _grade(factors)
        assignments.append(Assignment(
            band_id=b.id, rst=b.rst, identity=identity, label=label, basis=basis,
            confidence=grade, factors=named, share_of_lane=share(b), inherited=inherited,
            agreement=b.agree, n_hit=None, n_total=None,
        ))

    quant = _quantities(assignments, R, streaking, quantified)
    return assignments, impurities, quant


def _resolve_conflict(b, sm_anchor, product_anchor, d_sm, d_pr):
    """R5. A band whose position matches both anchors is decided on intensity consistency: whichever
    reference it resembles in strength, with distance as the tie-break."""
    b_amp = b.peak_od or b.area_od or 0.0
    sm_amp = (sm_anchor.peak_od or sm_anchor.area_od or 0.0) if sm_anchor else 0.0
    pr_amp = (product_anchor.peak_od or product_anchor.area_od or 0.0) if product_anchor else 0.0
    if b_amp > 0 and sm_amp > 0 and pr_amp > 0:
        if abs(b_amp - pr_amp) < abs(b_amp - sm_amp):
            return ("product", "the expected product",
                    f"position matches both references ({d_sm:.3f} vs {d_pr:.3f} Rst); its strength is closer to "
                    "the product standard's, so it is read as product (R5)")
        return ("starting_material", "unreacted starting material",
                f"position matches both references ({d_sm:.3f} vs {d_pr:.3f} Rst); its strength is closer to the "
                "starting material's, so it is read as starting material (R5)")
    nearer = "product" if (d_pr or 9) < (d_sm or 9) else "starting_material"
    return (nearer, "the expected product" if nearer == "product" else "unreacted starting material",
            "position matches both references and no intensity comparison was possible, so the nearer one was "
            "taken; treat this band as ambiguous (R5)")


def _quantities(assignments, R, streaking, quantified) -> dict:
    """R7. Product against starting material, inside the reaction lane only."""
    sm = [a for a in assignments if a.identity == "starting_material"]
    pr = [a for a in assignments if a.identity == "product"]
    imp = [a for a in assignments if a.identity in ("impurity", "origin_residue")]

    def total(group) -> float | None:
        vals = [a.share_of_lane.value for a in group if a.share_of_lane.value is not None]
        return float(sum(vals)) if vals else None

    sm_share, pr_share, imp_share = total(sm), total(pr), total(imp)
    out = {
        "rule": "R7: apparent conversion is product against starting material within the reaction lane only",
        "starting_material_share": (Value(sm_share, "frac", "measured", basis="sum of the shares assigned to SM")
                                    if sm_share is not None else
                                    refused("frac", "E_NO_SHARES", "No share could be computed for starting material.",
                                            "Re-shoot darker, or re-run the lane at a lower load.")).to_dict(),
        "product_share": (Value(pr_share, "frac", "measured", basis="sum of the shares assigned to the product")
                          if pr_share is not None else
                          refused("frac", "E_NO_SHARES", "No share could be computed for the product.",
                                  "Re-shoot darker, or re-run the lane at a lower load.")).to_dict(),
        "impurity_share": (Value(imp_share, "frac", "measured", basis="sum of every other band in the lane")
                           if imp_share is not None else
                           refused("frac", "E_NO_SHARES", "No share could be computed for the other bands.",
                                   "Re-shoot darker.")).to_dict(),
    }
    if streaking:
        out["apparent_conversion"] = refused(
            "frac", "E_STREAK_NO_CONVERSION",
            "The reaction lane is streaking, so no conversion figure is reported: a smear cannot be divided "
            "into a starting material and a product.",
            "Re-run at a lower load or with a modified eluent, then re-photograph.").to_dict()
        return out
    if not quantified:
        out["apparent_conversion"] = refused(
            "frac", "E_CONVERSION_UNQUANTIFIED",
            "Areas are withheld in the reaction lane, so no conversion figure is reported.",
            "Re-shoot the plate one to two stops darker and compare.").to_dict()
        return out
    if sm_share is None and pr_share is None:
        out["apparent_conversion"] = refused(
            "frac", "E_NO_SM_OR_PRODUCT",
            "Neither a starting-material band nor a product band was assigned, so there is nothing to divide.",
            "Check the lane labels, and that the S and sd lanes carry visible bands.").to_dict()
        return out
    p = pr_share or 0.0
    s = sm_share or 0.0
    if p + s <= 0:
        out["apparent_conversion"] = refused(
            "frac", "E_NO_SIGNAL", "The starting material and product bands carry no measurable area.",
            "Load more material, or re-shoot with a longer exposure that does not clip.").to_dict()
        return out
    od_ok = all((b.peak_od is None or CONVERSION_MIN_OD <= b.peak_od <= CONVERSION_MAX_OD)
                for b in R if b.status == "confirmed")
    out["apparent_conversion"] = Value(
        p / (p + s), "frac", "inferred",
        basis="product share / (product share + starting-material share), within this lane only",
    ).to_dict()
    out["linear_range_ok"] = od_ok
    if not od_ok:
        out["linear_range_note"] = ("At least one band sits outside the range where UV darkness tracks amount "
                                    "(0.05-1.00 OD), so the conversion figure is a rank indicator, not a number "
                                    "to quote.")
    return out


def _verdict(assignments, sm_anchor, product_anchor, R, quant, streaking):
    has_sm = any(a.identity == "starting_material" for a in assignments)
    has_pr = any(a.identity == "product" for a in assignments)
    factors = {
        "a starting-material reference band was found": sm_anchor is not None,
        "a product standard band was found": product_anchor is not None,
        "the reaction lane could be read": bool(R) or not streaking,
        "the reaction lane is not streaking": not streaking,
    }
    if sm_anchor is None or product_anchor is None:
        return ("cannot_conclude",
                "This plate cannot be read as a reaction: a reference lane is missing or too weak to anchor it.",
                factors)
    if has_pr and not has_sm:
        return ("complete",
                "No starting material is left in the reaction lane, and the product is there.", factors)
    if has_pr and has_sm:
        return ("in_progress",
                "The reaction is part-way: both starting material and product are in the reaction lane.", factors)
    if has_sm and not has_pr:
        return ("no_reaction_detected",
                "The reaction lane still shows starting material and no band where the product runs.", factors)
    return ("cannot_conclude",
            "The reaction lane holds no band matching either reference, so this plate does not answer the question.",
            factors)


def _narrate(verdict, assignments, quant, shift, cospot, sm_anchor, product_anchor, impurities,
             streaking, roles_present, grade) -> tuple[list[str], list[str]]:
    """Two voices over the same facts: one for someone who has never read a plate, one for a chemist."""
    plain: list[str] = []
    chem: list[str] = []

    def pct(d) -> str | None:
        v = (d or {}).get("value")
        return None if v is None else f"{100 * float(v):.0f}%"

    conv = pct(quant.get("apparent_conversion"))

    # --- plain -----------------------------------------------------------------------------------
    plain.append(
        "A TLC plate is a race. Four samples start at the same line and a solvent carries them up. "
        "How far each one climbs depends on what it is, so a compound always stops in the same place "
        "on the same plate — and that is how you tell one from another."
    )
    plain.append(
        "The four lanes here are: the material you started with (S), the material you started with "
        "mixed with the reaction (co), the reaction itself (R), and a pure sample of what you are "
        "trying to make (sd). Reading the plate means asking which of the bands in the reaction lane "
        "line up with the starting material, which line up with the product, and what is left over."
    )
    if verdict == "complete":
        plain.append("**The answer: the reaction looks finished.** In the reaction lane there is a band where "
                     "the product standard runs, and nothing where the starting material runs — so the "
                     "starting material has been used up, at least as far as this plate can see.")
    elif verdict == "in_progress":
        line = ("**The answer: the reaction is under way but not finished.** The reaction lane holds a band "
                "where the starting material runs and a band where the product runs, so some has converted "
                "and some has not.")
        if conv:
            line += f" Of the darkness in those two bands, about **{conv}** belongs to the product."
        plain.append(line)
    elif verdict == "no_reaction_detected":
        plain.append("**The answer: nothing seems to have converted yet.** The reaction lane shows the starting "
                     "material and no band where the product standard runs. That means either the reaction has "
                     "not started, or any product present is below what this photograph can show.")
    else:
        plain.append("**The answer: this plate cannot tell you.** The reference lanes that anchor the comparison "
                     "are missing or too faint, so nothing in the reaction lane can be identified with confidence.")

    imp = [a for a in assignments if a.identity == "impurity"]
    if imp:
        inherited = [i for i in impurities if i.get("inherited_from_starting_material")]
        made = [i for i in impurities if not i.get("inherited_from_starting_material")]
        bits = []
        if made:
            bits.append(f"{len(made)} that {'is' if len(made) == 1 else 'are'} not in the starting material, so "
                        f"{'it' if len(made) == 1 else 'they'} appeared during the reaction")
        if inherited:
            bits.append(f"{len(inherited)} that {'was' if len(inherited) == 1 else 'were'} already in the starting "
                        f"material, so the reaction did not make {'it' if len(inherited) == 1 else 'them'}")
        plain.append(f"There {'is' if len(imp) == 1 else 'are'} also **{len(imp)} other band"
                     f"{'' if len(imp) == 1 else 's'}** in the reaction lane: " + "; ".join(bits) + ".")
    if streaking:
        plain.append("One caution: the reaction lane is smeared rather than showing clean bands, so no percentages "
                     "are given for it. A smear usually means too much material was loaded, or the compound is "
                     "sticking to the plate.")
    plain.append("Finally, the honest limit: this plate says two things *travel the same distance*. That is strong "
                 "evidence they are the same compound, and it is not proof — a different compound can travel the "
                 "same distance. Confirmation needs a second solvent system, or NMR or mass spec.")

    # --- chemist ---------------------------------------------------------------------------------
    if sm_anchor and sm_anchor.rst is not None:
        chem.append(f"SM anchor: {sm_anchor.id} in the S lane at Rst {sm_anchor.rst:.3f} "
                    f"(SNR {sm_anchor.snr:.0f}, agreement {sm_anchor.agree:.2f}).")
    if product_anchor and product_anchor.rst is not None:
        chem.append(f"Product anchor: {product_anchor.id} in the sd lane at Rst {product_anchor.rst:.3f} "
                    f"(SNR {product_anchor.snr:.0f}, agreement {product_anchor.agree:.2f}).")
    ms = shift.get("applied", {})
    if ms.get("value") is not None:
        chem.append(f"Matrix shift applied: {float(ms['value']):+.3f} Rst — {ms.get('basis')}. "
                    f"Matching tolerance {shift.get('tolerance'):.3f} Rst.")
    if cospot.get("available"):
        chem.append(f"Co-spot decomposition: co ≈ {cospot['alpha_S']}·S + {cospot['beta_R']}·R, "
                    f"R² = {cospot['r_squared']} — {'self-consistent' if cospot['self_consistent'] else 'NOT self-consistent'}.")
    for a in assignments:
        share = a.share_of_lane.value
        share_txt = "" if share is None else f", {100 * share:.0f}% of the lane"
        chem.append(f"{a.band_id}: Rst {a.rst:.3f}{share_txt} → **{a.label}** ({a.confidence} confidence) — {a.basis}."
                    if a.rst is not None else f"{a.band_id}: → {a.label} — {a.basis}.")
    if conv:
        chem.append(f"Apparent conversion (product / (product + SM), this lane only): {conv}"
                    + ("" if quant.get("linear_range_ok", True) else " — outside the linear UV range, rank only."))
    elif quant.get("apparent_conversion", {}).get("refusal"):
        chem.append("Apparent conversion: " + quant["apparent_conversion"]["refusal"]["message"])
    if grade != "high":
        chem.append(f"Confidence in the overall call: {grade}. The named weaknesses are listed with the verdict.")
    return plain, chem


def _falsifiers(verdict, assignments, quant) -> list[str]:
    out = ["Re-run the same samples in a second, orthogonal solvent system. Bands that are genuinely the same "
           "compound stay together; bands that only happened to coincide separate."]
    if verdict == "complete":
        out.append("Load the reaction lane two to three times more heavily and re-photograph. If starting material "
                   "appears, it was simply below the detection limit rather than absent.")
    if verdict == "no_reaction_detected":
        out.append("Check that the product standard lane is loaded at a comparable amount. A faint standard makes "
                   "a real product band look like nothing.")
    if any(a.identity == "impurity" for a in assignments):
        out.append("Re-spot the starting material alone at higher load. An impurity that appears there was carried "
                   "in, not created.")
    if quant.get("apparent_conversion", {}).get("value") is not None:
        out.append("A single HPLC or NMR point on the same aliquot either calibrates this conversion figure or "
                   "falsifies it; TLC densitometry under handheld UV is a rank-order indicator.")
    return out


def _next_experiment(verdict, assignments, impurities, quant, cospot) -> str | None:
    if verdict == "cannot_conclude":
        return "Re-run the plate with a visible starting-material lane and an authentic product standard, loaded "\
               "at comparable amounts, and photograph it without clipping."
    if verdict == "no_reaction_detected":
        return "Sample again after a longer interval, and confirm the product standard is loaded heavily enough "\
               "to be seen at the same exposure."
    if verdict == "in_progress":
        return "Take the next timepoint. Two plates in the same campaign at the same loading turn this snapshot "\
               "into a trend; six make that trend testable."
    if any(not i.get("inherited_from_starting_material") for i in impurities):
        return "Quench and take a plate at half the reaction time: an impurity that grows with the product is on "\
               "the reaction path, one that does not is a side reaction."
    return "Quench and work up; confirm the product identity by NMR or mass spec before quoting a yield."
