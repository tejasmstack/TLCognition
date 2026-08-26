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
