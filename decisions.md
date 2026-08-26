# Decisions log

Append-only. One entry per non-obvious choice, written when the decision is made.
Format: D-NNN · title / Date / Status / Context / Options / Decision / Why / Consequences / Revisit if.

---

## D-001 · Repository root is `/Users/tejas.ghatule/Documents`
**Date:** 2026-08-26
**Status:** accepted
**Context:** The brief (§ START HERE) prescribes a repo tree with `BUILD_BRIEF.md` at the root
alongside `research/`, `dataset/`, `tlc_spec_impl/`, `evaluation/`. On disk, the brief and specs
live in `TLC_build_brief/`, and the working directory contains *only* project material
(`TLC_build_brief/`, `dataset/`, `research/`, `tlc-spec-impl/`).
**Options considered:**
  1. `git init` in the working directory as-is — matches the actual material layout; brief sits
     one level down in `TLC_build_brief/`.
  2. Create a fresh subdirectory repo and copy/symlink materials in — matches the brief's tree
     exactly but duplicates the dataset and breaks the paths the user already has.
  3. Move `TLC_build_brief/*` up to the root to match the brief's tree exactly.
**Decision:** Option 1. The repo root is the working directory; the brief and specs stay in
`TLC_build_brief/`. All references to `specs/` resolve to `TLC_build_brief/specs/`.
**Why:** zero data movement, zero duplication, and the user's existing paths keep working. The
brief's tree is a convention, not a functional requirement.
**Consequences:** paths in docs must say `TLC_build_brief/specs/...`. Prior implementation folder
is `tlc-spec-impl/` (hyphenated), not `tlc_spec_impl/` as the brief writes it.
**Revisit if:** never; cosmetic.

## D-002 · The prior evaluation `bundle/` code does not exist — port from the report instead
**Date:** 2026-08-26
**Status:** accepted (forced by reality)
**Context:** Repository contract step 5 says the reference implementations for photometry,
geometry, the null tests and the ensemble are in the prior evaluation's `bundle/` code and MUST be
ported, not rewritten. A full-tree search finds no `evaluation/` folder and no `bundle/` anywhere.
What exists is the evaluation *report*, `research/TLC_method_evaluation.pdf`, and the separate
prior app `tlc-spec-impl/`.
**Options considered:**
  1. Stop and ask for the bundle — blocks the whole build on a file that may simply not have been
     exported to this machine.
  2. Transcribe the algorithms and parameters from the evaluation report PDF, treating the report's
     stated methods/parameters as the porting source, and validate them against the report's own
     numbers where reproducible cheaply.
**Decision:** Option 2, with the deviation recorded here and in `ASSUMPTIONS.md` (A-001).
**Why:** the report documents the methods and their measured behaviour (the brief's §3 findings are
its conclusions). Transcribing from the report preserves the intent of "port, don't rewrite":
implement the *same* algorithms at the *same* operating points, not new ones.
**Consequences:** porting fidelity is limited by what the report states. Any parameter the report
does not state becomes an entry in `ASSUMPTIONS.md`, chosen conservatively and marked `chosen`,
not `measured`.
**Revisit if:** the bundle code turns up — then diff the transcription against it.

