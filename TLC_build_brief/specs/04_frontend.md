# 11. The frontend

> Scope: everything a chemist touches. This section is binding at the same level as the rest of the brief. Where it conflicts with a habit ("charts should be pretty", "confidence should be a percentage"), this section wins.
>
> The governing principle, restated for the UI: **the interface's job is not to present the answer, it is to present how much of an answer there is.** A screen that makes a 25-of-32 detection and a 10-of-32 detection look similar has broken the whole build, no matter how correct the backend is.

---

## 11.1 Stack

### 11.1.1 The SPA question, settled

**Decision: server-rendered HTML from the existing FastAPI process, HTMX for interaction, and three self-contained TypeScript "islands" for the parts that genuinely need a canvas. No SPA.**

The argument, explicitly:

**What an SPA would buy us.** One paradigm, familiar to more hires, client-side routing that makes lane-switching instant, and a single place to hold view state.

**What it would cost, in this specific system.**

1. **A second copy of the data model.** The JSON record is large, deeply nested, and carries the `measured|chosen|inferred` tag on every value. In an SPA that schema is re-expressed in TypeScript types, re-validated client-side, and re-rendered by components that drift from the Pydantic models the moment someone adds a field. Every drift is a chance to render a `chosen` value as if it were `measured` — which is non-negotiable #2, failing silently in the layer nobody tests.
2. **Duplicated formatting logic.** Sig-fig truncation to the interval (§11.3.B) has to live somewhere. In the server-rendered design it lives once, in Python, next to the numbers. In an SPA it lives twice or it lives only in TS — and then the JSON export, the print view and the CSV disagree with the screen.
3. **Dependency rot on a two-year horizon.** This tool has ~5 users and no dedicated frontend owner. A React app with a router, a data-fetching layer, a state store, a chart wrapper and a component library is 40–60 direct dependencies whose transitive tree will not install cleanly in 2028. Jinja templates and one 45 KB chart library will.
4. **We do not need the thing SPAs are for.** There is no optimistic UI, no live collaboration, no offline queue, no high-frequency mutation. Five screens, all of which are documents about a run.

**What server rendering buys us that an SPA does not, and that this project specifically needs.**

- **The numbers are in the HTML.** View-source on a result page shows the reported values. That is an audit property, and it means a broken JS bundle degrades to a readable record rather than a spinner.
- **Deep links are free and total.** `?lane=2&spot=L2-3&scale=rst` is the entire view state; a chemist pastes it into Slack and their colleague sees exactly the same screen. No serialisation layer.
- **Print is nearly free.** A result page is what gets stapled into a lab notebook. One print stylesheet, no headless-browser PDF service.
- **One deployable, one language for routing, auth and data.** The person maintaining this in two years is the person maintaining the pipeline.

**Where the islands go, and why they are not "React by another name".** Three interactive surfaces need real client code: the capture-QC image analyser, the plate-image layer stack, and the densitogram. Each is a canvas/SVG renderer with an imperative API — exactly the kind of thing you would wrap in a `useEffect` and a ref anyway. Wrapping them in a framework adds the framework and removes nothing.

**Revisit if:** the app grows past ~10 screens, or a genuine multi-user editing surface appears. Record that as a `decisions.md` entry with what changed.

### 11.1.2 The stack, decisively

| Layer | Choice | One-line justification |
|---|---|---|
| Server framework / templating | **FastAPI + Jinja2**, same process as the API | A result page and its JSON come from one object in one process, so they cannot disagree. |
| Interactivity | **HTMX 2** | Filter, sort, paginate, poll a running job and save a correction are all form posts returning HTML partials; this needs zero client state. |
| Islands | **Vanilla TypeScript, mounted on `<div data-island="…">`**, strict mode | Three canvas surfaces with imperative APIs; a component framework would add lifecycle machinery around code that has no lifecycle. |
| Build tool | **Vite 7** (library-ish mode, hashed assets + `manifest.json` read by a Jinja helper) | Least-config bundler with a stable dev server; FastAPI serves the built assets directly, no Node in production. |
| Charting | **Observable Plot** (D3 scales underneath) | It maps data to arbitrary x/y, so "migration on the vertical axis increasing upward" is one line of config rather than a fight with a chart framework's axis model; `areaX`/`ruleY`/`tip` marks cover the noise band, origin/front rules and hover; SVG output prints as vector into a lab notebook. |
| State management | **The URL query string.** `localStorage` only for view preferences (theme, chart orientation, default overlays). | Any state a chemist would want to send to a colleague must survive being pasted into Slack; anything that fails that test does not need a store. |
| Styling | **Plain CSS, one stylesheet, CSS custom properties as design tokens, `@layer` for cascade order.** No Tailwind, no CSS-in-JS. | The palette must be swappable wholesale for dark mode, high-contrast and greyscale-print (§11.6) — custom properties do that in one file; and forty semantic classes do not repay a utility framework's build step. |
| Types | `openapi-typescript` generated from the FastAPI schema at build time, committed | The islands consume the same contract the API publishes; a backend field rename breaks the build, not a chart. |
| Tests | **Playwright** for the five screens and the Gate 11 walkthrough; **Vitest** for the QC maths and the number formatter | The formatter and the QC estimator are the only frontend code that computes anything; everything else is layout and is tested by walking it. |
| Identity | Reviewer name chosen on first visit, stored in a signed cookie. No accounts, no roles. | Gate 6 needs per-reviewer labels and inter-reviewer agreement; nothing else needs auth behind the lab VPN. |

**Hard rule: no reported number is computed in the browser.** The one exception is capture QC (§11.2a), which is explicitly advisory, is labelled *provisional* on screen, is never stored as a measurement, and is recomputed authoritatively server-side. If the two disagree by more than 3 percentage points on clipping fraction, the server value is shown and the discrepancy is logged to `mistakes.md` fodder.

### 11.1.3 Routes

```
GET  /                          → 302 /runs?filter=needs_review
GET  /upload
POST /upload                    multipart; returns an HTMX partial per file
GET  /runs                      ?q=&from=&to=&capability=&reviewed=&flag=&sort=&page=
GET  /runs/{run_id}             ?lane=&spot=&layer=&scale=&od_opacity=
GET  /runs/{run_id}.json        the exact stored record, byte-identical to the pipeline output
GET  /runs/{run_id}/print
GET  /runs/{run_id}/review      ?pass=a|b
POST /runs/{run_id}/labels      immutable append; never mutates the machine result
GET  /compare                   ?runs=a,b,c&lane=R&anchor=origin
GET  /method                    "How to read these numbers" — the honest disclosure page
GET  /labels/progress           Gate 6 progress, inter-reviewer agreement
```

`/method` is not documentation garnish. Every confidence element, refusal card and scale badge links into a specific anchor on it. It states the current operating point, the measured blank-plate false-positive rate, the σ definition, and what has and has not been validated. It is generated from the same config the pipeline reads, so it cannot go stale.

### 11.1.4 Data the frontend needs from the JSON

Beyond the obvious. Flag any of these that the backend does not yet emit before Phase 11 starts.

