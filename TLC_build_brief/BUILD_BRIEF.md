# START HERE

**What this is.** The complete build brief for the TLC plate readout system, plus five binding
specification documents in `specs/`. It is written to be handed to Claude Fable 5 running with
multi-agent orchestration over a long autonomous session.

**How to invoke it.** Put this folder at the root of the repository, alongside your existing
material, so the tree looks like:

```
repo/
├── BUILD_BRIEF.md          ← this file
├── specs/                  ← the five binding specs
│   ├── 01_confidence_and_calibration.md
│   ├── 02_correlations.md
│   ├── 03_backend.md
│   ├── 04_frontend.md
│   └── 05_testing.md
├── research/               ← your research PDFs
├── dataset/                ← your TLC plate images
├── tlc_spec_impl/          ← your prior implementation attempt
└── evaluation/             ← the prior evaluation report + its bundle/ code
```

Then, in one message:

> Read `BUILD_BRIEF.md` completely before doing anything else, then build the system it describes.
> Work through the phases in order and do not begin a phase until the previous gate is met with
> committed evidence. Maintain `decisions.md`, `mistakes.md` and `ASSUMPTIONS.md` continuously, as
> things happen, not at the end. ultracode

**Why the brief is long.** The research has already been paid for. Section 3 contains fifteen
measured findings from a prior evaluation on our own plates — every one of them is something the
build would otherwise spend tokens rediscovering, and several are things it would otherwise get
wrong. Reading section 3 costs a few thousand tokens; re-deriving it costs a day.

**The one thing to check before starting.** Run the Phase 0 dataset audit first. If it reports that
fewer than fifteen images are usable for photometry, the correct next action is a change to how
plates are photographed, not more code — see §10, forced-stop conditions.

---
# BUILD BRIEF — TLC Plate Readout System

> **How to use this file.** This is the complete brief for building the system. Read it once, end to
> end, before writing any code. It is long on purpose: the research it encodes has already been paid
> for, and re-deriving it would cost more than reading it. Everything under **Established findings**
> is measured fact from a prior evaluation on our own plates — treat it as given, do not re-litigate
> it, and do not spend tokens re-discovering it.

---

## 1. Mission

Build an end-to-end system that turns a photograph of a TLC plate into a structured, trustworthy
measurement record, and lets a bench chemist upload plates and browse previous runs.

Two deliverables:

- **Backend** — ingests a plate photo, runs a deterministic measurement pipeline plus a VLM semantic
  layer, and emits a single JSON result per image containing spots, positions, confidence, flags,
  densitograms, and provenance.
- **Frontend** — upload (single and batch), run history, result viewer, plate-to-plate comparison,
  and a review screen where a chemist confirms or corrects the machine's reading.

**The primary requirement is accuracy, and the second is an honest confidence score.** A system that
reports fewer results with correct uncertainty is strictly better than one that reports more results
confidently. This ordering decides every trade-off in this document. When you are unsure which way
to go, choose the option that makes the system refuse more often.

## 2. The five non-negotiables

These are architectural constraints, not preferences. Violating any of them invalidates the build.

1. **No number the user sees may pass through a non-deterministic component.** The VLM layer decides
   *where to look* and *what things are called*. It never produces a position, an area, a ratio, or
   a confidence. Numbers come from deterministic code, seeded, pinned, and byte-reproducible.
2. **Every value in the output is tagged `measured`, `chosen`, or `inferred`.** A default that was
   assumed must never be presented as something that was observed. This is the single most common way
   these systems mislead, and it is trivially preventable at the schema level.
3. **The system must be able to refuse.** Every stage has an abstention path, refusals are
   first-class values in the JSON (not nulls, not zeros), and a refusal carries the reason and the
   remedy.
4. **Accuracy claims require a labelled set.** No accuracy number may be quoted anywhere — in the UI,
   in the README, in a commit message — unless it was computed against human-confirmed labels with a
   stated n and a stated interval. Agreement between two algorithms is not accuracy.
5. **Reproducibility is a feature, not a nicety.** The same image, at the same pipeline version, must
   produce byte-identical numbers forever. This is what makes the method validatable under ICH
   Q2(R2), and it is what makes a regression detectable.

