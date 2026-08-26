# TLC build spec — image-processing pipeline (phases A–F + E graph)

Implements §4–§8 of `TLC_Build_Spec.md` as the spec's `tlccore/` package, run on the
7 unique MEHQ plates. Phases G (rules engine), H (dashboard/PDF), the FastAPI server
and SQLite layer are **not** built — this is the image-processing half only.

## What is implemented

| Phase | Module | Spec section |
|---|---|---|
| A GATE | `gate.py` | §4 — green dominance, edge density, dark content, lane evidence, resolution cap |
| B NORMALIZE | `normalize.py` | §5 — find_plate → warp → 700 px standardise; orientation; OD + MAD noise |
| C STRUCTURE | `structure.py` | §6 — header band, origin dots, pencil lines from gutters, lanes |
| D LANE ROLES | `lanes.py` | §7 — lexicon-first ladder + structural cross-check |
| E GRAPH | `graph.py` | §8-preamble — pixel-faithful span / Rf axis / vmax |
| F EXTRACT | `extract.py` | §8 — profiles, sub-pixel multi-Gaussian, SNR tiers, streaks, stacking, bleed, Rs, width model |
| §9 confidence | `confidence.py` | quantised 0.05 + hysteresis, weakest-link propagation |
| §10 trace | `trace.py` | per-phase inputs/outputs/decision/reasons |
| orchestrator | `pipeline.py` | `analyze(image, run_dir, plate_id)` |

## Run

```bash
python -m venv venv && ./venv/bin/pip install numpy scipy opencv-python-headless \
    pillow scikit-image matplotlib pydantic
./venv/bin/python render.py            # writes figures/
```

Per-plate: `python -c "from tlccore.pipeline import analyze; print(analyze('plate.png','run1',plate_id='P33'))"`

## Verified invariants
- **Determinism**: same image twice → byte-identical `result.json`. Confirmed.
- **Only two hard stops**: NOT_A_PLATE / NOT_WORKABLE, decided at the gate. A synthetic
  chart and a portrait-like image are both rejected on `green_dominance`.
- **Confidence on everything**: every spot carries named factors; no naked numbers.
- **Trace**: 6 sections per run in `runs/<plate>/trace.json`.
- **Graceful degradation**: every plate emits a `cannot_conclude` list with bench fixes.

## Deviations from the spec, and why (all measured, not guessed)
1. **§5.B3 noise floor** — MAD on the *clipped* OD map is exactly 0 (clipping removes the
   negative half of the background distribution), which zeroes every threshold. Noise is
   estimated on the **signed** OD map instead.
2. **§4 lane_evidence** — the x-projection alone returns 0 peaks on P31 (its reference lanes
   are very lightly loaded) and would reject an analysable plate. Now accepts *either*
   projection peaks *or* spotting dots.
3. **§6.C3 dot row** — the spec's "bottom 30%" is not sufficient when lane labels are written
   *on* the plate: both the spotting row and the label row are colinear and wide. Rows are
   separated by **fill ratio** (dots 0.70–0.81, glyphs 0.48–0.66) — measured on all 7 plates.
4. **§12.6 spot band = 0.28h** — correct for a 2-line header, wrong for P32b's 3-line header
   (reaches 0.38h). The header block is now **measured** (mean error 0.049h) and 0.28h is a floor.
5. **§8.F2 tier sigma** — σ from the profile baseline collapses on a clean lane, so noise bumps
   cleared 3σ (7 phantom spots in P33's Sd lane). σ is floored at the theoretical lane-mean
   noise, `plate_noise / sqrt(columns)`.
6. **Added** `find_plate` border trim (a dark crop edge reads as OD 0.36 and its inflated MAD
   hides every real lane peak) and regular-pitch lane completion, gated on OD support and
   flagged `needs_user_confirmation`.
7. **Tried and reverted**: a per-spot stroke-compactness veto for handwriting. Streaks are also
   stroke-like, so it deleted 3 of 4 real spots on P29. Handwriting is separated by position only;
   features within 0.08h of the header band get a `near_text_band` flag instead.

## Not reachable from these images
- **Solvent front**: never found on any plate (evaporated before the photo) → `front_source=none`
  on all 7, true Rf disabled, positions reported as relative height. This is the spec's
  `no_front_line` cannot-conclude, and it needs the SOP fix (pencil the front).
- **Origin**: `virtual` (from the dot row) on 6 of 7 plates → confidence capped per §10.