- `spots[].agreement`: `{found: int, total: 32, threshold: int}` — the tally, not a fraction.
- `spots[].position_spread`: the per-pipeline centre positions (array) so the interval can be drawn as an empirical spread, not just a ±.
- `spots[].shape`: `gaussian | emg | streak | unmodelled`, plus `fit_residual`.
- `lanes[].sigma`: the single pre-masking σ, in OD, per lane and per plate.
- `lanes[].detection_limit_od`: what the faintest visible band would have been.
- `lanes[].densitogram`: min/max envelope decimated to ≤1200 samples (never mean-smoothed — anti-pattern #2), plus a link to the full-resolution array.
- `assets.od_map_png`, `assets.clip_mask_png`, `assets.rectified_png` — **rendered server-side at pipeline version**, with `od_map_domain: [min, max]`. Reproducibility applies to pixels the chemist looks at, not only to numbers.
- `refusals[]`: `{code, scope, measured, threshold, context{}}` — a code and its numbers, never a sentence. The frontend owns the sentence (§11.4.3).
- `provenance`: `{image_sha256, pipeline_version, seed, vlm_model_id, prompt_version, config_hash}`.
- `vlm.sample_id`: `{value|null, samples: ["MEHQ-14","MEHQ-14","MEHQ-14","MEHQ-1A","MEHQ-1A"], confirmed_by, confirmed_at}`.
- `calibration_state`: `uncalibrated | calibrated{ece, n, interval, date}` — gates whether any probability is rendered at all.

---

## 11.2 The screens

### (a) Upload — `/upload`

**Purpose.** Get the photograph in, and tell the chemist within about two seconds whether it will support a measurement — while the plate is still on the bench and the lamp is still on. A rejection that arrives after the chemist has walked away is worth almost nothing.

**Layout.**

```
┌───────────────────────────────────────────────────────────────┐
│  Drop plate photographs here          [ Choose files ]        │
│  or paste from clipboard, or [ Use camera ]                   │
├───────────────────────────────────────────────────────────────┤
│  ┌── CaptureQCCard ─────────────────────────────────────────┐ │
│  │ [thumb w/ clip     ]  IMG_4821.HEIC   3024 × 4032        │ │
│  │ [mask painted      ]                                     │ │
│  │ [magenta           ]  Positions   ██████ expected ok     │ │
│  │                       Photometry  ▨▨▨▨▨▨ at risk — 34%   │ │
│  │                                    of frame is clipped   │ │
│  │                       Framing     ██████ all edges clear │ │
│  │                       Text        ▨▨▨▨▨▨ label ~7 px     │ │
│  │                                                          │ │
│  │  Reduce exposure ~2 stops and retake. [ Why? ] [ Remove ]│ │
│  └──────────────────────────────────────────────────────────┘ │
│  … one card per file …                                        │
├───────────────────────────────────────────────────────────────┤
│  Plate metadata (applies to all, editable per file)           │
│  Plate ID  [        ]  Sample ID [        ]  Operator [ ▾ ]   │
│  Solvent system [                     ]  Date [auto]          │
│  Lanes  [4 ▾]   L1 [S ▾] L2 [co ▾] L3 [R ▾] L4 [sd ▾]         │
│  ⓘ If you enter the lane labels here, the model only confirms │
│    them. That is strictly more reliable than letting it read. │
├───────────────────────────────────────────────────────────────┤
│  [ Upload the 7 that passed ]  [ Upload all 11, flagged ]     │
└───────────────────────────────────────────────────────────────┘
```

**Client-side QC — what is computed, in a Web Worker, on the decoded full-resolution bitmap.** Budget: ≤300 ms per image on the bench tablet. All advisory.

| Check | Method | Displayed as |
|---|---|---|
| Green clipping fraction | fraction of pixels with `G ≥ 254`, over the central 80% crop (plate detection has not run) | percentage + **magenta clip mask painted on the thumbnail** |
| Resolution | short-side px; if lane count is known, px per lane | `~180 px/lane` or `3024 × 4032` |
| Text legibility | estimated glyph height from the operator-declared header band, or the top 12% strip | `label ~7 px — below the 12–15 px readers need` |
| Frame overrun | bright-region mask touching any image border for >2% of that border | per-edge chips: `top clear · bottom cut` |
| Tilt | dominant edge orientation of the bright region's hull | `4.1°` — advisory only, rectification handles it |
| Blur | normalised variance of Laplacian, reported as a three-state band, never a number | `sharp / soft / blurred` |

**The single most valuable element on this screen is the magenta clip mask.** A chemist fixes a photograph faster from a picture of what is wrong than from a sentence about it. Magenta is chosen because it does not occur on a 254 nm plate, so there is no ambiguity about what is overlay and what is sample.

**Interactions.** Drag-drop; clipboard paste; `capture="environment"` file input on tablets. Per-file progress with retry. SHA-256 computed client-side and shown as the first 8 hex characters — that becomes the image handle and matches the server's hash and the VLM cache key. EXIF orientation honoured; GPS stripped; original bytes preserved untouched (no client-side re-encode, ever — F15 applies to the browser too).

HEIC decode is unavailable in some browsers: fall back to uploading first, running QC server-side, and returning the same card via HTMX within a couple of seconds. The card looks identical; only its provenance chip says `server`.

**Batch mode** is the same card list, sorted worst-first, with a summary bar and three buttons (see refusal case R12 in §11.4). Never silently drop a file the user chose.

---

### (b) Run list — `/runs`

**Purpose.** Find a plate. Know at a glance which runs are trustworthy for what. Get to review.

**Layout.** A dense, server-rendered table. Default landing filter during Phase 6 is `needs_review`.

```
Corpus: 118 runs · 24 labelled · 8 double-labelled · accuracy not yet computed  [/method]

[ search sample or plate ID    ] [date ▾] [capability ▾] [reviewed ▾] [flag ▾]   118 runs

▢  thumb  Run          Plate     Sample          Lanes  Bands        Capability            Rev
▢  ▣      2026-08-24   MX-0412   MEHQ-14         4      3 + 2 marg   ███ ▨▨▨ ███ ███      ✓ AK
   14:02  #a19f3c                (confirmed)                          pos  pho  ide  sca
▢  ▣      2026-08-24   MX-0413   MEHQ-1A ⌇       4      2            ███ ███ ▨▨▨ ███      —
   14:19  #7b21de                (unverified)
▢  ▣      2026-08-23   MX-0409   — not read      3      refused      ███ ░░░ ░░░ ▨▨▨      —
   09:41  #04c8aa                                       ⌦ disagreement
```

**Columns.** Thumbnail (48 px, rectified, with confirmed spot ticks drawn) · date+time+short hash · Plate ID · Sample ID with verification state · lane count · band summary (`n confirmed + m marginal`, or the refusal glyph) · **CapabilityBar** (four segments, §11.3.F) · review state with reviewer initials.

**The quality indicator is the four-segment capability bar, not a score.** Rationale in §11.3.G. It is sortable — sorting by capability sorts lexicographically on the four states, which puts "everything works" at one end and "nothing works" at the other without inventing a scalar.

**Filters.** Date range · capability (`photometry available`, `positions only`, `refused`) · reviewed / unreviewed / double-labelled · flag (`clipped`, `streak`, `frame overrun`, `no front`, `ensemble disagreement`) · pipeline version. All as query params, all server-side, all HTMX partial swaps into the `<tbody>`.

**Search.** Substring over plate ID, sample ID, operator and free-text conditions. Results are split into two labelled groups — **Confirmed IDs** and **Unverified reads** — with the unverified group below a rule. A chemist searching "MEHQ-14" must not have a VLM guess silently included in the same list as a human-confirmed match.

**Bulk actions.** Select rows → `Compare` (opens `/compare?runs=…`), `Re-run at current pipeline version` (creates new runs; never mutates), `Export JSON/CSV`.

**Not built:** infinite scroll, live auto-refresh that moves rows under the cursor, saved-view management UI (URLs are the saved views).

---

### (c) Result view — `/runs/{id}` — the core screen

**Purpose.** Let a chemist answer "is my starting material gone, is there a new spot, and can I believe this?" — with the third question answerable from the screen alone.

**Layout at ≥1440 px — three columns, one shared vertical migration axis binding the middle two.**

```
┌ left rail 280px ──┬─ plate panel ─────────┬─ densitogram column ──────────┐
│ MX-0412 · 24 Aug  │  Rst                  │  L1 S      L2 co   L3 R   L4 sd│
│ #a19f3c           │  1.2─                 │  │         │        │      │  │
│                   │      ┌──┬──┬──┬──┐    │  │         │        │      │  │
│ Capability        │  1.0─┤▬ │▬ │  │▬ │◆   │  ├─◀       ├─◀      │      ├─◀│
│  pos  ███ measured│      │  │  │  │  │    │  │         │        │      │  │
│  pho  ▨▨▨ refused │  0.6─┤  │  │▬ │  │    │  │         │      ┌─┤      │  │
│  ide  ███ confirmd│      │▓▓│▓▓│▓▓│▓▓│    │  ▒▒▒▒      ▒▒▒▒    ▒▒▒▒   ▒▒▒▒│
│  sca  ███ Rst     │  0.0─┴──┴──┴──┴──┘    │  └───────  └─────   └────  └──│
│                   │      origin (measured)│   OD →                        │
│ Flags (3)         │                       │                               │
│  ⌦ clipping 41%   │  layers: ▣ rectified  │  ▒▒ = noise band (3σ)         │
│  ⌦ no front       │          ▢ OD map     │                               │
│  ⌦ L3 streak      │          ▣ spots      │                               │
│                   │          ▣ clip mask  │                               │
│ Provenance        │          ▢ orig photo │                               │
│  pipeline 0.9.3   │                       │                               │
│  seed 20260824    │                       │                               │
│  reproduced ✓ 25/8│                       │                               │
│ [ Review ] [Print]│                       │                               │
└───────────────────┴───────────────────────┴───────────────────────────────┘
┌ Spot table ─────────────────────────────────────────────────────────────┐
┌ Band correspondence matrix ─────────────────────────────────────────────┐
┌ Refusals and what to change ────────────────────────────────────────────┐
┌ Findings (fixed hypothesis register) ───────────────────────────────────┐
```

Below 1440 px the densitogram column collapses to a single panel with a lane selector; below 1024 px (tablet portrait) the columns stack and the shared axis becomes a scroll-synchronised pair.

**The plate panel** is a stack of canvas layers under one transform, owned by the `MigrationScale` module (§11.5). Layers, each independently toggleable, state in the URL:

| Layer | Default | Notes |
|---|---|---|
| Rectified plate image | on | toggle to original photo, which draws the rectification quad over it |
| Lane boundaries | on | dotted; labelled with the lane label and its source (`operator` / `VLM` / `refined`) |
| Spot markers | on | see §11.3.A/B for the encoding |
| Position intervals | on | drawn **to scale on the image** — the uncertainty occupies real plate distance |
| OD map | off | cividis, with a colourbar in OD units and an isoline toggle; opacity slider |
| Clip mask | on **when clipping > 0** | magenta, 50% |
| Valid mask edge | off | shows the eroded boundary |
| Annotation bands | on | 45° hatch over header/label rows, captioned `excluded from measurement` |
| Unconfirmed VLM proposals | on, recessive | dotted open rings; hiding disagreement loses information |

A migration ruler runs down the left edge in Rst, with the reference band marked by a diamond at 1.00.

**Spot table.** Sorted by lane then Rst. Confirmed spots above a rule; marginal/unconfirmed below it in a section headed `Below the detection threshold — not counted`.

| Lane | Label | Rst | Rf | Height (OD) | Area | Agreement | Shape | |
|---|---|---|---|---|---|---|---|---|
| 2 | co | `0.412 ±0.009` | `Rf* 0.43` ⓘ | `0.087` | `—` not quantified | `▮▮▮▮▮▮▮▮▮▯▯▯` 25/32 | EMG | ⟶ |
| 3 | R | `0.00–0.42` streak | `—` | `—` | `—` not quantified | `—` | streak | ⟶ |

Every cell carries a **ProvenanceChip** when its value is `chosen` or `inferred`: a dotted underline plus a superscript glyph (`ᶜ` chosen, `ⁱ` inferred), with a hover that states what was assumed and what observation would replace it. `measured` gets no chip — the absence of decoration is the signal, so the decorated cells stand out.

**Band correspondence matrix.** This is what a chemist actually reads a TLC plate for, and it deserves its own component rather than being left to eyeballing.

```
Rst        S      co      R      sd     match tolerance ±0.02 (combined intervals)
─────────────────────────────────────────
1.00       ·      ●       ·      ◆      reference
0.63       ·      ●       ●      ·      consistent (Δ 0.004)
0.41       ●      ●       ○      ·      SM: strong in S, marginal in R  ⓘ
0.18       ·      ·       ▨      ·      L3 streak — not matched
0.00       ●      ●       ●      ·      origin residue
```

Glyphs: `●` confirmed, `○` marginal, `·` absent, `▨` not quantified, `◆` reference. Each row hovers to show the per-lane Rst with intervals and whether the match is within combined uncertainty.

Fixed caption under the matrix, always present:

```
Same Rst is not the same compound. Two lanes agreeing on a position is evidence of
co-migration, nothing more. The co-spot lane is the only lane where co-migration is
designed to be informative.
```

**Findings panel.** The Phase 9 fixed hypothesis register, rendered in full — including the hypotheses that produced nothing. Suppression is only honest if the chemist can see what was suppressed.

```
Hypothesis register — 14 tested, FDR controlled at 0.10, 0 claimed

  ✓ claimed      (none)
  ○ not claimable  Conversion vs reaction time            n=7,  needs ~25 across ≥3 conditions
  ○ not claimable  SM band area vs catalyst loading       n=4,  photometry refused on 3 of 4
  ○ not claimable  New band at Rst 0.63 vs temperature    n=7,  confound: plate batch changed
  … 11 more

  At α=0.05 across 14 hypotheses you would expect 0.7 findings by chance alone.
  This is why nothing appears above until the register clears the FDR budget.
```

**Refusals panel.** One `RefusalCard` per refusal (§11.4), rendered with the same visual weight as a result card — same border, same type scale, no red, no icon of a warning triangle.

---

### (d) Comparison view — `/compare?runs=a,b,c`

**Purpose.** Time series and condition series. "Is the SM going away?"

**Anchoring.** All included runs are placed on a **common origin-anchored axis**: origin at 0, the standard-lane reference band at Rst 1.00. A run whose standard lane has no usable reference **cannot** be co-plotted and is shown in the strip greyed, in place, with a one-line reason and a link — never dropped silently, because a silently dropped timepoint changes the shape of a trend.

**Layout — three stacked panes sharing one vertical axis.**

1. **Filmstrip.** Each run's selected lane (default `R`) resampled onto the common axis, side by side, with the run's date under it. Selecting a different lane per run is allowed and is labelled per column.
2. **Overlaid densitograms.** Up to six traces; dash pattern is the primary distinguisher, direct end-of-line labels, hue redundant (§11.6). A "ridgeline" offset mode for when overlap is unreadable.
3. **Track chart.** For a time series, per timepoint:
   - if photometry is permitted on **all** included runs → `SM band area ÷ total band area` with intervals, as a line with points;
   - if photometry is refused on **any** run → the whole track **degrades to a categorical presence track** (`present / marginal / absent / not quantified`) rendered as a row of glyphs. This is the important design move: a refused photometry does not mean an empty screen. Presence/absence over time is still the answer to most reaction-monitoring questions, and it is honest.

Mixing the two on one chart is forbidden.

**Interaction.** Linked hover at a common Rst across all three panes and all runs. Drag on the axis to define an Rst window → a per-run table of integrated area in that window, stamped `manual window — chosen, not a detected spot`.

**Comparability banner** appears above the panes whenever runs differ in anything that makes comparison unsafe:

```
These runs are not directly comparable.
  · Pipeline version differs: 2 runs at 0.9.1, 1 run at 0.9.3.  [ Re-run all at 0.9.3 ]
  · Solvent system differs: "3:1 hex/EtOAc" (2 runs), "4:1 hex/EtOAc" (1 run).
  · Rst is anchored per plate, so it survives a plate-to-plate change. Areas do not —
    band area depends on how much you spotted and how the plate was lit.
```

---

### (e) Review and correction — `/runs/{id}/review`

**Purpose.** Produce the labelled set. This is Phase 6, this is the critical path, and this screen is the mechanism. Target: **≤90 seconds per clean plate.** If it takes five minutes, Gate 6 will not be met and Gate 7 is blocked behind it.

**The methodological problem, and the protocol that solves it.** If the reviewer sees the machine's answer before making their own, they anchor to it, and the "labels" become a measure of how persuasive the machine is. So review is **two passes**, and only Pass A produces the labels used for accuracy.

**Pass A — blind read.** The screen shows: the rectified plate image, the lane grid, and nothing else. No machine spots, no densitograms, no confidence, no flags, no VLM text. The reviewer marks what they see.

- Click in a lane at a migration position → adds a band; drag vertically → sets its extent.
- Arrow keys nudge 1 px, shift-arrow 10 px. `1`–`9` select lane. `n` = next plate.
- Per-lane state buttons: **`No bands in this lane`** (an affirmative negative — absence must be recorded positively or false-positive scoring is impossible), `Streaking`, `Too faint to call`, `Obscured by writing`.
- A "reveal densitogram" button exists, and using it **records the fact on the label** (`aided: true`). Aided labels are usable for error analysis but excluded from the primary accuracy denominator. Do not remove the button; chemists will need it on hard plates and hiding it produces guesses instead of data.

**Pass B — adjudication.** Now the machine's result is revealed as a diff against Pass A.

```
Lane 3 (R)

  Rst 0.63    you ●        machine ● 0.631 ±0.007   25/32     [ accept machine ] [ keep mine ]
  Rst 0.41    you ●        machine ○ 0.409  11/32   marginal  [ machine is right, it's real ]
                                                              [ machine is right, it's not there ]
  Rst 0.18    you —        machine ● 0.183 ±0.011   22/32     ▸ disposition required:
                                                              ( ) false positive
                                                              ( ) real, I missed it
                                                              ( ) can't tell from this photo
```

Every unmatched item requires a disposition before save. `can't tell` is a first-class answer and must be as easy to click as the others; a review UI that makes uncertainty expensive manufactures certainty.

Also corrected in Pass B: lane count, lane labels, annotation band extents, front-line presence, and the sample ID (rendered as an `UnverifiedField`, §11.3.D).

**Persistence.** Saving writes an **immutable label record** — reviewer, timestamps, per-pass durations, pipeline version, aided flag, every disposition. It does **not** modify the run's machine result. Corrections are a separate layer, forever. A run's JSON is what the pipeline produced; the labels are what a human said. Conflating them destroys the only ground truth this project will ever have.

**Double-labelling.** The system assigns a subset to a second reviewer. The second reviewer's Pass A must not display the first's labels, and the UI must not indicate that the plate is a repeat until after Pass A is saved.

**Progress, always visible in the rail:**

```
Gate 6:  24 of 30 plates labelled        ████████████████████░░░░
          8 of 10 double-labelled        ████████████████░░░░░░░░
   inter-reviewer agreement (n=8):  0.81  [0.62 – 0.93]
```

Show the gate. Chemists respond to a visible finish line, and the interval on the agreement figure teaches the honesty norm by example.

---

## 11.3 Displaying confidence and uncertainty

### 11.3.G First, the argument against a single percentage

This system's trustworthiness has **at least five independent axes**, and they fail independently:

1. **Detection** — did the ensemble find this band?
2. **Position** — where is it, ± what?
3. **Photometry** — is intensity meaningful at all, or is the background clipped?
4. **Identity** — is the sample ID / lane label what we say it is?
5. **Scale** — is the position on a real scale (Rst against a reference), a conventional one (apparent Rf), or none?

A single number averages them. A plate with perfect detection, perfect positions, and a fabricated sample ID would score 80% — and the 80% is worse than useless, because the one thing that is wrong is the one thing a chemist will act on. Four further reasons:

- **A percentage invites arithmetic it cannot support.** People will compare 82% on one plate with 79% on another and treat the difference as meaningful. Nothing about the underlying quantity licenses that.
- **The scale does not reach 1.** F6: on the cleanest plate, the maximum agreement for any feature was 0.78. A bar rendered against an implied 100% ceiling systematically reads as mediocre when it is in fact the observed best case. Any bar we draw must carry the decision threshold and the observed ceiling as reference marks, or it lies by framing.
- **An uncalibrated percentage is an accuracy claim**, which non-negotiable #4 forbids until Phase 7 is passed with a reported ECE and interval.
- **Red-amber-green is the wrong semantics.** A chemist reads green as "the reaction worked", not "this was measured well". Confidence must never use the good/bad colour axis. See §11.6.

**Therefore: confidence is never a single number, and it is always rendered in the same visual grammar as the thing it qualifies.** No "confidence column". No gauge. No star rating.

**And: before Phase 7 completes, no probability appears anywhere.** The UI shows the raw agreement tally (`25 of 32`), which requires no calibration to be honest, plus the banner in R10.

---

### 11.3.A Detection agreement — the tally, not the fraction

**Component: `AgreementTally`.** A discrete strip of 32 cells (32 × 3 px, or 4 × 8 in compact rows), filled for each pipeline that found the feature, with a **threshold tick** drawn at the shipped decision point and a faint **ceiling tick** at 0.78 × 32 = 25 where the observed maximum sits.

```
25 of 32   ▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▮▯▯▯▯▯▯▯
                               ▲ threshold (20)   ▲ best ever observed (25)

10 of 32   ▮▮▮▮▮▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯
                     ▲ threshold (20)
```

The discreteness is the point. "78%" invites you to read it as a probability; "25 of 32" invites you to ask *which* 32, and clicking the tally answers that — it expands to the 4×4×2 grid of background radius × background model × threshold, with each cell showing found/not-found, so the chemist can see *whether the disagreement is structured* (e.g. "only the small radii found it" is a very different situation from "scattered").

**The two worked cases the brief asks for:**

| | 25 of 32 | 10 of 32 |
|---|---|---|
| Plate marker | solid ring, 2 px, full opacity | dotted open ring, 40% opacity, 1 px |
| Table placement | in the main body | **below a rule**, in `Below the detection threshold — not counted` |
| Table row rule | 4 px solid left border | 1 px dotted left border |
| Rst | `0.412 ±0.009`, normal weight | `≈0.41`, muted, italic, wider interval |
| Area | quantified (if photometry allowed) | `—` `not quantified` |
| Densitogram marker | filled triangle + interval band + label | hollow triangle, label on hover only |
| Hover text | see below | see below |

Hover, 25/32:

```
25 of 32 processing variants found a peak here.
Position spread across those 25: ±0.008 Rst. Threshold to count is 20.
The 7 that missed it were all at background radius 4 px, which under-fits a broad band.
```

Hover, 10/32:

```
10 of 32 processing variants found a peak here — below the threshold of 20, so it is
not counted as a band. On blank plates built from this plate's own noise, features at
this level appear routinely. That does not mean nothing is there; it means this
photograph cannot tell you.
```

The second sentence is the crux of the whole design: **tie the marginal call directly to the measured null rate.** The chemist does not have to trust our judgement; they are given the fact that made it.

---

### 11.3.B A position with an interval

**Component: `ValueWithInterval`.**

- **On the plate image**, a spot is never a dot. It is a **tick at the fitted centre plus a translucent band spanning the 95% interval, drawn across the lane width, to scale.** The uncertainty occupies real plate distance. A chemist looking at a 3 mm interval band and a 0.4 mm interval band understands the difference without reading a number.
- **In the densitogram**, the interval is a shaded y-range behind the peak marker.
- **In tables**, `0.412 ±0.009`, with the `±` clause at 0.85 em in the muted token.

**Hard formatting rule, enforced in one shared Python formatter that every surface — HTML, CSV, JSON export, print — must call:**

> Round the value so its last displayed digit is one decade finer than the first significant digit of the half-interval. Never print `0.4123 ±0.009`. Never print an interval to more precision than the value.

If the interval cannot be computed (fit failed, streak, single-pipeline detection), print `—` plus a reason chip. **Never print a bare centre with no interval**; a naked number reads as exact.

All numerals use `font-variant-numeric: tabular-nums` so columns align and digits do not shimmer under hover.

---

### 11.3.C Rst vs measured Rf vs apparent Rf

**Component: `ScaleBadge`.** Three visually distinct treatments; Rst is always primary.

| Case | Rendering | Behaviour |
|---|---|---|
| **Rst** (against the standard-lane reference) | `Rst 0.412 ±0.009` — normal weight, no decoration | primary column, sortable, exported by default |
| **Rf, measured front** | `Rf 0.43` with a small solid-rule glyph and the tooltip `front line detected at row 118` | normal weight; appears only when a real drawn front exists |
| **Rf, inferred front** | `Rf* 0.43` — asterisk is part of the label, value in the muted token, dotted-underlined box | **hidden behind an "Advanced scales" disclosure**; not sortable in the run list; excluded from export unless a checkbox is ticked, and ticking it writes the convention string into the export header |
| **No reference band** | `12.4 mm from origin`, with an `ⁱ` provenance chip | flagged; comparable within this plate only |

Mandatory hover on `Rf*`:

```
Apparent Rf — the solvent front is not visible on this plate.
This value assumes the front is at the top edge of the imaged plate. On our own plates,
the same spot has yielded Rf anywhere from 0.34 to 0.97 depending on which defensible
front convention is used. Nothing in this report depends on this number. Use Rst.
```

The friction is deliberate and calibrated: available to someone who knows they want it and knows why, never encountered by accident.

---

### 11.3.D A sample ID the VLM is unsure about

**Component: `UnverifiedField`.** The core move: an unverified read must **look like a draft someone typed**, not like a measurement. It is rendered with form-field chrome — dotted underline, a caret affordance, an `unverified` pill — not as body text.

```
Sample ID   MEHQ-14 ⌇ unverified                       [ confirm ] [ edit ]
            ⌐ read by model, 3 of 5 samples agreed
              other readings: MEHQ-1A (2 of 5)
```

Rules:

- **Show the disagreement, always.** If the 5 self-consistency samples split, list every distinct variant with its count, ordered by count. On a 3/2 split the majority is **not** given extra visual weight — a 3/2 split is close to a coin flip and typography must not pretend otherwise. On 5/5 the alternatives row is omitted and the pill reads `unverified · 5 of 5 agreed`.
- **All samples `UNREADABLE` → the field reads `Not read`** with a `Type it in` button. Never an empty string. Never a guess. Never a placeholder that looks like a value.
- **Confirmation is a first-class event.** On confirm, the pill becomes `confirmed by AK · 24 Aug`, the dotted underline is removed, and the record gains a label entry. Only confirmed IDs appear in the primary group of search results.
- Lane labels use the same component but succeed far more often (closed vocabulary); an operator-entered lane label is shown as `confirmed by operator` and the VLM's read is shown beside it only if they disagree.

---

### 11.3.E A lane the system refuses to quantify

The data is not hidden. It is **marked as not being a measurement**, in a way that survives a screenshot.

- The lane's **densitogram panel is cross-hatched at 45°** in the neutral structural token, with the trace still visible underneath.
- The **lane strip on the plate image** gets the identical hatch pattern, so the two are unmistakably the same statement.
- The **reason sits directly under the lane header**, not in a distant flags panel. Proximity is the whole point.
- In the spot table, the lane's rows show `—` for area, a `not quantified` chip, and for a streak a **single row** reading `Streak, Rst 0.00 – 0.42 — no discrete band position`, rather than fabricated per-band rows.
- Hover on the hatch anywhere gives the same sentence. There is no way to interact with the panel that yields a number.

---

### 11.3.F The run-level summary: `CapabilityBar`

Four independent segments. Each carries **fill state + hatch pattern + a word**. Colour is the fourth, redundant channel.

```
Positions   ██████  measured
Photometry  ▨▨▨▨▨▨  refused — 41% of the plate is clipped
Identity    ███░░░  unverified — model read, not confirmed by a person
Scale       ██████  Rst — no solvent front, so Rf is not reported
```

States, per segment: `██ measured/confirmed` · `▨▨ refused` · `██░░ partial/unverified` · `░░ not attempted`. Each segment links to the relevant refusal card or `/method` anchor. This component appears identically in the run list row, the result view rail, the print header and the comparison strip — one grammar, learned once.

---

### 11.3.H The uncertainty of absence

An empty lane must distinguish "nothing there" from "we could not tell". Every lane with zero confirmed bands carries:

```
No bands detected in lane 2.
At this exposure the faintest band that would have registered is about 0.03 OD —
roughly a spot you would just barely see by eye under the lamp. Anything weaker
would not appear here.
```

The detection limit comes from the lane's σ and the shipped threshold. Reporting it turns a null result into a bounded statement, which is the difference between a useful negative and no information.

---

## 11.4 Refusals

### 11.4.1 Principles

1. **A refusal is a result, not an error.** Same card, same border, same type scale, same slot in the layout as a successful result. No toast. No modal. No empty state with a shrugging icon.
2. **Never red.** Red is reserved exclusively for *system faults* — upload failed, pipeline crashed, storage unreachable. Those are our problem. A refusal is the system working correctly, and rendering it in the fault colour teaches the chemist that the honest path is the broken path. Refusals use the neutral structural token with a left rule and a hatch corner.
3. **Four parts, always in this order.** *What we did measure* (never zero — positions usually survive when photometry does not) → *what we will not report* → *why, with the measured number and the threshold* → *what to change about the photograph, physically*.
4. **Show, don't only tell.** Every refusal that concerns the image carries a thumbnail with the offending region marked (clipped pixels magenta, overrun edge highlighted, streak lane hatched).
5. **No "try anyway" for photometry.** An override produces a number that will be screenshotted and quoted. Offer instead `Show the raw densitogram (not a measurement)` — inspection, clearly labelled, non-exportable.
6. **Always re-testable at the bench.** Every image-related refusal has `Retake and compare`, which opens `/upload` pre-filled with the same metadata and shows the previous QC numbers beside the live ones.
7. **Tone rules.** No apology words ("Sorry", "Unfortunately", "Oops"). No exclamation marks. No blame ("your photo was bad"). No hedging adverbs. Always a number. Always end on a physical action. Never use the word "error".

### 11.4.2 The microcopy

Each block is the literal copy. Values in `{braces}` are interpolated from the refusal object.

---

**R1 · `CLIP_PHOTOMETRY` — over-exposure, photometry refused**

```
Photometry refused — the photograph is over-exposed

{41}% of the plate is at the sensor's maximum in the green channel. Where the background
is clipped we cannot know how bright it should have been, so optical density is not
computable there — and clipping happens where the plate is brightest, so the error does
not average out.

  Clipped pixels   {41}%        Limit for photometry   {8}%

Still reported: band positions in lanes {1–4}. Positions do not depend on the brightness
scale, so they are unaffected.

What to change: retake with less light reaching the sensor. On a phone, tap the plate to
focus, then drag the exposure slider down about two stops. On a camera, set −1.5 to −2 EV.
The photograph should look slightly too dark to your eye. Keep the lamp at the same
distance and do not use flash.

[ Retake and compare ]   [ Show the raw densitogram (not a measurement) ]
```

---

**R2 · `NO_FRONT_RF` — no solvent front**

```
Rf not reported — there is no solvent front on this plate

Rf is the distance the compound travelled divided by the distance the solvent travelled,
and the solvent front is not drawn here. Any Rf we printed would be a guess about where
the front was. On our own plates that guess has moved the same spot between 0.34 and 0.97.

Reported instead: Rst — position relative to the reference band in the standard lane on
this same plate. Rst is comparable between lanes here, and between plates run in the same
solvent system.

What to change: mark the solvent front in pencil as soon as the plate comes out of the
tank, before it dries, and get both the origin line and the front inside the frame.
```

---

**R3 · `FRAME_OVERRUN_ORIGIN` — plate cut off**

```
Origin not in frame — positions cannot be anchored

The plate continues past the bottom edge of the photograph for {62}% of its width, so the
origin line is not visible. Every position we could report would depend on where we
guessed the origin was.

What to change: retake with the whole plate inside the frame and about a centimetre of
dark background on all four sides. Stand back and crop afterwards rather than filling
the frame.

[ Retake and compare ]
```

---

**R4 · `STREAK_LANE` — streaking lane, inline on the lane, not in a global panel**

```
Lane {3} not quantified — this is a streak, not a band

Material is smeared continuously from the origin to Rst {0.42} rather than sitting in a
band. A streak has no single position, so any Rf, Rst or area we printed for it would be
invented.

Reported instead: the extent of the streak, {0.00–0.42}. That is the most that can
honestly be said about it.

What to change: if this is overloading, spot less material or dilute ten-fold. If the
compound is acidic or basic, add 0.5–1% acetic acid or triethylamine to the mobile phase.
```

---

**R5 · `ID_UNREADABLE` — sample ID not read**

```
Sample ID not read

The handwriting is about {6} pixels tall in this photograph. Text that small is below what
any reader we have can resolve — including a person looking at this image at full
magnification — so we have not guessed.

Sample ID   [                    ]   Not read      [ save ]

What to change: photograph the label separately, close up, before you shoot the plate. A
second photo of just the header costs nothing and fixes this permanently.
```

---

**R6 · `NO_PLATE_FOUND` — nothing recognisable**

```
No TLC plate found in this photograph

We look for a bright rectangular region with straight edges. This image does not contain
one, or the plate fills so much of the frame that its edges fall outside it.

What to change: check that this is the file you meant to upload. If the plate is in there,
retake it against a dark background with all four corners visible.
```

---

**R7 · `NO_REFERENCE_RST` — no reference band**

```
Rst unavailable — no reference band in the standard lane

Rst is measured against the reference band in the standard lane. Lane {4} ({sd}) has no
band that clears the detection threshold, so there is nothing to measure against.

Reported instead: distance from the origin in millimetres. That is comparable within this
plate only — not between plates, and not between solvent systems.

What to change: run a standard alongside. If the standard is there but faint, hold the
plate under the lamp a little longer before photographing, or spot more of it.
```

---

**R8 · `BELOW_DETECTION` — an honest negative**

```
No bands detected in lane {2}

Nothing in this lane reaches the detection threshold. At this exposure the faintest band
that would have registered is about {0.03} OD — roughly a spot you would just barely see
by eye. A band weaker than that would not appear here, so this is not the same as
"nothing is present".
```

---

**R9 · `ENSEMBLE_DISAGREEMENT` — spot table withheld**

```
Spot list withheld — the processing methods do not agree on this plate

Of 32 defensible combinations of background model, background radius and threshold, no
more than {9} agreed on any single band. Disagreement at that level usually means the
lighting across the plate is uneven, not that the chemistry is ambiguous.

The densitograms are drawn below so you can look at them. We are not going to publish a
band list from them.

What to change: move the plate to the centre of the lamp's field so the illumination is
even, or take two photographs with the plate rotated 180° and upload both — if the same
bands appear in both, they are real.

[ Retake and compare ]   [ Show the densitograms (not a measurement) ]
```

---

**R10 · `UNCALIBRATED_CONFIDENCE` — global banner, shown until Gate 7 passes**

```
Confidence probabilities are not available in this build.

What is shown instead is the raw ensemble agreement — how many of 32 processing variants
found each band. That count needs no calibration to be meaningful.

A calibrated probability requires a set of human-confirmed plates. {24} of the {30} needed
are done.                                                    [ Help label plates ]
```

---

**R11 · `NOT_CLAIMABLE_N` — a correlation that will not be claimed**

```
Not claimable at n={7}.

To tell an effect of the size you would care about apart from noise, this comparison needs
roughly {25} plates spanning at least three conditions. It is being tracked and will appear
here when there are enough.

Also blocking: {plate batch changed between the two groups, which is confounded with the
variable you are asking about}.
```

---

**R12 · `BATCH_PARTIAL` — batch upload with failures**

```
{4} of {11} photographs will not support photometry.

Band positions will still be reported for all of them. You can upload everything and
accept that, or retake those four now while the plates are still on the bench.

[ Upload the 7 that passed ]  [ Upload all 11, flagged ]  [ Show me what's wrong with each ]
```

---

**R13 · `NO_CALIBRATION_CONVERSION` — the "% conversion" refusal**

```
Not a conversion percentage.

Fluorescence quenching is not linear in how much material is present, and no calibration
series has been run for this compound. What is plotted is the ratio of two band areas on
the same plate. It will move in the same direction as conversion, but it is not a
percentage of conversion and should not be quoted as one.

What to change: to get a real number, spot a dilution series of the standard on the same
plate and we can fit a response curve for it.
```

---

**R14 · `MIXED_PIPELINE_VERSIONS` — comparison banner**

```
These runs were processed by different pipeline versions ({0.9.1} and {0.9.3}), which
changed {the background model}. Differences between them may come from the software rather
than the chemistry.                                    [ Re-run all at {0.9.3} ]
```

---

### 11.4.3 Where the copy lives

**The backend emits a code and numbers; the frontend owns the sentence.** One module, `frontend/copy/refusals.py`, maps `code → {title, body_template, remedy, actions[]}`. This is testable (every code has copy; every template's placeholders are satisfiable from the refusal object's fields), reviewable by a chemist in one file, and prevents the pipeline from accumulating user-facing prose. A CI test asserts that every refusal code the backend can emit has copy, and that no copy contains the words `error`, `sorry`, `unfortunately`, `failed to`, or `!`.

---

## 11.5 The densitogram chart

### 11.5.1 Orientation

**Migration is the vertical axis, increasing upward. Optical density increases to the right, with the baseline at the left of each panel.**

Justification: the chart is pixel-registered to the plate image sitting immediately to its left, and the plate is displayed as it ran — origin at the bottom, solvent front at the top. Adopting the scanner convention (Rf horizontal) would require rotating the plate image 90°, which breaks the spatial intuition every bench chemist already has. Registration with the physical object beats convention with an instrument nobody in this lab owns.

The horizontal presentation is available as a toggle (`Rotate`, persisted in `localStorage`) and is the **default in the comparison view and in print**, where several lanes stack better horizontally.

### 11.5.2 Axes

**y — migration.**
- Primary: `Rst`, 0 at the origin line, 1.00 at the standard-lane reference band, extending past 1.0 where the plate does. Ticks at 0, 0.25, 0.5, 0.75, 1.00, then every 0.25.
- Fallback when there is no reference band: `mm from origin`; the axis title changes and gains the `ⁱ` provenance chip; the axis is drawn in the muted token so the degradation is visible at a glance.
- The axis is **shared, in the literal sense**: a single `MigrationScale` object owns `rst → px` and is consumed by the plate canvas, every densitogram panel, the ruler and the comparison filmstrip. Registration cannot drift because there is only one mapping.

**x — optical density.** `OD = log10(I0/I)`. Zero at the panel gutter. Never inverted. Never transmittance. Never Kubelka–Munk (F5).
- Default: **common OD scale across all lanes on the plate**, because comparing band strength between lanes is the point of looking at four lanes at once.
- Per-lane autoscale is a toggle, and when it is on, every panel header gains `autoscaled — heights not comparable between lanes`.

### 11.5.3 Marks, in draw order

| # | Mark | Rendering |
|---|---|---|
| 1 | **Noise band** | filled region from OD 0 to OD = kσ, low-contrast neutral, drawn *behind* everything. Its outer edge is a solid 1 px rule labelled `3σ`. A second, lighter rule at 5σ. **Always drawn, even when the trace never reaches it** — that is the most informative case there is. |
| 2 | **Background model** | thin dashed line, shown only in the "pre-subtraction" view (a toggle). Default view is post-subtraction with the background at zero. The model is never hidden from someone who asks. |
| 3 | **Trace** | 1.5 px solid stroke, min/max envelope decimated server-side to ≤1200 samples. Extrema preserved; **never mean-smoothed** — if smoothing changes the band count, the band count was never real (anti-pattern #2). |
| 4 | **EMG fit** | thin dashed overlay in a second stroke style, drawn only when `fit_residual` is within tolerance. When it is not, the fit is omitted and the peak is chipped `shape not modelled`. Seeing the fit fail is more useful than seeing a fit that does not fit. |
| 5 | **Integration region** | 45° hatch between trace and baseline, bounded by tick marks at the integration limits. Limits are draggable **only in the review screen**. |
| 6 | **Peak markers** | confirmed: filled triangle at the fitted centre pointing at the trace, plus a translucent band spanning the 95% position interval, plus a leader to a label `Rst 0.412`. Marginal: hollow triangle, 40% opacity, label on hover only. VLM-proposed-unconfirmed: small open circle on the axis with a dotted leader. |
| 7 | **Origin rule** | solid, 1.5 px, full width, label `origin (measured)` — or `origin (operator-set)` with the `ᶜ` chip. |
| 8 | **Front rule, measured** | solid, 1.5 px, label `front (measured)`. |
| 9 | **Front rule, inferred** | **dashed 6-4, 1 px, 60% opacity**, with a fade band spanning the plausible range, label `front (inferred — not visible on this plate)`. The region above it is given a subtle diagonal hatch. Fixed caption: `Shown for orientation only. No reported number depends on this line.` |
| 10 | **Reference band** | solid rule with a diamond glyph, `reference (sd lane) · Rst 1.00`, drawn in **every** lane panel so the anchor is always in view. |

The measured/inferred distinction is carried by **stroke style first** (solid vs dashed), opacity second, label third. It survives greyscale printing and it survives a screenshot pasted into a slide.

### 11.5.4 Overlaying multiple lanes

Up to six traces per panel. Distinguished by, in priority order:
1. **Dash pattern**, from a fixed ordered set of six patterns — the primary channel;
2. **Direct end-of-line labels** — no legend lookup, ever;
3. **Hue** from the Okabe–Ito ramp — redundant only.

The noise band is drawn for the **focused** trace only (otherwise the panel becomes mud); a compact table beside the chart lists σ per lane. A **ridgeline offset mode** stacks traces along the OD axis with a visible zero baseline per trace, for when overlap is unreadable.

### 11.5.5 Interaction

| Action | Behaviour |
|---|---|
| **Hover** | horizontal crosshair at the pointer's Rst; a readout chip pinned to the axis showing `Rst 0.412 · OD 0.087 · 4.1σ`. **Linked highlight:** the same Rst is drawn as a thin horizontal line across all lanes on the plate image, and the matching spot-table row highlights. |
| **Reverse hover** | hovering a spot-table row or a marker on the plate image highlights the corresponding peak and draws the plate line. Bidirectional, always. |
| **Click a peak** | selects it. Selection persists, scrolls the table to it, and loads its full detail (agreement tally grid, interval, σ support, provenance) into the left rail. Written to the URL as `?spot=L2-3` so it can be shared. |
| **Zoom** | vertical-only by wheel / pinch / axis drag-select; **the plate image zooms in lockstep** through the shared transform. OD-axis zoom is a separate control, because it is a different intent. Double-click resets. **Zoom may never scroll the noise-band edge out of view** — it is clamped into frame. |
| **Brush** | drag on the axis to define an Rst window → a panel with integrated area per lane in that window, stamped `manual window — chosen, not a detected band`, non-exportable as a spot. |
| **Keyboard** | `↑`/`↓` step between peaks, `[`/`]` between lanes, `Esc` clears selection, `f` toggles fit overlay, `b` toggles background view. |
| **Touch** | on the bench tablet, hover is replaced by a draggable ruler handle on the axis; all hover-only affordances have a tap equivalent. |

### 11.5.6 Implementation and export

- Trace and marks: SVG via Observable Plot. Plate image, OD map and clip mask: canvas layers beneath, under the shared transform.
- Performance budget: initial render of a 4-lane plate ≤ 120 ms; pan/zoom at 60 fps; total island JS ≤ 120 KB gzipped.
- **Export bakes provenance into the image.** Every chart exports SVG and 2× PNG with a caption block rendered into the file: run ID, date, lane, σ value, scale type (Rst / Rf measured / Rf inferred), pipeline version, and any active refusal. A chart that leaves this application without its caption is a future misquotation, and the only reliable prevention is to make the caption part of the pixels.

---

## 11.6 Accessibility and colour

### 11.6.1 The green rule

**The plate is green. Therefore nothing in the UI is green.** No green buttons, no green "pass" states, no green chart strokes, no green in the capability bar. Two reasons: a green chrome element adjacent to a green plate is read as plate signal; and green paired with red — the obvious alternative — is the exact collision that deuteranopes cannot resolve. Removing green from the chrome solves both problems at once and costs nothing.

### 11.6.2 Palette

Defined once as CSS custom properties in `:root`, redefined for `[data-theme="dark"]`, `@media (prefers-color-scheme: dark)`, `@media (prefers-contrast: more)` and `@media print`. No colour is ever written as a literal outside the token block.

| Token | Role | Notes |
|---|---|---|
| `--surface`, `--surface-raised`, `--ink`, `--ink-muted` | neutral slate/paper base | carries 90% of the UI |
| `--accent` | interactive affordance only | blue-violet; never used for data |
| `--attention` | unverified / needs a human | amber; paired always with the `⌇` glyph |
| `--structural` | refusals, hatches, not-quantified | neutral grey-blue. **Refusals are never red.** |
| `--fault` | system faults only | desaturated red. Upload failed, pipeline crashed. Nothing else. |
| `--series-1…8` | Okabe–Ito CVD-safe categorical | redundant channel only; dash pattern is primary |
| `--od-ramp` | **cividis** | perceptually uniform *and* designed for CVD, so a deuteranope reads the same ordering. **Never jet, never rainbow** — they invent edges that look like bands. |

### 11.6.3 Never hue alone

Every stateful element carries **at least two** of: shape, fill pattern, stroke style, position, text label. The complete mapping, enforced by a Playwright test that renders each state in a greyscale filter and asserts distinguishability:

| State | Shape | Pattern / stroke | Text | Hue |
|---|---|---|---|---|
| Confirmed band | filled triangle / solid ring | solid 2 px | `Rst …` | ink |
| Marginal | hollow triangle / dotted ring | dotted 1 px, 40% | count on hover | ink-muted |
| VLM proposed, unconfirmed | open circle | dotted leader | `proposed` | ink-muted |
| Not quantified | — | 45° hatch | `not quantified` | structural |
| Refused | — | corner hatch + left rule | full card | structural |
| Unverified identity | `⌇` glyph | dotted underline | `unverified` | attention |
| System fault | `✕` | solid left rule | `…failed` | fault |
| Measured value | — | none | — | ink |
| Chosen value | `ᶜ` | dotted underline | tooltip | ink |
| Inferred value | `ⁱ` | dotted underline | tooltip | ink-muted |

**Do not rely on opacity alone** for the recessive states — at 40% on a projector or a sunlit bench tablet they vanish. Opacity is always paired with a dotted stroke and a text chip.

### 11.6.4 The OD map

Always accompanied by a **colourbar with numeric ticks in OD units**, and an **isoline overlay toggle** drawing contours at fixed OD steps. With isolines on, magnitude is readable with no colour perception at all. The colourbar's domain is printed numerically (`0.00 – 0.34 OD`) because an unlabelled ramp is an unfalsifiable picture.

### 11.6.5 The rest

- **Contrast:** text ≥ 4.5:1; chart strokes and glyphs ≥ 3:1 against panel background. The noise-band *fill* may be low-contrast; its threshold *rule* may not.
- **Keyboard:** complete operation of the run list, result view and review screen without a pointer. The plate canvas is never the only route to a spot — a focusable spot list with arrow navigation shadows it. Visible focus rings in both themes. Skip links.
- **Screen readers:** every chart has a generated `<figcaption>` ("Lane 2 densitogram. 3 confirmed bands at Rst 0.18, 0.41, 0.63. Noise threshold 0.006 OD. Photometry refused for this plate.") and is `aria-describedby` the spot table, which is the real accessible representation. Do not aria-label individual path segments; do not sonify.
- **Motion:** no animated transitions on data marks, ever — a moving peak reads as a changing measurement. Animation only on disclosure. `prefers-reduced-motion` removes even that.
- **Dark mode is required, not optional.** These plates are viewed in a darkened UV cabinet room. Both themes are complete token sets. The plate imagery is **never re-tinted by the theme**, and the image panel keeps a fixed neutral surround in both themes so the plate's perceived contrast does not shift between them.
- **Print:** a stylesheet producing a one-page result summary — plate image, capability bar, spot table, refusals, provenance footer — because that page gets stapled into a lab notebook. Charts print as vector. Because dash and hatch carry all meaning, greyscale printing loses nothing.
- **Numerals:** `font-variant-numeric: tabular-nums` everywhere a number appears.

---

## 11.7 What not to build

Each of these will feel like the obvious next thing. For a five-chemist internal tool they are a waste of the build's budget, and several are actively harmful.

**Actively harmful — do not build these even if asked:**

1. **A user-facing threshold slider** ("drag σ and see what happens"). This is the single most tempting and most damaging feature in the list. It converts an honest ensemble into a p-hacking instrument: a chemist who wants a spot will find a threshold at which it exists. If method development genuinely needs it, it lives behind a flag in a separate `/method-dev` area, its outputs are stamped `exploratory`, and they are not exportable and not storable as a run.
2. **An in-app image editor** — crop, rotate, brightness, contrast, "auto-enhance". Any client-side pixel manipulation upstream of photometry is F15 in a friendlier costume. The only permitted transform is exact 90° rotation, performed server-side and recorded as an operation in the provenance.
3. **An "AI summary of this plate" headline.** The VLM's prose comment may appear, clearly labelled as a draft, below the numbers. It may never be the first thing on the screen, and it is never stored as a result field.
4. **A chat interface over a run.** It produces fluent numbers from a model, which is non-negotiable #1 with a text box in front of it.
5. **A pipeline configuration admin UI.** Config lives in a version-controlled file. A UI for it lets someone change a validated method without a commit, which destroys the audit trail the whole build exists to create.
6. **Editable run history / "undo" on a run.** Runs are immutable; a re-run is a new row. A mutable measurement record is not a record.

**Simply not worth it at this size:**

7. Accounts, roles, permissions, an invite flow. Five users behind the lab VPN; a reviewer name in a cookie is the entire identity model.
8. Real-time collaboration, presence cursors, WebSockets. HTMX polling a status partial every 2 s, stopping at a terminal state, covers everything.
9. A PDF report generator. The print stylesheet does this; a PDF pipeline is a subsystem with fonts, page breaks and a headless browser.
10. A component library of our own, Storybook, or a design system. Forty classes.
11. Internationalisation. One language, one lab.
12. Offline/PWA mode, service workers, background sync. The bench is on the wifi.
13. Email digests, Slack integration, a notification centre. If batch-completion notification is genuinely wanted later, it is one outbound webhook, not a subsystem.
14. Infinite scroll and virtualised tables. Paginate; chemists navigate by date.
15. A dashboard builder, configurable widgets, saved layouts. URLs are the saved layouts.
16. Mobile-first responsive design. Support desktop and tablet landscape (the bench device). Phones get the upload screen only, which is the only thing anyone will do on a phone.
17. A spot-detection CNN demo tab, an embeddings view, a t-SNE of plates, a "gallery wall" 3D visualisation. No dataset (F12), no purpose.
18. An animation library. `transition: opacity 120ms` is the whole motion system.
19. Full-text search over the research PDFs. That is what the filesystem is for.
20. Theming beyond light/dark/high-contrast/print.

---

## 11.8 Gate 11, operationalised

Gate 11 says a chemist who has not seen the system can upload a plate, judge from the screen alone whether the result is trustworthy, and correct a wrong spot, unaided. Make that testable:

Recruit one chemist who has not been involved. Give them a plate, the URL, and nothing else. Do not narrate. Record the screen. Five tasks, each with a pass condition:

| # | Task | Passes if |
|---|---|---|
| 1 | Photograph and upload a deliberately over-exposed plate | they notice the QC card and retake **before leaving the bench**, unprompted |
| 2 | Open the resulting run and say whether they would act on the numbers | they say photometry is refused, without being shown the flags panel |
| 3 | Point at a band the system is unsure about and say why | they cite the agreement tally or the below-threshold section, not the colour |
| 4 | State the position of a band and say what it is relative to | they say Rst and mention the reference band — **if they say "Rf", §11.3.C has failed** |
| 5 | Correct a wrong spot | they complete Pass A and Pass B and save, in under 3 minutes, without asking what Pass A is for |

Every hesitation, wrong click and question goes into `mistakes.md` at the moment it happens, with the timestamp in the recording. Two failed tasks means the screen is redesigned, not the chemist retrained.

---

## 11.9 Component inventory

| Component | Type | Used on |
|---|---|---|
| `NumberFormatter` | Python module — the only path to a rendered number; enforces sig-fig-to-interval | everywhere, including CSV and print |
| `MigrationScale` | TS module — sole owner of `rst → px`; consumed by every registered surface | result, comparison, review |
| `CaptureQCCard` + `ClipMaskThumb` | island | upload |
| `CapabilityBar` | template partial | run list, result rail, print header, comparison strip |
| `AgreementTally` | template partial, expands to the 4×4×2 grid | spot table, spot detail |
| `ValueWithInterval`, `ProvenanceChip`, `ScaleBadge` | template partials | spot table, matrix, exports |
| `UnverifiedField` | partial + HTMX post | result, review, run list search groups |
| `PlateStack` | island — layered canvas | result, comparison, review |
| `Densitogram` | island — Observable Plot + shared transform | result, comparison |
| `SpotTable`, `BandMatrix`, `FlagList` | server-rendered | result |
| `RefusalCard` | server-rendered from `refusals.py` | everywhere a refusal can occur |
| `FindingCard` | server-rendered | result, comparison |
| `ReviewCanvas` (Pass A) / `AdjudicationDiff` (Pass B) | island + form post | review |
| `ComparisonStrip`, `TrackChart` | island | comparison |
| `ProvenanceFooter` | server-rendered | result, print, every chart export |

---

## 11.10 The one-sentence test

Before any screen ships, apply this: **can a chemist screenshot any part of it, paste it into an email with no caption, and have the recipient reach a correct conclusion about how much to trust it?**

If a number can be cropped away from its interval, its provenance chip, or its refusal — the layout is wrong. That is why intervals are inline rather than in a column, why refusals sit in the result slot rather than in a sidebar, why chart exports carry a baked caption, and why the scale badge is part of the value rather than a header.