## 3. Established findings — do not re-derive these

A prior evaluation ran eight densitometry methods, 819 OCR runs, a 32-pipeline ensemble, two
independent null tests, and a resolution/tilt study on seven of our own MEHQ plates. The full report
and the code are in the repository. These are its conclusions. **You may verify them cheaply if you
wish, but do not re-run the study.**

### The chemistry and the imaging

Silica F254 plates photographed handheld under 254 nm UV. Analyte appears as **dark
fluorescence-quenching zones on a bright green background** — the plate glows, the spots do not.
The **green channel carries essentially all the signal**; luminance dilutes it with red and blue
noise. Lanes are labelled `S` (starting material), `co` (co-spot), `R` (reaction), `sd` (standard).

### F1 · Exposure clipping is the dominant defect

The green channel reads 255 on **14–60% of the plate on six of seven plates**. Where `I0` is clipped,
`OD = log10(I0/I)` is meaningless, and it is wrong *non-uniformly* because clipping happens where the
plate is brightest. **Consequence for the build:** clipping fraction is a first-class input gate.
Above a threshold, the system reports positions and refuses all photometry.

### F2 · There is no solvent front, so Rf is undefined

No pencil front line exists on any plate, and four of seven plates are cut off in frame. The same
physical spot yields **Rf from 0.34 to 0.97** depending on which defensible origin/front convention
is chosen. **Consequence for the build:** the system reports **Rst** — position relative to the
reference spot in the standard lane on the same plate — as the primary scale. `rf` appears in the
schema only when a real front line was detected, and is `null` with a reason otherwise. Never
silently substitute an "apparent Rf".

### F3 · Background subtraction invents spots, and we know how many

On synthetic blank plates built from the real plate's own noise texture, the standard pipeline at the
conventional operating point (background radius 35 px, 3σ) reports **5.5 spots per blank plate**, and
**100% of blanks yield at least one**. At 5σ, 69% still do. **Consequence for the build:** the
blank-plate false-positive rate is a CI-gating metric with a numeric threshold. It is not an
experiment someone once ran.

### F4 · The noise unit is circular

σ measured on the post-subtraction residual varies **3.6×** with the background radius (0.0032 at
R=4 px to 0.0116 at R=30 px). Masking spots before measuring σ makes every downstream threshold
**1.68× more permissive** (0.01122 masked vs 0.01881 unmasked). **Consequence for the build:** σ is
measured **once, before any spot masking**, and that value is carried through the whole ensemble. A
"3σ rule" whose σ depends on a tuning parameter is not a rule.

### F5 · Method disagreement is about marginal features, not strong ones

Eight published densitometry methods on identical pixels reported **1, 1, 2, 2, 2, 2, 2 and 9** spots
on one lane. On the strong spots they share, the good methods (Gaussian, iterative, polynomial,
median background) cluster to **0.3 px**; the rolling-ball baseline sits 3.3 px outside that cluster.
Kubelka–Munk is the physically correct transform for a scattering layer but **diverges as reflectance
approaches 1**, which is exactly the regime of a faint quenching zone — it reported 9 spots where
others reported 2. **Consequence for the build:** use polynomial-surface or iterative-masked
background with log-OD. Do not use Kubelka–Munk in this regime. Do not treat method spread on strong
peaks as uncertainty; the real uncertainty is in which marginal features clear the bar.

### F6 · Ensemble agreement is the best available mitigation

Running 32 defensible pipelines (4 background radii × 4 background models × 2 thresholds) and
reporting the fraction that find a spot at each position converts an unquantified risk into a
reported number. **On the cleanest plate, no feature reached 80% agreement** — the per-lane maxima
were 0.78, 0.75, 0.66 and 0.56. **Consequence for the build:** this is the core of the confidence
score, and the calibration must not assume agreement saturates near 1.

### F7 · Handwriting is indistinguishable from analyte to a detector

The sharpness/ridge heuristic fired on **47–71% of all plate pixels** — at this resolution a pen
stroke is 1–2 px, which is the noise scale, so the filter carries no information. By optical density,
header ink runs p10–p90 = **0.053–0.162** (max 0.382) and chemistry runs **0.038–0.092** (max 0.352):
the distributions overlap almost entirely and **the ink is often darker than the chemistry**.
**Consequence for the build:** annotation-band location comes from the VLM or from an operator-set
geometric convention. Do not attempt a deterministic ink detector; it has already been tried and it
fails.