## D-003 · Stack pins per spec 03 §7.1 (normative, not re-opened)
**Date:** 2026-08-26
**Status:** accepted
**Context:** `specs/03_backend.md` §7.1 is normative and fully decides the stack.
**Decision:** Python 3.12.x (pinned patch) via uv + committed `uv.lock`; FastAPI ≥0.115 +
Pydantic v2 (frozen, extra="forbid" result models); sync endpoints; ProcessPoolExecutor worker over
a SQLite job table; SQLite 3.45+ WAL via SQLAlchemy 2.0 + Alembic; content-addressed local blob
store; HDF5 via h5py; imageio.v3 + Pillow decode (cv2 forbidden as decoder); Typer CLI; structlog;
pytest + hypothesis, xdist off.
**Why:** spec §7.1.1 states each trade-off in one line; the document closes the argument.
**Consequences:** repo layout follows spec §7.1.2 (`tlc/` package at root, `holdout/` separate).
**Revisit if:** a pinned wheel is unavailable for a required package on this machine (record the
substitution here if so).
## D-004 · Dataset audit method and the §10 forced-stop outcome
**Date:** 2026-08-26
**Status:** accepted
**Context:** Gate 0 requires a full-corpus capture audit. At session start the corpus was 12
files (7 unique — guaranteeing the §10 "<15 usable" forced stop); the user added images
mid-session, bringing it to 66 files / 61 unique (43 MEHQ + 18 PER).
**Options considered:**
  1. Audit-grade segmentation (Otsu on green, largest component, hole fill) now; pipeline-grade
     geometry later in Phase 2.
  2. Build Phase 2 geometry first and audit with it — better numbers, but inverts the brief's
     ordering ("you cannot design gates without knowing what fraction of the corpus can pass").
**Decision:** Option 1. `scripts/dataset_audit.py` is deterministic, re-runnable, and stamps a
`corpus_inventory_sha256` so corpus mutations are detectable. Verdicts use provisional
thresholds (ASSUMPTIONS A-006).
**Why:** the audit's job is triage, not measurement. Its clipping numbers reproduce F1 on the
original seven plates (11–53% on six of seven, P33 clean), which validates the method.
**Consequences:** 48 of 61 unique images usable for photometry → **the §10 forced stop is NOT
triggered**. 13 images are positions-only; `reports/CAPTURE_PROTOCOL.md` records the
capture-protocol remedies. The audit is a snapshot; re-run after any corpus change.
**Revisit if:** Phase 2 geometry contradicts audit verdicts on >10% of images.

## D-005 · Prior implementation `tlc-spec-impl/`: keep / port / discard = MIXED
**Date:** 2026-08-26
**Status:** accepted
**Context:** Repository contract step 4. An independent survey agent read all ~1,500 lines and
judged them against NN1–NN5 and F1–F15 (full report preserved; salvage list with file:line refs).
The code's refusal philosophy is strong (reason+remedy catalogue, measured/needed gate checks),
but it violates F1 (59%-clipped plate publishes photometry, clipping never gates), F4 (σ measured
post-masking and re-estimated per lane), F11 (symmetric Gaussian, not EMG), F2 (no Rst anywhere;
`to_rf` silently returns three different quantities), F10 (lane count from signal), NN2 (no
value tags), NN5 (nothing pinned; params_hash omits live constants), and carries two NN1 hazards
(history-dependent confidence hysteresis; a hardcoded "VLM cache" with identical fabricated
labels/confidences for all 7 plates).
**Decision:**
  - **PORT** (into the new pure, pinned, tested core): iterative-masked background + log-OD
    (normalize.py:143–164) with σ relocated pre-masking (F4) and sat_frac measured pre-warp;
    dark-border trim (normalize.py:38–70); plate localisation (normalize.py:72–103); all of
    signals.py (cv2.blur → numpy); gutter-median pencil-line detection (structure.py:196–236);
    fill-ratio dot/glyph discrimination (structure.py:111–193); measured header band
    (structure.py:59–89); pitch completion AS A PROPOSAL GENERATOR only (F10); streak fraction +
    quantification suppression (extract.py:102–105); analytic lane noise plate_σ/√ncols promoted
    from floor to THE lane σ (extract.py:93–101); cross-lane stacking, gutter hygiene, Rs pairs,
    width-vs-position model (extract.py:166–217); fit framework with EMG replacing Gaussian and
    tagged fallbacks (extract.py:106–123); lexicon role ladder minus the fake cache
    (lanes.py:36–69); cannot_conclude reason+remedy catalogue and Check shape (pipeline.py:191–214,
    gate.py:18–42); Factor(name,value,reason) confidence pattern WITHOUT hysteresis
    (confidence.py:14–49); trace section schema as a pure value (trace.py:18–26).
  - **KEEP as reference/fixtures:** the 7 runs/ outputs (golden regression baseline), README's
    deviations list, render.py's presentation decisions (band highlights, pixel-faithful graph).
  - **DISCARD:** pipeline.py orchestration (impure, no F1 gate, no NN2), lanes.py VLM_LABEL_CACHE
    (NN1 poison), confidence.py hysteresis branch, render.py recompute-from-image path,
    params_hash as-is (replaced by hashed frozen config).
