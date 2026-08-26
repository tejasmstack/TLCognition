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