### F8 · Classical and neural OCR fail completely on these plates

756 Tesseract runs and 63 EasyOCR runs across every scale, preprocessing and page-segmentation mode:
best CER **0.11** (on a four-character label row), mean **0.74–0.81**, and **not one run recovered a
usable sample identifier**. Measured glyph heights are **3–16 px** against AWS's documented 15 px and
Azure's 12 px minimums. **Consequence for the build:** do not ship Tesseract or EasyOCR as the text
path. Do not spend a phase tuning them.

### F9 · A frontier VLM places bands accurately but hallucinates at the margin

Scored against the pipeline, VLM by-eye band positions agreed to **0.001–0.024** in apparent Rf
(mean 0.012 on one plate) — within human reading precision. But it **invented one band** with only
1.8σ pixel support and **omitted the origin residue**. **Consequence for the build:** the VLM is
excellent for structure and useless as a source of record. Every VLM-proposed band must be confirmed
against the pixels before it enters the spot table.

### F10 · Lane detection from signal fails when a lane is empty

The lane grid slides toward whichever lanes carry material. **Consequence for the build:** lane count
and approximate positions come from the label row (VLM) or the operator; the pipeline refines within
a narrow window. Never infer lane count from the signal alone.

### F11 · Spot shapes are not Gaussian

T-shaped streaks, comet tailing from overloaded origins, solvent halos, and bands so diffuse that one
plate confirmed zero spots at 5σ. **Consequence for the build:** EMG (exponentially modified
Gaussian) is the peak model. Streaking lanes are flagged and **not quantified** — for a streak,
"the position of the spot" is not well defined, and reporting one is a fabrication.

### F12 · There is no ground truth, and none exists publicly

No annotated TLC image dataset exists on Kaggle, Zenodo, Roboflow or HuggingFace. **Consequence for
the build:** building the labelled set is a first-class phase, it blocks every accuracy claim, and
the correction UI is the mechanism that produces it. This is the critical path — treat it as such.

### F13 · Resolution matters for text, not much for spot position

Spot positions are stable to 0.006 apparent Rf down to 10 px/lane; what degrades is sensitivity to
faint bands and the ability to resolve the origin dots. For text it is the fatal constraint.
**Consequence for the build:** do not build a super-resolution step for the measurement path. Do
crop tightly at native resolution for the text path.

### F14 · Cost is not a constraint

Frontier VLM readout is **$0.11–$4.50 per 1,000 plates**; cloud OCR is $1.50; self-hosting only wins
above roughly 400,000 images/month, which is ~185× our volume. **Consequence for the build:** choose
the VLM on accuracy and on whether it exposes a usable confidence signal, never on price. Budget for
3–5× self-consistency sampling as standard; it is still cheaper than a single call to a premium model.

### F15 · Generative "enhancement" is disqualified

Any learned image restoration is a non-linear, uncalibrated intensity transform. It breaks the
pixel→OD map irreversibly and will sharpen JPEG ringing into convincing spots. **Consequence for the
build:** no super-resolution, denoising, or "enhancement" model anywhere upstream of photometry. Ever.
Deterministic rectification and normalisation are fine and are already in the design.

---

## 4. The repository contract

Read in this order. The point of the ordering is that each step tells you what to skip in the next.

| Order | Path | What to do with it | Budget |
|---|---|---|---|
| 1 | This brief | Read completely. | — |
| 2 | `research/` (PDFs) | **Index first, read selectively.** Extract title, abstract and conclusions from each; write `research/INDEX.md` with a two-line summary and a relevance verdict per paper. Then fully read only the papers marked relevant. | Index all; deep-read ≤ 8 |
| 3 | `dataset/` (plate images) | Run a capture audit over the whole set before anything else — resolution, clipping fraction, tilt, frame overrun, per-image. Write `dataset/AUDIT.md` and a machine-readable `dataset/audit.json`. This tells you which images are usable and it is the first phase gate. | All images |
| 4 | `tlc_spec_impl/` (prior code) | **Read it, then decide explicitly whether to keep, port, or discard.** Record that decision in `decisions.md` with reasons. Do not silently rewrite working code and do not silently inherit broken code. | All |
| 5 | The prior evaluation report and its `bundle/` code | The reference implementations for photometry, geometry, the null tests and the ensemble are already written and validated. **Port them; do not rewrite from scratch.** | All |