**Why:** the algorithms encode hard-won plate-specific findings (signed-OD MAD trap, border-trim
trap, gutter medians); the architecture around them fails the non-negotiables. Porting algorithms
into a compliant skeleton costs less than re-deriving them and less than retrofitting compliance.
**Consequences:** golden-output tests against runs/ JSONs are written before porting begins
(converts the README's unverifiable determinism claim into a regression gate).
**Revisit if:** golden tests show the described algorithms don't reproduce runs/ outputs.

## D-006 · Gate 1 reviewer flags: what changes now, what becomes a convention
**Date:** 2026-08-26
**Status:** accepted
**Context:** the independent Gate 1 review passed the gate and raised six flags outside its
coverage (verbatim in GATES.md).
**Decision:**
  1. Flag 5 (background contrast): PlateSpec default background_rgb corrected from a guessed
     near-black (14,24,30) to the measured corpus teal (13,96,115). Plate detection on synthetics
     now faces realistic contrast. Gate 1 statistics are unaffected (all in-plate, green).
  2. Flag 2: random_spec swing floor raised 0.10→0.11 to match the gate sweep.
  3. Flag 3 (EMG centre semantics) becomes a build-wide CONVENTION: a spot's position is the EMG
     mu (Gaussian-component centre), in ground truth and in every pipeline fit and error metric.
     The rendered intensity peak of an EMG sits ~O(tau) toward the origin; mixing conventions
     would create a systematic offset on tailing spots. Recorded here so Phase 3/5 inherit it.
  4. Flags 1 (estimator-relative noise target) and 6 (clip knob delivers ~0.02-0.04 low on the
     emitted image; effective emitted range 0.12-0.56) are accepted and documented — A-007
     already states the estimator-relative nature; the clip knob's purpose (span the observed
     regime) is met.
  5. Flag 4 (no spot hue signal in synthetics): accepted; the pipeline measures green only (F1
     context). If colour-ratio logic is ever proposed, synthetics cannot validate it — that
     limitation is recorded here.
**Consequences:** gate1 evidence regenerated at the new defaults (criteria unchanged, artifacts
re-hashed); committed in the same change.
**Revisit if:** Phase 2 plate detection behaves differently on real vs synthetic in a way traced
to residual background/edge differences (e.g. cut corners, tape).

## D-007 · Background model for the primary estimate
**Date:** 2026-08-26
**Status:** accepted
**Context:** F5: polynomial and iterative-masked agree to 0.3 px (0.1 px per the eval report
§2); polynomial has 10 printable coefficients, iterative has kernel + iterations + a masking
rule; rolling-ball sits 3.3 px off the cluster; Kubelka-Munk diverges in exactly the faint-zone
regime (9 spots vs 2). The brief's own worked example (§5.1) reaches the same conclusion.
**Options considered:**
  1. Polynomial surface, order 3 — fewest parameters, all printable in an SOP.
  2. Iterative masked box-blur — avoids spots biasing their own background (ported from
     tlc-spec-impl normalize.py:143-164 per D-005).
  3. Rolling ball — familiar to reviewers, but 3.3 px off the cluster (F5).
