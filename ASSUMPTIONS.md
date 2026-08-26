# Assumptions

Every place the build decided something the brief and specs did not specify, or where reality
deviated from what they describe. This is where the human reviewer looks first.

---

## A-001 · The prior evaluation bundle code is absent
The brief's repository contract (step 5) references `evaluation/` containing the prior evaluation
report "and its `bundle/` code" as the porting source for photometry, geometry, null tests and the
ensemble. No such folder or code exists anywhere on this machine. The evaluation *report* exists as
`research/TLC_method_evaluation.pdf`. Assumption: the report's described methods and parameters are
the authoritative porting source (see decisions.md D-002). Any parameter the report does not state
is chosen conservatively and logged here.

## A-002 · Folder naming deviates from the brief's tree
`tlc-spec-impl/` (hyphenated) is the brief's `tlc_spec_impl/`; the brief and specs live in
`TLC_build_brief/` rather than at the root. Paths in this repo's docs follow the on-disk names.

## A-003 · The corpus grew mid-session; the audit is a snapshot, not a constant
At session start `dataset/` held 12 files (7 unique plates — the prior evaluation's corpus). The
user added images during Phase 0 ("i have added more images to dataset for better working"):
36 more MEHQ-series images and an 18-image PER-P19 series. The audited corpus is 66 files /
61 unique images; `dataset/audit.json` records a `corpus_inventory_sha256` so any further
mutation is detectable, and `scripts/dataset_audit.py` re-derives everything from the directory.
§10's forced-stop condition (<15 usable for photometry) is **not triggered**: 48 unique images
are usable (35 clean, 13 partial). The audit's clipping measurements reproduce F1 on the original
seven plates (11–53% on six of seven; P33 clean), which validates the method and shows the added
images are genuinely better captured. 13 images remain positions-only; the capture-protocol
recommendations are in `reports/CAPTURE_PROTOCOL.md`.

## A-006 · Provisional usability thresholds in the Phase 0 audit
The audit classifies photometry usability by in-plate hard-clip fraction (G≥255): ≤5% ok,
≤15% partial (lane-dependent), >15% positions-only; plate detection requires ≥20% frame coverage.
These are Phase 0 *audit* labels chosen before reading spec 01; the shipped input gate is set in
Phase 4 against spec 01 and may differ. Lane count for px/lane is assumed 4 (S/co/R/sd per F5)
and tagged `inferred`. Tilt in the audit is a second-moment estimate, superseded by Phase 2
geometry.

## A-004 · No chemist is available during this autonomous session
Phase 6 requires a human chemist to label 30–50 plates and a second reviewer for the overlap
subset. This session is autonomous. Per §10, everything from Phase 7 onward that needs human labels
is built and validated against synthetic ground truth only, every accuracy field that requires
human labels is reported as *not computed*, and the correction UI (Phase 6's deliverable) is built
and tested so that labelling can start the moment a chemist sits down.

## A-005 · Git identity
Commits are authored as "Tejas Ghatule <tejas.ghatule@mstack.co>" (the repository owner's known
identity), set as local git config. No remote is configured; CI is a committed workflow definition
plus a locally runnable script, and "CI green" gates are evidenced by committed local runs.

## A-007 · Noise-unit estimator for Gate 1 (evaluation report leaves it unstated)
The eval report's plate-P33 "unmasked empty-band sd" is 0.01881 OD but never states the
estimator, region, or background model (UNSTATED #5 in reference/EVAL_REPORT_EXTRACT.md). Chosen
estimator (tlc/synth/stats.py): poly3 illumination surface fit on unclipped in-plate pixels;
empty band = the eighth of the plate's row extent with lowest p95(OD residual); sigma = plain sd
over that band (unmasked; MAD reported alongside). Under this estimator P33 reads 0.00964 —
inside the report's own radius-dependent sigma spread (0.0032–0.0116) but 0.51× its 0.01881
anchor, which is exactly the F4 circularity (sigma depends on the background model's stiffness).
Consequence: Gate 1's "residual noise sd within ±20% of the real value" is evaluated with THIS
estimator applied to both real and synthetic plates; the real-corpus target is
reports/corpus_stats.json (median 0.0077, range 0.003–0.025 over 61 unique images).

## A-008 · Synthetic scene model uses pure rotation for tilt
The PlateSpec table (spec 05 §12.2) parameterises geometry by tilt_deg 0–12 only. The generator
implements tilt as in-plane rotation with exact corner tracking (no perspective foreshortening).
Handheld perspective distortion exists in reality; Gate 2's rectification is projective and will
be additionally exercised with synthetic projective jitter when Phase 2 needs it. If Phase 2
requires generator-level perspective, it is added as a knob then (a decisions.md entry).

## A-009 · Determinism "two machines" is unavailable this build
Gate 2 (and spec 03 §7.2.2 tier 1) call for identical hashes across two machines. This build has
one machine (darwin/arm64). Evidence provided instead: two full in-process passes hash-identical
plus reviewer re-runs in fresh processes byte-comparing the evidence files. Cross-machine tier-1
verification is listed as not performed in EVALUATION.md; the committed GitHub Actions workflow
(ubuntu-x86_64) becomes the second architecture the moment CI runs remotely.

## A-010 · Rectification idempotency is evaluated at corners with recoverable evidence
For a plate cut off by the photo frame (F2: four of seven original plates), the ideal-rectangle
corners on the cut side lie outside the image; the rectified frame there contains clamped border
smear, and a second detection pass honestly measures a different, cut object. Idempotency
(re-warp is a no-op ≤ 0.5 px) is therefore checked at corners whose first-pass source position
is ≥ 3 px interior to the source frame; corners at the frame boundary are reported as
not-checkable with the reason, never silently passed. Plates fully inside the frame are checked
at all four corners. This is a scoping of what is measurable, not a loosening: on checkable
corners the 0.5 px bound stands.

## A-011 · Result-schema interpretations (spec 03 §7.3 ambiguities)
Resolved while freezing tlc/schemas/result.py (details in that module's docstring):
1. Float canonicalisation: probabilities are not structurally distinguishable, so any Q with
   unit "1" rounds to 4 dp; every other unit 6 dp; bare agreement/p-value floats 4 dp, other
   bare floats 6 dp. Purpose (byte-stable canonical JSON) is preserved.
2. The §7.3.6 worked example shows inferred quantities without `method`, which rule (b)
   forbids; the normative rule wins — producers always set method on inferred values.
3. `rf` is modeled `Q[float] | None`: omitted when the front is absent per the field table, but
   a refused-Q form (as the worked example shows) is also legal.
4. `Flag.evidence` admits list values (the example's per_lane_max); `Refusal.evidence` stays
   `dict[str, float | str]` exactly.
5. The Lane rule "`suppression` populated iff `quantified == false`" is enforced by validator.
6. Densitogram previews: producer rounds to 5 dp; schema serialises at 6 dp (identity).
7. Floats inside Any-typed payloads (VLM samples, config_document) are canonicalised by the
   producer, not the schema — unreachable structurally.
8. `created_at` is an ISO-8601 string (canonical-JSON stability), not a datetime object.

## A-010 amendment (Gate 2 review condition 2)
The idempotency result (worst 0.0 px) is exact BY CONSTRUCTION: `_snap_to_frame` (SNAP_PX=2.0,
shipped pipeline code) snaps corners within 2 px of the frame boundary onto it, and a rectified
plate fills its frame exactly, so second-pass corners snap to the ideal frame corners whenever
raw re-detection lands within 2 px. Raw pre-snap re-detection repeatability measured by the
independent reviewer is ~0.8–1.5 px (one-sided edge evidence biases the 50%-crossing estimator
inward at frame boundaries — the physical rationale for the snap). The gate's wording ("re-warp
is a no-op") is met exactly; first-pass corner accuracy is proven independently by the sweep
(p95 0.208 px with 21–43 px margins, snap out of play). Recorded so `worst_residual_px: 0.0`
is never read as a sub-pixel re-detection claim.

## A-009 amendment
"EVALUATION.md" is the Phase 12 deliverable (forward reference): when written, it lists
cross-machine tier-1 determinism as an open item until the committed CI workflow has run on a
second architecture (ubuntu-x86_64).

## A-012 · Background-model parameters the eval report leaves unstated
(UNSTATED #1-#4 in reference/EVAL_REPORT_EXTRACT.md.) Chosen, all `chosen` provenance:
Gaussian illumination blur sigma = radius/2; iterative-masked = box blur (kernel 2R+1), 3
iterations, mask rule `keep if g > bg - 0.5*mean|bg-g|` (ported constants from
tlc-spec-impl/normalize.py:146-152); median kernel = 2R+1; rolling ball in the ImageJ 8-bit
convention (M-007). Untrusted pixels (invalid/clipped) are excluded via normalized convolution
or pre-filled from the Gaussian estimate; they never inform a fit.

## A-013 · CONFIG_GRID_v1 is selected on synthetic dev plates (no human labels exist yet)
Spec 01 §2.3's grid pruning needs measured recall on adjudicated-true spots and FP on blank
plates. Pre-Gate-6 there are no adjudicated real spots, so the 576->24 selection runs on a
synthetic dev set (corpus-calibrated generator, Gate 1) plus synthetic/real-texture blanks.
Per §3.4 the grid selection is a fitted quantity: when real labels exist (Phase 6+), selection
is re-run inside the CV loop and the synthetic-selected grid is treated as version 1, never as
evidence of real-data performance. The synthetic dev seeds are disjoint from every seed used in
gate evidence elsewhere.

## A-014 · Analysable-band convention on real plates (pre-VLM)
Until the VLM/operator annotation bands exist (Phase 8/6), real-plate photometry evidence uses a
chosen convention band of rows 0.25h–0.80h (below the header ink, above the label row/origin on
every corpus plate). Provenance is `chosen`; gate3_check.py's P33 case uses it. Superseded per
plate by measured bands once the semantic layer runs.