**Do not** deep-read every research PDF. **Do not** re-run the prior evaluation. **Do not** start
writing application code until the dataset audit exists — you cannot design gates without knowing
what fraction of the corpus can pass them.

## 5. Working protocol

### 5.1 Two logs, maintained continuously

Both live at the repository root, both are append-only, both are written **as things happen**, not
reconstructed at the end.

**`decisions.md`** — every non-obvious choice. One entry per decision:

```markdown
## D-014 · Background model for the primary estimate
**Date:** 2026-08-26
**Status:** accepted
**Context:** F5 says polynomial and iterative-masked agree to 0.3 px; polynomial has 10 free
parameters, iterative has a radius plus an iteration count plus a masking threshold.
**Options considered:**
  1. Polynomial surface, order 3 — fewest parameters, all printable in an SOP.
  2. Iterative masked Gaussian — avoids spots biasing their own background.
  3. Rolling ball — familiar to reviewers, but sits 3.3 px off the cluster (F5).
**Decision:** Polynomial order 3 as the primary; iterative retained in the ensemble.
**Why:** validation cost. Ten coefficients can be written into a method document; a three-stage
iterative masking procedure cannot be described in a way a QA reviewer can check.
**Consequences:** slightly worse under strong local gradients — mitigated because the ensemble
still contains the iterative variant, so disagreement will surface it.
**Revisit if:** the blank-plate FP rate for polynomial exceeds the iterative variant's by >20%.
```

**`mistakes.md`** — every thing that did not work, what the symptom was, what the cause turned out
to be, and what fixed it. **Write the entry when you hit the problem, before you fix it.** A mistake
log written retrospectively is a sanitised fiction and is worth nothing.

```markdown
## M-007 · Lane detector locked onto the plate edge, not the lanes
**Symptom:** on 4 of 31 images the leftmost detected lane sat at x < 3 px.
**Wrong hypothesis:** peak-finding distance parameter too small. Changing it did not help.
**Actual cause:** the rectification warp leaves an interpolated rim of partially-plate pixels; the
eroded valid mask was 2 px but the warp bleeds ~4 px at the corners on tilted plates.
**Fix:** erosion radius derived from measured tilt rather than fixed at 2 px, plus an explicit
`lane_at_plate_edge` flag when a detected centre is within one lane half-width of the mask boundary.
**Test added:** `test_lane_never_at_edge` over the full dataset; property test on synthetic tilted plates.
**Lesson:** a fixed pixel constant in a pipeline that handles variable geometry is a latent bug.
```

Also maintain **`ASSUMPTIONS.md`** — every place you had to decide something the brief did not
specify. This is where the human reviewer looks first.

### 5.2 Phase discipline

Work in the phases in §6. Each phase has a numeric gate. **You may not begin a phase until the
previous phase's gate is met and the gate evidence is committed.** If a gate cannot be met, stop and
write up why rather than lowering the gate. Lowering a gate is itself a decision and goes in
`decisions.md` with an explicit statement of what accuracy is being traded away.

### 5.3 When to fan out, and when not to

You are running with multi-agent orchestration available. Tokens spent on the wrong thing are worse
than tokens not spent, so:

**Fan out for:**
- Independent exploration where you need coverage, not depth — the research PDF index, the dataset
  audit across many images, surveying the prior implementation.
- Genuinely parallel implementation of decoupled modules with a fixed interface already agreed
  (e.g. the five background models, once the signature is settled).
- Adversarial verification of any accuracy claim, any calibration result, and any correlation
  finding. **Every number that will be shown to a chemist gets an independent agent whose job is to
  refute it.** This is the highest-value use of parallelism in this build.
- Reviewing a completed phase against its gate, by an agent that did not write the code.

**Do not fan out for:**
- Boilerplate — API scaffolding, CRUD, config plumbing, component shells. Write it directly.
- Anything where the agents would need to agree on an interface that does not exist yet. Settle the
  interface first, solo, then fan out.