**Decision:** poly3 as the primary; iterative, Gaussian, median, rolling-ball retained as
ensemble members (they are the Phase 4 grid's background axis). Kubelka-Munk not implemented
anywhere (F5: wrong regime; anti-pattern list).
**Why:** validation cost — ten coefficients go in a method document; a three-stage masking
procedure cannot be checked by a QA reviewer. Ensemble disagreement surfaces the cases where
poly3 is worse under strong local gradients.
**Revisit if:** blank-plate FP rate for poly3 exceeds the iterative variant's by >20% (Phase 4).

## D-008 · The noise unit: background-free difference-based sigma, measured once, pre-masking
**Date:** 2026-08-26
**Status:** accepted
**Context:** F4: sigma measured on a post-subtraction residual inherits the background radius
(3.6x spread) and spot-masking loosens every threshold 1.68x. The eval never states its
estimator (UNSTATED #5). The prior impl's 70th-percentile trim is itself weak masking.
**Options considered:**
  1. sd/MAD over a chosen "blank band" — band choice depends on spot content: weak masking.
  2. MAD over the poly3 residual — inherits the primary model (radius-free but model-bound).
  3. Robust first-difference estimator on raw log-intensity:
     sigma_od = 1.4826 * median(|d log10(g)|) / sqrt(2) over valid unclipped pixel pairs in the
     analysable band — no background model, no masking, no tuning radius, measured once on the
     raw band before anything else sees the image.
**Decision:** option 3 (`sigma_method: "mad_diff_1.4826_prespot"`). Every ensemble member and
every threshold consumes this one number. `sigma_stability_across_radii` is still computed (the
same estimator applied to each member's OD residual) and gated at <=0.15 as the tripwire that
nobody reintroduces a radius-dependent unit.
**Why:** the only estimator in the option list whose value cannot move when a tuning parameter
moves (F4's demand made structural). Known property: first differences read correlated noise
lower than per-pixel sd by sqrt(1-rho1) (~20% at the corpus texture); the unit is consistent
system-wide, and detection thresholds are calibrated in this unit against the null battery
(Phase 4), so the scale convention cancels where it matters.
**Revisit if:** Gate 4 cannot meet FP<=0.2/plate and recall>=0.95 simultaneously and the
operating-point analysis traces the failure to the noise unit's scale convention.

## D-009 · Phase 4 follows spec 01's ensemble design, not the eval's literal 32-grid
**Date:** 2026-08-26
**Status:** accepted
**Context:** The brief's Phase 4 heading says "the 32-pipeline agreement ensemble" (the grid the
prior evaluation ran: 4 radii x 4 models x 2 thresholds). Spec 01 §2.3 — binding on "the
ensemble design" per the brief's own reading table — shows that grid's failure mode (near-
duplicate configs; at rho=0.8, K_eff = 1.24 of 32) and prescribes: 5 decorrelated axes (radius
8/16/32/64 log-spaced; background models morphological-opening/rolling-ball, running-median,
arPLS lambda=1e5, 2-D Legendre order 3; sigma-variant; densitogram extraction mean/median/
trimmed-20; peak model EMG/bi-Gaussian/raw-max) -> 576 run offline -> prune by measured
performance -> greedy max-min diversity to K=24 (CONFIG_GRID_v1, hashed) with K_eff and config
weights reported.
**Decision:** implement spec 01's design. The brief wins only on NN1-5/F1-15 conflicts; there is
none here — F6's substance (ensemble agreement as the core evidence, no saturation assumption)
is preserved and strengthened. Threshold policy likewise per spec 01 §2.2: per-plate empirical
surrogate nulls (S1 gutter transplant, S2 IAAFT, S4 synthetic blanks incl. real-texture) with
Davison-Hinkley MC p-values and BH q=0.10 — never a bare "3-sigma rule". The noise unit is
spec 01 §2.1's sigma0 + full-autocovariance matched-filter z (radius-free, fixed 6 px high-pass);
D-008's difference-based estimator remains as the Gate 3 radius-independence tripwire.
**Why:** an agreement fraction over near-duplicates measures redundancy, not robustness; the
spec's K_eff >= 4 CI gate (G10) makes that failure detectable instead of latent.
**Consequences:** more implementation surface (arPLS, Legendre, opening, IAAFT, biweight
midcovariance); Gate 4's numeric criteria unchanged (FP <= 0.2/plate on >= 200 blanks AND
recall >= 0.95 at >= 5 sigma, simultaneously).
**Revisit if:** K_eff on the selected 24 falls below 4 on the dev set (G10), or the 576-sweep
compute becomes prohibitive on this hardware (then shrink axes, recorded here).

## D-010 · The operative per-plate null is S1 (gutter transplant); S2 (IAAFT) is a diagnostic
**Date:** 2026-08-26
**Status:** accepted
**Context:** spec 01 §2.2 lists S1 and S2 as "the primary null (100 each)". Measured on
synthetic dev plates: IAAFT preserves the lane profile's power spectrum and amplitude
distribution, so on a lane carrying a strong spot the surrogates inherit that spot's spectral
power — a real 6-sigma spot next to a 10-sigma spot drew p_mc = 0.51 against a mixed S1+S2
null (its z=9.3 vs null p90 = 11.3), which would fail Gate 4's recall arm outright. On blank
lanes S1 and S2 agree (no signal to inherit).
**Decision:** p_mc = FWER-style Davison-Hinkley p against per-surrogate MAX z over S1 nulls
(spec's own FWER construction). S2 runs at n/5 samples and its exceedance fraction is recorded
per peak (`s2_exceed`) as a calibration feature and diagnostic — never the operative null.
Three further null-fidelity fixes found empirically and applied: (a) S1 transplants contiguous
gutter STRIPS with one flip+roll per strip (independent per-column rolls destroy cross-column
correlation and made the null anti-conservatively light: an empty-lane z=1.4 bump drew
p=0.010); (b) S1 segments are taken and rolled WITHIN the analysable band (rolling whole
columns rotated header/label ink into the chemistry zone and manufactured phantom null peaks);
(c) candidates and null peaks are both screened to positive z and positive amplitude
(negative-going maxima inflated BH's m and shrank MC denominators — anti-conservative).
**Why:** the null must be signal-free by construction and texture-true; S1 is both, S2 is
neither on spotted lanes. FP control is still verified end-to-end by the S4 battery (Gate 4's
own metric), which is independent of this choice.
**Revisit if:** the Gate 4 battery shows the S1 null under-controls FP on real-texture blanks
(then S2 returns to the operative mix on blank-classified lanes).

## D-011 · K_eff for grid health (G10) is computed on blank-plate detection vectors
**Date:** 2026-08-26
**Status:** accepted
**Context:** spec 01 §2.3 defines K_eff from the mean pairwise phi-correlation of configs'
binary detection vectors "over all candidate positions". Measured on CONFIG_GRID_v1: 1.93 over
all dev bins, 10.68 over blank-plate bins only. The all-bins value is dominated by configs
agreeing on TRUE spots (14 spotted dev plates with 12-25 sigma spots every good config finds) —
perfect detectors would score K_eff = 1 by that formula, which cannot be the intended "grid
health" reading.
**Decision:** both values are recorded in the artifact; G10 (K_eff >= 4) is evaluated on the
blank-plate (null) detection vectors — the correlation of configs' MISTAKES, which is the
redundancy §2.3 warns about ("agreement then measures redundancy rather than robustness").
Config weights w_c stay as specified (from the full vectors).
**Why:** agreement suppresses phantoms only if configs' phantoms are decorrelated; that is
what the null-vector K_eff measures directly. Selected grid: 4 models, 4 radii, 3 sigma
variants (unmasked_mad did not survive diversity selection), 3 extractions, 3 peak models;
218 configs excluded by measured performance (156 for FP > 1.0/blank — all rolling-ball
families and median@32/64; 78 for recall < 0.5 — the R=8 families).
**Revisit if:** the Gate 4 battery shows phantom agreement clustering (many blank-plate
ensemble spots with a > 0.5) — that would mean the null K_eff overstates independence.

## D-012 · Gate 4 attempt 2: matching tolerance, observability, and grid pruning threshold
**Date:** 2026-08-26
**Status:** accepted
**Context:** attempt 1 failed (M-009). Three parameters had been chosen without spec anchoring.
**Decision:**
  1. Truth matching for recall uses spec 05 §12.6's evaluation tolerance |dRst| <= 0.03,
     implemented in px as max(0.4 x nominal FWHM, 0.03 x (origin_row - front_row)); the 0.4 FWHM
     figure stays as the ENSEMBLE clustering tolerance (spec 01 §2.3), which is a different job.
  2. A true spot whose peak box (+/-2 sigma_y, lane window) is >= 50% source-clipped is
     "unobservable": reported in a separate count, never a miss and never a hit. F1 says
     clipped pixels carry no information; a recall metric that penalises the detector for
     obeying F1 measures the wrong thing.
  3. Grid pruning MIN_RECALL raised 0.5 -> 0.7 on the synthetic dev set, per spec 01 §2.5
     item 2: configs "systematically blind to real spots ... are also measured-bad and should
     exit". Greedy diversity had been selecting weak-but-different members whose weights capped
     achievable agreement on faint true spots (median a = 0.68 for true 5-6 sigma spots).
**Why:** none of the three loosens the gate's numbers (FP <= 0.2, recall >= 0.95 at >= 5 sigma);
they fix what is being counted.
**Revisit if:** the re-selected grid's null-bin K_eff falls below 4 (G10).

## D-013 · Gate 4: §10 forced stop invoked; interim operating point proposed, not chosen
**Date:** 2026-08-26
**Status:** ACCEPTED AS INTERIM — option 1 chosen by the repository owner on 2026-08-26; the
adversarial review then refuted the diagnosis the options rested on (M-010): the forced stop was
premature and attempt 3 proceeds. OPERATING_POINT_v1 stays in force until attempt 3 reports;
its thresholds are on the pre-D-015 agreement scale (0.6 ≈ 70% weighted hit fraction, 0.4 ≈ 30%).
**Context:** two genuine attempts (M-009, D-012) leave no operating point meeting FP ≤ 0.2/blank
and recall ≥ 0.95 at ≥ 5σ simultaneously. The failure is confined to the real-texture null:
on synthetic noise both arms are met within a few percent of recall at a ≥ 0.5.
**Options for the human:**
  1. Interim two-tier point (recommended): `spots_reported` a ≥ 0.6 (pooled FP 0.31; 0.05 on
     noise; recall 0.88), `spots_candidate` a ≥ 0.4 (recall 0.96). Honest FP claim: "≤ 0.1 on
     synthetic noise; real-plate phantom rate not measured". Build continues to Phase 5 with
     confidence refused (E_UNCALIBRATED) as already mandated.
  2. Precision-first: a ≥ 0.7 (pooled FP 0.16 meets the gate; recall 0.74 — a quarter of
     5σ spots missed).
  3. Recall-first: a ≥ 0.5 (recall 0.94; pooled FP 0.54; 0.08 on noise).
  4. Stop the build until 20 real solvent-only blank plates are photographed under
     CAPTURE_PROTOCOL — the experiment that actually resolves the question.
**What is traded in option 1:** on real plates, up to ~0.3 phantom candidates per plate may
reach the reported list if the real-texture structure is truly phantom; 12% of faint (5σ)
spots land in the candidate list rather than the reported list.
**Revisit if:** real blank plates exist (then the battery re-runs with a real null and this
entry is superseded).

## D-014 · Spot position convention: the MODE (darkest row), superseding D-006's mu
**Date:** 2026-08-26
**Status:** accepted
**Context:** D-006 fixed position = EMG mu in truth and fit. Measured in Phase 5: on a synthetic
EMG spot (sigma 5.4, tau 8.1) the poly3 background absorbs part of the tail (F5: spots pull the
estimate under themselves), the observed profile is more symmetric than the rendered truth, and
the fitted mu lands 3.2 px (0.025 Rst) from the true mu — over Gate 5's 0.01 bound, for a
model-mismatch reason no fitter can remove.
**Options considered:**
  1. Keep mu; accept the bias or try to "de-absorb" the tail (model-dependent, fragile).
  2. Position = the mode (argmax of the spot's profile), sub-pixel refined, in BOTH ground
     truth (argmax of the rendered spot contribution at its lane column) and the fit (argmax of
     the fitted curve). mu, sigma, tau remain shape descriptors and feed the streak statistic.
**Decision:** option 2. It is what a chemist measures with a ruler, it is robust to tail
absorption (the mode moves far less than mu), and it needs no deconvolution assumption.
**Consequences:** GroundTruth gains `y_mode`; Gate 5's position error is mode-vs-mode; Rst is
computed from modes. mu is still reported (emg_sigma_px/emg_tau_px stay as shape).
**Revisit if:** the labelled set (Phase 6) shows chemists mark tailing spots systematically away
from the darkest row.

## D-015 · Agreement scale: config weights sum to K, not 1 (Jeffreys shrinkage as intended)
**Date:** 2026-08-26
**Status:** accepted
**Context:** spec 01 §2.3: a = (Σ w_c 1[hit] + α)/(Σ w_c + α + β), α=β=0.5 — MILD shrinkage
with Σw ≈ K. config_weights() normalised Σw = 1, so a = (hit_w + 0.5)/2 ∈ [0.25, 0.75]:
a = 0.5 meant 50% weighted agreement, 0.7 meant 90%, and 0.8 was unattainable (reviewer).
**Decision:** weights are scaled to Σw = K; a now spans ~[0.02, 0.98] and reads as a weighted
hit fraction. Operating-point grids are re-tuned on that scale; D-013's interim point is
restated in the new scale once attempt 3 reports.
**Why:** logit(a) is a calibration feature (x1); a compressed scale throws away resolution at
both ends, exactly what §2.5 warns costs "resolution at the top".

## D-016 · VIF-based plate abstention (spec 01 §6.2 row 14) suspended pending calibration of the estimator
**Date:** 2026-08-26
**Status:** accepted
**Context:** the plate-level VIF (spec 01 §2.1: correlated/white matched-filter noise) as
implemented — profile autocovariance summed over cross-column lags to 24 px on a fixed-scale
high-pass — reads > 6 on ~80% of SYNTHETIC plates, which contain no structured artefacts by
construction, and on 49/61 real plates. Spec §2.1 expects VIF ~1.5-2.5 for plain correlated
noise; the estimator's normalisation (white variance from the profile-level sigma0) does not
reproduce that scale. Abstaining on it demoted every spot to candidate on plates the gate's own
synthetic ground truth says are clean.
**Decision:** VIF is still measured and emitted (`vif`, flag `noise_structured` severity warn
when > 6), but it does NOT change spot status or verdict until the estimator is calibrated
against plates with known structured artefacts (glare/fingerprint synthetics — a Phase 8-9 item).
**Why:** an uncalibrated statistic must not gate results (spec 01 anti-pattern 12 in spirit);
the honest state is "measured, not yet actionable".
**Revisit if:** the calibrated VIF separates artefact plates from clean ones — then row 14 is
re-enabled with the calibrated threshold, recorded here.

## D-013 amendment (attempt 3) · Operating point restated as OPERATING_POINT_v2
The human's two-tier interim decision stands; on the corrected agreement scale (D-015) it is
reported a ≥ 0.55 / candidates a ≥ 0.40 (p_med ≤ 1/61, no z threshold). Measured on the eval
split: reported FP 0.19/blank (0.17 synthetic noise, 0.23 real texture), recall 0.949 (n=315);
candidates recall 0.959. Gate 4 status: boundary (see GATES.md). OPERATING_POINT_v1 is retired.
