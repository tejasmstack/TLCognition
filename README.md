# TLC plate readout

A deterministic measurement pipeline for TLC plate photographs, with a semantic (VLM) layer that is
allowed to read handwriting and lane labels but is never allowed to produce a number.

The system's contract, in five lines:

1. No user-visible number comes from a non-deterministic component.
2. Every value is tagged `measured`, `chosen`, `inferred` or `refused`.
3. A refusal is a value — code, one-sentence reason, remedy — never a null or a zero.
4. No accuracy claim exists without human labels. There are none yet, so there are none.
5. Any historical run re-executes byte-for-byte at its recorded version.

What it currently reports and what it does not is in **[EVALUATION.md](EVALUATION.md)** — including
every metric that cannot be computed, and why.

## Quickstart

```bash
uv sync                                   # Python 3.12.13, pinned by uv.lock
./scripts/ci.sh                           # ruff + the full test suite (network blocked)
uv run uvicorn tlc.api.app:app --reload    # http://127.0.0.1:8000/upload
```

Upload a plate photograph, enter the lane count and labels (`S, R, co, sd`), and the result screen
shows band positions with intervals, the ensemble agreement tally behind each one, refusal cards for
anything withheld, within-plate findings, and an assumptions-and-limits panel.

### Command line

```bash
uv run python -m tlc.cli.main run plate.png --n-lanes 4 --labels "S,R,co,sd"
uv run python -m tlc.cli.main list
uv run python -m tlc.cli.main replay <run_id>      # must reproduce result_sha256
uv run python -m tlc.cli.main export <run_id>      # the stored result JSON, verbatim
```

`TLC_DATA_DIR` (default `data/`) holds the SQLite database, the content-addressed blob store, the
per-run OD field (HDF5), the result JSON and the findings JSON.

## What is where

| Path | What it is |
|---|---|
| `tlc/pipeline/` | the deterministic pipeline: geometry, photometry, noise, surrogate nulls, the 24-config ensemble, peaks, streaks, Rst, refusals. Imports only numpy/scipy/skimage — enforced by a test. |
| `tlc/synth/` | the synthetic plate generator with exact ground truth (built before the pipeline, so it cannot flatter it) |
| `tlc/schemas/result.py` | the frozen result contract; every scientific scalar is a `Q` envelope |
| `tlc/insight/` | the pre-registered hypothesis registry, confound panel, statistical gate and the insufficient-data ladder |
| `tlc/calibration/` | the Phase 7 confidence map; refuses below 30 labelled plates, and none are shipped |
| `tlc/vlm/` | provider protocol with null/replay/live modes, response cache, self-consistency aggregation |
| `tlc/labels/` | correction ops, the promoter, reviewer agreement, tune/calibrate/holdout partitioning |
| `tlc/api/`, `tlc/web/` | JSON API and the server-rendered screens |
| `config/` | the immutable, content-hashed pipeline config, ensemble grid, operating point and hypothesis registry |
| `scripts/gate*_check.py` | the gate checks; each writes its evidence to `reports/` |
| `tools/evaluate.py` | regenerates `EVALUATION.md` from committed evidence |

## The logs

`decisions.md`, `ASSUMPTIONS.md`, `mistakes.md` and `GATES.md` are written as the build happens, not
afterwards. They are the audit trail: what was chosen and why, what is assumed and how to check it,
what went wrong and what changed as a result, and what each gate reviewer found.

## Running the gates

```bash
uv run python scripts/gate4_check.py         # detection recall / false positives (synthetic)
uv run python scripts/gate5_check.py         # position, streaks, real corpus
uv run python scripts/gate9_check.py         # label-shuffle null battery
uv run python scripts/gate10_check.py --images dataset --n 3   # schema, replay, API contract
uv run python tools/evaluate.py              # regenerate EVALUATION.md
```

Gates 6, 7, 8 and 11 need a chemist: 30 labelled plates (10 double-labelled) unblocks calibration and
VLM accuracy, and one unaided chemist at a bench settles whether the screens work.