- Sequential debugging. One agent with the full trace beats five with fragments.
- Re-deriving anything in §3.

**A rule of thumb:** fan out when the work is *wide*, work solo when it is *deep*, and always fan out
to *check*, never only to *produce*.

### 5.4 Commit and checkpoint discipline

Commit at every gate, with the gate evidence in the commit message. Keep the working tree clean
between phases. If you are about to make a change that would invalidate a passed gate, say so
explicitly in `decisions.md` first.

---

## 6. The phase plan

Eleven phases. Each has a **gate** — an objective, numeric condition that must be demonstrated before
the next phase starts. Gates are checked by an agent that did not write the code.

### Phase 0 · Audit and orientation

Index the research PDFs. Audit every image in `dataset/`. Read and judge `tlc_spec_impl/`. Port the
validated reference implementations from the prior evaluation bundle. Set up the repo, dependency
pinning, CI skeleton, and the three logs.

**Gate 0** — `dataset/audit.json` exists and covers 100% of images with: pixel dimensions, in-plate
green-clipping fraction, estimated tilt, per-edge frame-overrun fraction, and a usability verdict.
`research/INDEX.md` covers 100% of PDFs. `decisions.md` contains an explicit keep/port/discard
decision for the prior implementation. CI runs and is green on an empty test suite.

### Phase 1 · Synthetic plate generator

Before touching real plates, build the thing that gives you exact ground truth. A generator that
produces plates with known spot positions, widths, amplitudes and shapes (Gaussian and EMG) on a
realistic illumination surface with realistic correlated noise, plus switchable defects: clipping at
a chosen fraction, tilt, frame overrun, streaking, handwriting overlay, missing front line, empty
lanes, overloaded origin with halo.

This phase exists first because **every later gate depends on it**, and because a generator built
after the pipeline will unconsciously be shaped to flatter the pipeline.

**Gate 1** — the generator reproduces the measured statistics of the real corpus: illumination swing
within the observed range, residual noise sd within ±20% of the real plates' empty-band value, and a
clipping-fraction knob that reproduces the observed 14–60% range. A human-visible side-by-side of
three synthetic and three real plates is committed.

### Phase 2 · Deterministic geometry

Plate detection, frame-overrun check, corner extraction, projective rectification, valid-mask
erosion derived from measured tilt. No photometry yet.

**Gate 2** — on synthetic plates with known corners: corner recovery error < 1.5 px at 95th
percentile across the tilt range 0–12°. On the real corpus: plate detected on 100% of usable images,
frame-overrun correctly flagged on the four known cases, and rectification is idempotent (re-warping
a rectified plate is a no-op within 0.5 px). Determinism: identical output hash across two runs and
two machines.

### Phase 3 · Photometry and the noise unit

Signal channel selection, illumination model (polynomial primary; iterative, Gaussian, median,
rolling-ball as ensemble members), log-OD conversion, and **σ measured once, before any spot
masking** (F4).

**Gate 3** — Rst is invariant to a global exposure scale factor of 0.7–1.3 to within 0.005 (a
metamorphic property, no ground truth needed). Recovered spot amplitude is monotonic in synthetic
spot amplitude with Spearman ρ > 0.98. σ is stable to within 15% across background radii, because it
is no longer measured on the residual — if it is not, the implementation is wrong.

### Phase 4 · Ensemble detection and the null battery

The 32-pipeline agreement ensemble. The blank-plate null test, wired as a CI-gating metric.

**Gate 4** — **blank-plate false-positive rate ≤ 0.2 spots per plate** at the shipped operating
point, measured over ≥ 200 synthetic blanks including the real-noise-texture variant. Simultaneously,
recall on synthetic plates ≥ 0.95 for spots at ≥ 5σ amplitude. Both must hold at once; achieving
either alone is not a pass. If they cannot both be met, that is a finding — write it up and propose
the operating point that best trades them, with the curve.

### Phase 5 · Peak modelling, Rst, and refusal

EMG fitting seeded by the ensemble, position and area with intervals, streak detection, Rst against
the standard-lane reference spot, and the full abstention decision table.

