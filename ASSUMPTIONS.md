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