**Gate 5** — position error on synthetic plates < 0.01 in Rst at 95th percentile for confirmed spots.
Every streaking synthetic lane is flagged and quantification is suppressed. Every plate in the real
corpus produces either a result or a refusal with a reason and a remedy — zero silent nulls.

### Phase 6 · The labelled set and the correction UI

**This is the critical path.** Build the minimal review interface and get a chemist through 30–50
plates. Two reviewers on an overlapping subset so inter-reviewer agreement can be measured.

**Gate 6** — ≥ 30 plates labelled, ≥ 10 double-labelled, inter-reviewer agreement reported with an
interval. Labelled set is split into disjoint tune / calibrate / hold-out partitions **grouped by
plate**, and the hold-out is written to a location the pipeline code cannot read.

### Phase 7 · Calibration

Fit and validate the confidence map. Nothing before this phase may display a confidence number to a
user.

**Gate 7** — expected calibration error ≤ 0.10 on the held-out partition with a bootstrap interval
reported; a reliability diagram is committed; grouped cross-validation by plate is used throughout.
The honest statement of what n permits is written into `ASSUMPTIONS.md`.

### Phase 8 · The VLM semantic layer

Lane count, lane labels, annotation bands, front-line presence, sample ID and conditions. Structured
output with `UNREADABLE` in every enum. Self-consistency sampling. Response caching by image hash.
An offline replay mode so tests never hit the network.

**Gate 8** — field-level accuracy on the held-out labelled set, reported per field with intervals.
Lane count correct on ≥ 95%. The abstention path demonstrably fires on a blank image, a photo of
something else, and a plate with no writing. Full offline determinism: the whole test suite passes
with the network disabled.

### Phase 9 · Correlations

The fixed hypothesis list, the confound checks, the multiple-comparison control, and the
"insufficient data" default.

**Gate 9** — on label-shuffled data, the number of surfaced findings is ≤ the nominal false-discovery
rate over ≥ 500 shuffles. Every confound check runs. At the current corpus size the system correctly
reports that almost nothing is claimable — if it reports a confident finding from 7 plates, the gate
has failed.

### Phase 10 · API and persistence

The endpoints, the schema, the storage of the OD field, run history, versioned re-run and diff.

**Gate 10** — schema validation on 100% of outputs; a historical run can be re-executed at its
recorded version and produces a byte-identical result; API contract tests pass.

### Phase 11 · Frontend

Upload with immediate capture QC, run list, result view, comparison view, review screen.

**Gate 11** — a chemist who has not seen the system can upload a plate, understand from the screen
alone whether the result is trustworthy, and correct a wrong spot, without being told how. Test this
with an actual person; write what they got stuck on into `mistakes.md`.

### Phase 12 · End-to-end and honest reporting

**Gate 12** — the full accuracy report on the held-out partition, with n and intervals on every
number, committed as `EVALUATION.md`. Any metric that cannot be computed for lack of labels is listed
as not computed rather than omitted.

---

## 7. The detailed specifications

Five binding specification documents sit in `specs/`. Read each **in full** at the start of the phase
that needs it, not before — they are dense and reading them all up front wastes context you will need
later.

| File | Read at | Binding on |
|---|---|---|
| `specs/01_confidence_and_calibration.md` | before Phase 4 | the whole confidence contract: the claim inventory, the ensemble design, the calibration method, conformal intervals, the abstention decision table, VLM self-consistency |
| `specs/02_correlations.md` | before Phase 9 | the fixed hypothesis registry, the confound panel, the statistical gate, the null battery for findings, the presentation JSON, the "insufficient data" ladder |
| `specs/03_backend.md` | before Phase 10 (skim §7.3 at Phase 2) | stack, determinism machinery, the complete result JSON schema, OD-field storage, API surface, the correction loop, the VLM integration layer |
| `specs/04_frontend.md` | before Phase 11 | stack, every screen, uncertainty visualisation, refusal microcopy, densitogram chart spec, accessibility |
| `specs/05_testing.md` | before Phase 1 | the synthetic generator, the null battery, the property suite, golden policy, the evaluation harness, CI |

Where a spec conflicts with this brief, **this brief wins** on the five non-negotiables (§2) and the
established findings (§3); the spec wins on everything else. Where two specs conflict, stop and
record the conflict in `decisions.md` with your resolution — do not pick silently.

One structural note, because it is the most likely source of a wasted week: `specs/03_backend.md`
§7.3 defines the result JSON schema, and almost everything else in the system either produces or
consumes it. **Settle that schema in Phase 2, before writing pipeline code**, even though you will not
build the API until Phase 10. Changing it at Phase 10 means rewriting Phases 3–9.

---

## 8. The VLM layer — concrete guidance

### 8.1 What the VLM is allowed to do

Exactly five jobs, and nothing else:

| Job | Output type | Why the VLM and not code |
|---|---|---|
| Lane count and approximate lane x-positions | integer + list | F10 — signal-based lane finding fails on empty lanes |
| Lane labels | closed enum per lane | F8 — no OCR engine can read them; closed vocabulary is the VLM's strongest regime |
| Annotation band extents (header, label row) | two fractions of plate height | F7 — no deterministic detector works |
| Is a pencil solvent-front line present? | boolean + approximate row | F2 — needs a semantic judgement about what a drawn line is |
| Sample ID and conditions text | free text, flagged for review | F8 |

It does **not** produce spot positions, areas, ratios, Rst values, confidence numbers, or chemical
conclusions that enter the record. It may produce a clearly-labelled draft comment for a chemist to
read alongside the numbers, and that comment is never stored as a result field.

### 8.2 Model selection

Choose on measured field accuracy against your own held-out labelled set, not on benchmarks. Start
with two candidates and measure:

- **Gemini Flash-Lite at low media resolution** — ~$0.11 per 1,000 plates. Google's own documentation
  notes OCR quality saturates at *medium* resolution and that *high* rarely improves it, so do not
  pay for the higher tier without measuring.
- **Claude Haiku 4.5 via the batch API** — ~$1.12 per 1,000 plates.

Useful prior from the closest public benchmark (socOCRbench, degraded real-world handwriting,
normalised edit similarity): frontier hosted models score 0.54–0.68, small open-weight models
0.43–0.48, and Tesseract 0.135. Expect free-text header accuracy in the frontier band and
**near-98% on closed-vocabulary lane labels** — a 2026 study on constrained handwritten answers
found 98.4% for a Flash-tier model. Design around that asymmetry: the lane labels are nearly solved,
the header is not.

**Note on confidence:** Anthropic does not expose logprobs, and Google removed them for Gemini 3.x.
If a token-level confidence signal turns out to be necessary, the only route is a self-hosted model
under vLLM. Do not architect around logprobs being available.

### 8.3 The request design

- **Crop tightly at native resolution** before sending. A tight crop beats the whole plate
  downscaled, on both accuracy and cost.
- **Structured output with a JSON schema**, and `UNREADABLE` present in **every** enum. Constraining
  a lane label to `{S, co, R, sd}` on an illegible lane forces the model to pick one of four wrong
  answers with full syntactic confidence. Including `UNREADABLE` is what makes constrained decoding
  safe rather than dangerous.
- **Self-consistency sampling**, 3–5 samples, and treat disagreement as the abstention trigger.
  Published evidence puts multi-sample agreement at ~42% relative better than a model's own
  self-assessment for predicting OCR correctness. At Flash-Lite prices five samples cost about $0.55
  per 1,000 plates, so this is effectively free.
- **Do not** tell the model what it should expect to see. Supplying the expected product or a prior
  Rf makes it more likely to report seeing it — this is the documented hallucination mechanism.
  Priors may be used to decide *where the pipeline measures*, never to inform the model's reading.
- **Prompt-only abstention is not a control.** Asking a model to say "UNREADABLE if unsure" moves
  refusal rates from ~2% to ~12% in published tests, and one study found admission rates *fell* when
  models were asked directly. Treat the instruction as a weak nudge; the real control is
  multi-sample disagreement plus pixel confirmation.

### 8.4 Confirmation against pixels

F9: the VLM invented a band with 1.8σ support. Therefore **every VLM-proposed band is a hypothesis,
not a finding.** The pipeline measures at the proposed location and the band enters the spot table
only if it clears the ensemble criterion. A VLM-proposed band that fails confirmation is recorded in
the JSON as `proposed_unconfirmed` with its pixel support, because that disagreement is informative
and hiding it loses information.

### 8.5 Offline determinism

Cache every response keyed by `(image_hash, prompt_version, model_id, sample_index)`. The test suite
must pass with the network disabled, replaying from cache. A provider silently updating a model is a
real risk; a fixed prompt-regression suite run nightly against live endpoints is how you detect it.

---

## 9. Anti-patterns — things that will look like progress and are not

Each of these has already cost someone time on this specific problem.

1. **Tuning Tesseract.** F8. Nine hundred configurations have already been tried. Do not spend a day
   on preprocessing tricks; the glyphs are below the engine's documented floor.
2. **Chasing a prettier densitogram.** A smooth baseline is a rendering choice, not a measurement
   improvement. If smoothing changes the spot count, the spot count was never real.
3. **Reporting agreement between two algorithms as accuracy.** It is not. Only human-confirmed labels
   are accuracy (non-negotiable #4).
4. **Letting the VLM produce a number.** It will produce one, fluently, and it will be plausible.
   Non-negotiable #1.
5. **Adding a super-resolution or denoising step to "help" the pipeline.** F15. This is the most
   tempting wrong move in the whole build, because the output looks better.
6. **Measuring σ after spot masking.** F4. It silently makes every threshold 1.68× looser.
7. **Inferring lane count from the signal.** F10. It works on every plate where you'd notice and
   fails on the ones you wouldn't.
8. **Fitting a symmetric Gaussian to a tailing peak,** then reporting its centre. F11.
9. **Quantifying a streaking lane.** The position of a streak is not defined; any number is invented.
10. **Presenting an apparent Rf as an Rf.** F2. The spread across conventions is 0.39–0.80.
11. **Building a spot-detection CNN.** No public dataset exists (F12), and the published precedent in
    the adjacent domain found the neural approach gave *no quantification advantage* over classical
    background-corrected densitometry. Build the classical path first and prove it insufficient.
12. **Writing the mistake log at the end.** It becomes a tidy fiction. Write entries at the moment of
    failure, before the fix.
13. **Fanning out to write boilerplate.** §5.3.
14. **Meeting a gate by lowering it.** If you change a gate, that is a `decisions.md` entry stating
    what accuracy is being traded and why.

## 10. Forced-stop conditions

Stop and write up rather than proceeding, if any of these occur:

- A gate cannot be met after two genuine attempts, and the third attempt would require weakening the
  accuracy contract.
- The dataset audit shows fewer than 15 images are usable for photometry. The right answer then is a
  capture-protocol change, not more code, and that is a decision for a human.
- The labelled set cannot be produced because no chemist is available. Everything from Phase 7 onward
  is blocked; build Phases 8–11 against synthetic ground truth only and mark every accuracy field as
  not computed.
- Blank-plate false-positive rate and recall cannot be satisfied simultaneously at any operating
  point. Report the trade-off curve and let a human choose.
- You find yourself about to hard-code a magic constant to make a specific real plate pass. Stop.
  That is the moment the system starts lying.

## 11. Definition of done

- All twelve gates passed, with evidence committed.
- `decisions.md`, `mistakes.md`, `ASSUMPTIONS.md` are substantial and were written as you went.
- `EVALUATION.md` reports every accuracy metric against the held-out labelled set, with n and an
  interval on every number, and explicitly lists what could not be measured.
- A chemist can upload a plate and get either a trustworthy result or a clear refusal with a remedy.
- Re-running any historical run at its recorded pipeline version reproduces it byte-for-byte.
- The whole test suite passes with the network disabled.
- No number anywhere in the UI, the JSON, or the docs is presented with more confidence than the
  evidence supports. When in doubt, the system says so.

---

## 12. A closing instruction

The failure mode for this build is not that it produces nothing. It is that it produces a polished
system that confidently reports five spots on a blank plate, an Rf that depends on an unstated
convention, and a sample ID that a language model invented — and that nobody notices for six months
because it all looks right.

Everything in this brief exists to prevent that specific outcome. When a trade-off arises between
shipping a feature and being able to say honestly how wrong the system might be, choose the second.
The chemists using this will make decisions about real reactions from its output, and a wrong number
delivered confidently is worse than no number at all.
