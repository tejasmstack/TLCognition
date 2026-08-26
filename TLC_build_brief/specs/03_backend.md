# 7. Backend architecture

> **Status of this document.** Normative. Every "MUST" is a build constraint; every table of choices is already decided. Where a choice is arguable, the one-line trade-off is given and the argument is closed. The reader is an autonomous build agent: do not re-open these decisions, record any deviation in `decisions.md`.
>
> Cross-references: `F1`–`F15` are the established findings in §3. `NN1`–`NN5` are the non-negotiables in §2. §10 governs *what the VLM is asked*; §7.9 below governs *how the call is made, cached and paid for*.

---

## 7.1 Stack

### 7.1.1 The decisions

| Layer | Choice | Why, in one line | Rejected |
|---|---|---|---|
| Language | **Python 3.12.x**, pinned to a patch release | The entire scientific pipeline is numpy/scipy/scikit-image; a second language at the boundary buys nothing and costs a serialization contract. | 3.13 (scikit-image wheel lag), any polyglot split |
| Package manager | **`uv`** with a committed `uv.lock` | Resolver is deterministic and the lock hash is a first-class reproducibility input (§7.2). | pip+requirements.txt (no hash-locked transitive graph), poetry (slower, weaker lock semantics for our use) |
| Web framework | **FastAPI** (≥0.115) + **Pydantic v2** | The result schema is *one* Pydantic model tree that simultaneously validates output, emits `schemas/result_v1.schema.json`, and generates the frontend's TypeScript types — three artefacts that must never drift. | Flask (needs 3 add-ons to reach parity, and no schema-first story); Django (ORM + admin + migrations framework is dead weight for ~8 tables, and its request model fights CPU-bound work) |
| Concurrency in the API | **`def` (sync) endpoints**, not `async def`, except the two that stream | FastAPI runs sync handlers in a threadpool; the pipeline is CPU-bound numpy and an `async def` handler that touches it blocks the event loop for 20 s. Only SSE and the VLM client are async, and the VLM client lives in the worker. | async-everywhere (an invitation to a blocking-call incident) |
| Job execution | **In-process `ProcessPoolExecutor` (N=`cpu_count()-1`, default cap 4) driven by a SQLite job table** | At 20–80 plates/day on one machine, Celery costs a broker, a result backend, a second deployable and a serialization boundary, and buys nothing. The DB table gives durability and restart-safety, which is the only property Celery would actually have provided. | Celery/RQ/Dramatiq (needs Redis); bare threads (GIL + BLAS contention); arq (async, wrong shape for CPU work) |
| Why *process* pool | Required, not stylistic | numpy releases the GIL unevenly and BLAS threads across a thread pool make wall-clock and, in rare reduction-order cases, *results* dependent on scheduling. One process, one BLAS thread (§7.2.2). | ThreadPoolExecutor |
| Database | **SQLite 3.45+, WAL mode**, accessed through **SQLAlchemy 2.0** | One writer, <200 writes/day, whole database is a single file you can `cp` for a backup and `sha256sum` for an audit. SQLAlchemy keeps the Postgres escape hatch open at the cost of about 200 lines. | Postgres now (a container, a user, a backup policy, a migration story — for a file) |
| Migrations | **Alembic**, from commit #1 | The labelled set is the most valuable artefact in the build and it lives in this DB. | ad-hoc `ALTER TABLE` |
| Blob storage | **Content-addressed local filesystem** behind an `ObjectStore` protocol; `LocalFSStore` shipped, `S3Store` stubbed and untested | `blobs/sha256/ab/cd/<full-hash>` gives free dedup, free integrity checking, and no service to run. The protocol means moving to S3/MinIO later is a config change. | MinIO (a service), storing bytes in SQLite (kills the `cp` backup story) |
| Array storage | **HDF5 (h5py) — one `.h5` per run** | §7.4. | npz, zarr, TIFF-as-store |
| Image decode | **`imageio.v3` + `pillow-simd`-free `Pillow`**, EXIF orientation applied *explicitly* and recorded | `cv2.imread` differs across OpenCV builds and silently drops EXIF; forbidden by lint rule. | OpenCV in the ingest path (it is fine inside the pipeline for pure array ops, but is not the decoder) |
| Validation/serialisation | Pydantic v2 with `model_config = ConfigDict(extra="forbid", frozen=True)` on every result model | An extra key is a bug, and frozen models make "the result is immutable" a type-level fact. | dataclasses + hand-rolled JSON |
| CLI | **Typer** — `tlc run`, `tlc replay`, `tlc diff`, `tlc export`, `tlc label-stats` | The whole system MUST be operable and testable with the HTTP layer switched off. The build agent will use the CLI far more than the API. | API-only |
| Observability | `structlog` → JSON lines to stdout; a `/metrics` endpoint in Prometheus text format; no agent, no collector | A lab tool's telemetry story is `grep`. | OpenTelemetry stack |
| Tests | pytest + `pytest-xdist` off by default (determinism), `hypothesis` for the metamorphic properties in Gate 3 | Gate 3's exposure-invariance property is a textbook Hypothesis test. | — |
| Auth | Single **bearer token** from env, plus `X-Reviewer-Id` from a static reviewer list; bind to LAN | This is an internal lab tool. Reviewer identity must be *attributable* (ALCOA+), not *authenticated against an IdP*. | OAuth, JWT, user tables |

### 7.1.2 Repository layout

```
tlc/
  core/            ids.py hashing.py errors.py determinism.py canonical_json.py
  config/          loader.py models.py            # PipelineConfig (hashed) + RuntimeSettings (env)
  pipeline/        # PURE. numpy in, dataclasses out. No I/O, no DB, no network, no logging of values.
    geometry.py photometry.py background.py ensemble.py peaks.py rst.py flags.py streak.py
    correlate.py runner.py                        # runner.py orchestrates and is the only stateful file
  vlm/             provider.py anthropic.py gemini.py replay.py null.py aggregate.py cache.py
    prompts/<prompt_id>/v3.md                     # frozen, hash-checked in CI
    schemas/<schema_id>/v2.json
  storage/         blobs.py odstore.py db.py models_orm.py repositories.py
  jobs/            queue.py worker.py events.py
  labels/          corrections.py adjudication.py partition.py metrics.py
  api/             app.py deps.py routers/{images,runs,corrections,labels,export,admin}.py
  schemas/         result.py  # THE Pydantic result tree; the source of truth for NN2
  cli/             main.py
holdout/           # SEPARATE TOP-LEVEL PACKAGE. Import-guarded. See §7.8.5.
config/pipeline/   v1.0.0.toml v1.1.0.toml ...    # immutable once released
schemas/           result_v1.schema.json          # generated, committed, diffed in CI
```

**Hard rule:** `tlc/pipeline/**` may import numpy, scipy, skimage and `tlc.core` — nothing else. A CI test walks the AST and fails on any other import. This is what keeps the numerics replayable and the VLM out of the numbers (NN1).

---

## 7.2 The pipeline as a versioned, replayable object

### 7.2.1 The four identifiers

Every run is pinned by four hashes. Nothing else is allowed to affect the numbers.

```python
# tlc/core/ids.py
image_sha256   : str  # sha256 of the ORIGINAL uploaded bytes, before any decode
config_hash    : str  # sha256 of the canonical TOML bytes of the pipeline config document
code_fingerprint: str # sha256 over sorted (relpath, sha256(bytes)) of every file under tlc/pipeline/
                      #   + tlc/core/  -- NOT the git commit. A dirty tree must not masquerade as clean.
env_fingerprint: str  # sha256 of canonical-JSON {python: "3.12.7", numpy: "2.1.3", scipy: "1.14.1",
                      #   skimage: "0.24.0", h5py: "3.12.1", lock_hash: "...", platform_tag: "..."}

run_key = sha256(canonical_json({
    "image_sha256": image_sha256,
    "config_hash": config_hash,
    "code_fingerprint": code_fingerprint,
    "env_fingerprint": env_fingerprint,
    "vlm_bundle_hash": vlm_bundle_hash,   # §7.9.3; null when vlm_mode == "off"
}))
```

`platform_tag = f"{sys.platform}-{platform.machine()}-{numpy.__config__.blas_opt_info_name}"`, e.g. `linux-x86_64-openblas`.

`POST /runs` is **idempotent on `run_key`**: a partial unique index guarantees at most one succeeded/refused run per key, and a repeat request returns `200` with the existing `run_id` and `deduplicated: true`. This is why a re-upload of the same photo costs nothing.

### 7.2.2 Determinism mechanisms (all mandatory)

```python
# tlc/core/determinism.py — imported at the top of worker.py and cli/main.py, BEFORE numpy.
import os
for v in ("OMP_NUM_THREADS","OPENBLAS_NUM_THREADS","MKL_NUM_THREADS",
          "NUMEXPR_NUM_THREADS","VECLIB_MAXIMUM_THREADS"):
    os.environ[v] = "1"
os.environ["PYTHONHASHSEED"] = "0"
```

1. **BLAS is single-threaded.** Multi-threaded reductions change summation order, which changes the last ULP, which changes a threshold comparison, which changes a spot count. This is the single most likely cause of a "flaky determinism test" and it MUST be closed on day one.
2. **One RNG, seeded from the data.** `rng = numpy.random.Generator(numpy.random.PCG64(seed))` with `seed = int(image_sha256[:16], 16) ^ config.seed_salt`. Passed explicitly down the call tree. **Lint rule: `np.random.` (module-level) anywhere under `tlc/` is a CI failure.** The bootstrap intervals on Rst and the null-battery draws are the only consumers.
3. **No wall-clock, no `time`, no `uuid4`, no `os.urandom`, no locale, no `datetime.now()` inside `tlc/pipeline/**`.** Enforced by the same AST walker. Timestamps exist only in the run envelope, which is excluded from `result_sha256` (§7.2.4).
4. **No unordered iteration.** `sorted()` on every collection that reaches the output. Sets are permitted internally, never at a boundary.
5. **Canonical JSON.** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)`. `allow_nan=False` is deliberate: a NaN in the output is a bug, and the schema requires `null` plus a refusal reason instead.
6. **Float canonicalisation at the schema boundary.** Every float in the result JSON is rounded by a Pydantic serializer: positions and fractions to 6 dp, OD values to 6 dp, probabilities to 4 dp, costs to 6 dp. This is what makes byte-identity survivable across platforms.

**The honest determinism contract**, stated in `ASSUMPTIONS.md` verbatim:

> *Tier 1 (guaranteed):* on the same `platform_tag` and `env_fingerprint`, a replay produces a byte-identical result JSON **and** a byte-identical OD array (`od_sha256` equal).
> *Tier 2 (guaranteed):* across platforms with the same `env_fingerprint` minus `platform_tag`, the result JSON is byte-identical after canonical rounding; `od_sha256` may differ in the last ULP of a small number of pixels.
> *Not guaranteed:* anything across a different `env_fingerprint`. A dependency bump is a new pipeline version and requires a comparability run (§7.2.5).

CI enforces Tier 1 with `test_replay_byte_identical` over the whole corpus, run twice in the same job and once in a second job on a cold container.

### 7.2.3 The pipeline version

`pipeline_version` is a semver string owned by the config document, e.g. `1.3.0`. Rules:

- **PATCH** — a change that cannot alter any output on any input (docstrings, logging, refactor). CI must prove it: the comparability run over the full corpus must show zero diffs.
- **MINOR** — a change that alters outputs but not the schema. Requires a committed comparability report.
- **MAJOR** — a schema change. `schema_version` in the result increments too, and the frontend gets a new generated type.

A released config file under `config/pipeline/` is **immutable**. CI hashes every file in that directory against a committed `config/pipeline/HASHES.txt` and fails on any change to an existing version. You add `v1.4.0.toml`; you never edit `v1.3.0.toml`.

### 7.2.4 What is hashed and what is not

```
result_sha256 = sha256(canonical_json(result.model_dump(mode="json", exclude=DETERMINISM_EXCLUDED)))

DETERMINISM_EXCLUDED = {
  "run_id", "created_at", "started_at", "finished_at", "duration_ms",
  "provenance.host", "provenance.worker_pid",
  "vlm.latency_ms_per_sample", "vlm.cache_hit",     # cache hit changes nothing but timing
  "storage.*",                                       # paths are deployment-local
}
```

Everything else — including `vlm.cost_usd` and `vlm.tokens`, which are deterministic given the cached responses — is inside the hash. `result_sha256` is stored on the run row and is the object a comparability report diffs.

### 7.2.5 Replay and diff

```
POST /api/v1/runs/{run_id}/replay
  body: { "pipeline_version": "1.3.0" | null,   # null = the version the original used
          "config_override": null,               # non-null forces version "0.0.0-adhoc" and
                                                 #   marks the run non-validatable
          "vlm_mode": "replay" }                 # default: replay from the original's cache
  -> 202 { "run_id": "run_...", "replay_of": "run_...", "expected_run_key": "..." }
```

A replay at the *same* version with `vlm_mode=replay` MUST reproduce `result_sha256` exactly; the worker asserts this and marks the run `failed` with `E_REPLAY_DRIFT` if it does not. That assertion is the regression detector for the entire build.

```
GET /api/v1/runs/compare?a=run_A&b=run_B&tolerance_rst=0.005
-> 200 {
  "comparable": true,
  "version_delta": {"a": "1.2.1", "b": "1.3.0", "config_diff": [
      {"path":"ensemble.radii_px","a":[8,16,24,35],"b":[6,12,20,30]}]},
  "env_delta": [{"lib":"scipy","a":"1.14.1","b":"1.15.0"}],
  "geometry_delta": {"corner_rmse_px": 0.31, "tilt_deg_delta": 0.02},
  "spot_matching": {                     # greedy match within tolerance, per lane
    "matched":   [{"a":"sp_01","b":"sp_01","d_rst":0.0031,"d_agreement":+0.06,
                   "status_a":"confirmed","status_b":"confirmed"}],
    "only_in_a": [{"id":"sp_04","rst":0.412,"status":"candidate","agreement":0.44}],
    "only_in_b": [],
    "status_changes": [{"id":"sp_03","from":"candidate","to":"confirmed"}]
  },
  "flag_delta": {"added": [], "removed": ["low_ensemble_agreement"]},
  "verdict": "b_reports_one_fewer_marginal_candidate"
}
```

The comparability *report* (Gate 10 evidence, and the artefact a QA reviewer reads) is `tlc diff --version-a 1.2.1 --version-b 1.3.0 --corpus dataset/ --out reports/comparability_1.2.1_1.3.0.md`, which runs the pairwise compare over the whole corpus and tabulates it.

### 7.2.6 Why this matters for ICH Q2(R2)

ICH Q2(R2) (Validation of Analytical Procedures, adopted 2023) and its companion Q14 place four demands on a procedure like this one, and each maps onto a mechanism above:

1. **The reportable value must be reconstructable from the raw data.** The raw data for a densitometric readout is not the JPEG — it is the calibrated optical-density field. Storing the OD field immutably (§7.4) alongside a manifest that names every parameter is precisely the "raw data + documented procedure" pair a reviewer needs to re-derive the reportable value by hand.
2. **Robustness = deliberate variation of procedure parameters.** Q2(R2) asks you to demonstrate that small, deliberate changes to procedure parameters do not change the conclusion. The 32-pipeline ensemble (F6) *is* a robustness study, executed on every plate, with its outcome reported as `ensemble_agreement`. Do not describe the ensemble as a confidence trick; describe it as a per-run robustness experiment, because that is what it is.
3. **Specificity requires a stated reference.** Because there is no solvent front (F2), the reportable position is **Rst**, and Q2(R2) requires the reference to be named. The schema therefore makes `rst.reference_spot_id` mandatory and non-nullable whenever `rst.value` is non-null. An Rst without a named reference is not a measurement.
4. **Lifecycle and change management (Q14).** A pipeline version bump is a change to an analytical procedure. The comparability report in §7.2.5 is the change-control evidence. Without it, every dependency upgrade silently invalidates the validation.

Layered on top, **ALCOA+ / 21 CFR Part 11 §11.10(e)** requires attributable, contemporaneous, original, enduring records with a secure audit trail. Consequences already baked in: runs are immutable and are *superseded*, never deleted or edited; corrections are append-only and carry `reviewer_id` and a server-side timestamp; the `audit_log` table records every state transition; and no endpoint issues an `UPDATE` to a result.

**State this honestly in the README:** these mechanisms make the method *validatable*. They do not constitute a validation, which requires the labelled set (Gate 6), the calibration (Gate 7) and a signed protocol.

---

## 7.3 The result JSON schema

### 7.3.1 The provenance envelope (NN2 made structural)

Every scientific scalar is wrapped. Bookkeeping fields (ids, counts, paths, enums about the run itself) are not — wrapping them adds noise without adding honesty.

```python
# tlc/schemas/result.py
Provenance = Literal[
    "measured",   # derived from this image's pixels by deterministic code
    "chosen",     # a configured constant or an operator input; a decision, not an observation
    "inferred",   # derived from other measured values under a stated model/assumption
    "refused",    # the system declined to produce this value; `refusal` is populated
]

class Refusal(BaseModel):
    code: str            # stable machine code, e.g. "E_CLIP_PHOTOMETRY"
    message: str         # one sentence for a chemist
    remedy: str          # what the human should DO
    evidence: dict[str, float | str] = {}

class Q(BaseModel, Generic[T]):            # "quantity"
    value: T | None                        # null iff provenance == "refused"
    unit: str                              # "px" | "frac" | "OD" | "OD*px" | "deg" | "1" | "USD" | "s"
    provenance: Provenance
    method: str | None = None              # e.g. "emg_fit", "poly3_background", "vlm_majority_vote"
    ci95: tuple[float, float] | None = None
    n: int | None = None                   # sample size behind ci95, where applicable
    refusal: Refusal | None = None
    note: str | None = None
```

**Rules enforced by validators, not by convention:**
- `provenance == "refused"` ⇒ `value is None` and `refusal is not None`.
- `provenance == "inferred"` ⇒ `method is not None`.
- `ci95` may only be present when `provenance in {"measured","inferred"}`.
- `unit` is a closed enum. There is no unitless float in the scientific part of this document.

### 7.3.2 Top-level structure

```
Result
├─ schema_version: "1"                    str, fixed per MAJOR
├─ run_id, image_id, created_at, status
├─ status: "succeeded" | "refused" | "degraded"
├─ image        ImageBlock
├─ capture_qc   CaptureQCBlock
├─ geometry     GeometryBlock
├─ annotation_bands: [AnnotationBand]
├─ lanes:        [Lane]
├─ reference     ReferenceBlock            (origin row, front row, Rst anchor)
├─ photometry   PhotometryBlock
├─ densitograms: [Densitogram]
├─ spots:        [Spot]
├─ flags:        [Flag]
├─ correlations  CorrelationBlock
├─ vlm           VLMBlock
├─ refusals:     [Refusal]                 (plate-level; NN3 — refusals are values, not nulls)
├─ storage       StorageBlock              (excluded from result_sha256)
└─ provenance    ProvenanceBlock
```

### 7.3.3 Field reference

**`image`** — all `measured` from the bytes; no envelope needed.

| field | type | unit | note |
|---|---|---|---|
| `sha256` | str(64) | — | of original bytes, pre-decode |
| `bytes` | int | B | |
| `mime` | str | — | `image/jpeg` \| `image/png` \| `image/heic` |
| `width_px`, `height_px` | int | px | after EXIF orientation |
| `exif_orientation` | int\|null | — | value found; **applied explicitly**, never by the decoder |
| `decoder` | str | — | `imageio.v3/pillow-11.0.0` |
| `original_filename` | str\|null | — | never trusted for anything |

**`capture_qc`** — the input gate (F1). Every field `measured`.

| field | type | unit | note |
|---|---|---|---|
| `green_clip_frac_in_plate` | Q[float] | frac | **the F1 gate**: fraction of in-plate px with G≥254 |
| `green_clip_frac_frame` | Q[float] | frac | whole frame, for comparison |
| `black_clip_frac_in_plate` | Q[float] | frac | G≤1 |
| `channel_sat_frac` | Q[float] | frac | any channel ≥254 |
| `plate_area_frac` | Q[float] | frac | plate mask / frame |
| `frame_overrun` | Q[float] ×4 | frac | `top/bottom/left/right`; >0.02 ⇒ plate cropped in-camera |
| `tilt_deg` | Q[float] | deg | from the min-area rect |
| `focus_metric` | Q[float] | 1 | var(Laplacian) normalised by mean²; **relative only**, note says so |
| `mean_green_in_plate` | Q[float] | 1 | 0–1 |
| `verdict` | enum | — | `ok` \| `positions_only` \| `unusable` |
| `gates_fired` | [str] | — | codes of every gate that changed the verdict |

Gate thresholds live in the config (§7.10) and are echoed here as `chosen` values so the JSON is self-explaining: `gate_thresholds: {green_clip_max: Q(0.15, "frac", "chosen"), ...}`.

**`geometry`**

| field | type | unit | prov |
|---|---|---|---|
| `corners_src_px` | float[4][2] | px | measured (order: TL,TR,BR,BL after canonical sort) |
| `homography` | float[3][3] | 1 | measured — maps rectified→source |
| `rectified_shape` | [int,int] | px | inferred (H,W from corner distances) |
| `tilt_deg` | Q[float] | deg | measured |
| `idempotency_residual_px` | Q[float] | px | measured — Gate 2's re-warp check, run every time |
| `valid_erosion_px` | Q[int] | px | inferred — derived from `tilt_deg`, not a constant (see `M-007`) |
| `valid_frac` | Q[float] | frac | measured |
| `detection_method` | str | — | `hsv_bright_green_largest_cc` |

**`annotation_bands`** — F7: never deterministic.

```
{ "kind": "header" | "label_row" | "footer",
  "y0_frac": Q[float](unit="frac"), "y1_frac": Q[float](unit="frac"),
  "provenance": "vlm" | "operator" | "convention" | "refused",
  "vlm_agreement": float|null,          # 0..1, samples agreeing on the modal band
  "vlm_iqr_frac": float|null,           # spread across samples; >0.05 ⇒ abstain
  "source_detail": "gemini-2.5-flash-lite@prompt:bands/v3" | "operator:tejas" | "config.default_bands" }
```

**`lanes`** — F10: count never comes from the signal.

| field | type | unit | prov |
|---|---|---|---|
| `index` | int | — | 0-based, left→right in the rectified frame |
| `label` | str \| `"UNREADABLE"` | — | closed enum from config + `UNREADABLE` |
| `label_provenance` | `vlm`\|`operator`\|`refused` | — | |
| `label_agreement` | float\|null | 1 | VLM self-consistency, 0..1 |
| `x_center_px` | Q[float] | px | `measured` — refined within a ±`window_frac` box seeded by the VLM |
| `x_center_frac` | Q[float] | frac | inferred |
| `half_width_px` | Q[int] | px | chosen (config `lane_halfwidth_frac` × lane pitch) |
| `x_seed_provenance` | `vlm`\|`operator`\|`uniform_grid` | — | what seeded the refinement |
| `is_empty` | Q[bool] | 1 | measured — no feature ≥ `empty_lane_sigma` anywhere |
| `is_streaking` | Q[bool] | 1 | measured — F11 streak statistic |
| `quantified` | bool | — | false ⇒ areas suppressed for this lane |
| `suppression` | Refusal\|null | — | populated iff `quantified == false` |
| `at_plate_edge` | bool | — | the `M-007` guard |

**`reference`** — where the scale comes from. This block is the one most likely to be quietly wrong, so it is the most heavily annotated.

```
{
 "origin_row_px":  Q[float](unit="px", provenance="measured", method="origin_dot_blobs", ci95=[...]),
 "origin_provenance": "detected_dots" | "operator" | "vlm_proposed_confirmed" | "assumed_plate_bottom" | "refused",
 "origin_support_sigma": Q[float](unit="1"),      # how many σ the dots stand out
 "origin_dots_found": Q[int](unit="1"),           # per-lane count, out of n_lanes

 "front_row_px":   Q[float]|refused,              # F2: on our corpus this is ALWAYS refused
 "front_provenance": "detected_line" | "vlm_confirmed" | "operator" | "absent",
 "front_absent_reason": Refusal|null,

 "rst_anchor": {                                   # NN: an Rst without a named anchor is not a measurement
   "spot_id": "sp_07",
   "lane_index": 3,
   "lane_label": "sd",
   "y_px": Q[float],
   "selection_rule": "highest_confirmed_spot_in_standard_lane",   # chosen
   "provenance": "measured"
 } | null,
 "rf_available": false,
 "rf_unavailable_reason": Refusal|null
}
```

**`photometry`**

| field | type | unit | prov | note |
|---|---|---|---|---|
| `signal_channel` | str | — | chosen | `green` (F: green carries the signal) |
| `background_model` | str | — | chosen | `poly3` primary (D-014) |
| `background_radius_px` | Q[int] | px | chosen | primary member only |
| `od_transform` | str | — | chosen | `log10(I0/I)`; `kubelka_munk` is **not permitted** in this regime (F5) |
| `sigma_od` | Q[float] | OD | measured | **measured once, on the raw analysable band, before any spot masking** (F4) |
| `sigma_method` | str | — | chosen | `mad_1.4826_prespot` |
| `sigma_stability_across_radii` | Q[float] | frac | measured | max relative spread of σ across the ensemble radii; Gate 3 requires ≤0.15 |
| `clipped_px_frac_in_analysable` | Q[float] | frac | measured | |
| `photometry_mode` | enum | — | inferred | `full` \| `positions_only` \| `refused` |

**`densitograms`** — one per lane.

```
{ "lane_index": 0,
  "y_px": {"start": 0, "stop": 1104, "step": 1},         # implicit axis, not an array
  "unit": "OD",
  "sampling": "mean over valid px in [x_center-hw, x_center+hw]",
  "n_valid_columns": 89,
  "ref": "h5://runs/run_0193.../densitograms/lane_00",   # full float32, authoritative
  "sha256": "…",
  "preview": [0.0012, 0.0009, …]                          # ≤512 pts, decimated by block-mean,
}                                                          #   rounded 5dp, for the frontend only
```

The preview is explicitly labelled non-authoritative in the schema description. Nothing may be computed from it.

**`spots`** — the table.

| field | type | unit | prov | note |
|---|---|---|---|---|
| `id` | str | — | — | `sp_NN`, stable within a run, assigned in (lane, y) order |
| `lane_index` | int | — | — | |
| `status` | enum | — | — | `confirmed` \| `candidate` \| `rejected` \| `proposed_unconfirmed` \| `suppressed_streak` |
| `y_px` | Q[float] | px | measured | rectified-frame row; `ci95` from the EMG fit covariance |
| `y_frac` | Q[float] | frac | inferred | `y_px / H` |
| `rst` | Q[float] | 1 | inferred | `(y_origin − y_spot)/(y_origin − y_anchor)`; `ci95` by bootstrap over the ensemble |
| `rst_reference_spot_id` | str\|null | — | — | mandatory when `rst.value` is not null |
| `rf` | Q[float] | 1 | refused (F2) | present only when `reference.front_provenance != "absent"` |
| `peak_model` | enum | — | chosen | `emg` \| `gaussian` \| `none` |
| `emg_sigma_px` | Q[float] | px | measured | Gaussian width component |
| `emg_tau_px` | Q[float] | px | measured | tailing constant; `tau/sigma > tail_ratio_max` ⇒ streak flag |
| `fwhm_px` | Q[float] | px | inferred | numerically from the fitted EMG, not a closed form |
| `amplitude_od` | Q[float] | OD | measured\|refused | refused when `photometry_mode == "positions_only"` |
| `area_od_px` | Q[float] | OD·px | measured\|refused | integral of the fitted EMG |
| `area_frac_of_lane` | Q[float] | frac | inferred\|refused | **the number a chemist wants**; refused with the area |
| `snr` | Q[float] | 1 | measured | `amplitude / sigma_od`, computed even in `positions_only` but flagged |
| `ensemble_agreement` | Q[float] | 1 | measured | fraction of the 32 pipelines placing a peak within `match_tol_px` |
| `ensemble_n_total` | int | — | — | 32 |
| `ensemble_n_hit` | int | — | — | |
| `ensemble_y_spread_px` | Q[float] | px | measured | sd of matched positions across pipelines |
| `confidence` | Q[float] | 1 | inferred | **calibrated** probability the spot is real; `method` names the calibration version; `provenance="refused"` before Gate 7 |
| `calibration_version` | str\|null | — | — | |
| `fit_residual_rms_od` | Q[float] | OD | measured | |
| `vlm_proposed` | bool | — | — | true if this position originated as a VLM hypothesis |
| `vlm_confirmation` | obj\|null | — | — | `{proposed_y_frac, pixel_support_sigma, confirmed: bool}` (F9) |
| `flags` | [str] | — | — | e.g. `near_origin`, `overlaps_annotation_band`, `clipped_neighbourhood` |

**`flags`** (plate-level) — `{code, severity: "info"|"warn"|"block", message, remedy, evidence: {}}`.

Reserved codes: `green_clipping_high`, `frame_overrun`, `no_solvent_front`, `no_origin_dots`, `lane_at_plate_edge`, `streaking_lane`, `low_ensemble_agreement`, `vlm_unavailable`, `vlm_disagreement`, `annotation_band_assumed`, `empty_lane`, `zero_spots_at_5sigma`, `uncalibrated_confidence`.

**`correlations`** — see §7.3.5.

**`vlm`**

```
{ "mode": "live"|"replay"|"off",
  "model_id": "gemini-2.5-flash-lite-001", "prompt_bundle": {"bands":"v3","lanes":"v4","front":"v2"},
  "n_samples": 5, "temperature": 1.0,
  "fields": { "lane_count": {"value":4,"agreement":1.0,"samples":[4,4,4,4,4]},
              "lane_labels":{"value":["S","co","R","sd"],"agreement":0.8,
                             "disagreements":[{"index":1,"votes":{"co":4,"Co":1}}]},
              "bands":      {"value":[0.26,0.90],"agreement":0.6,"iqr_frac":0.018},
              "front_present":{"value":false,"agreement":1.0},
              "header_text":{"value":"MEHQ-P29  4hr (30+ GA:MeOH)","agreement":0.4,
                             "flagged_for_review":true} },
  "cache": {"hits":5,"misses":0,"bundle_hash":"…"},
  "cost": {"input_tokens":4820,"output_tokens":610,"cached_tokens":0,"usd":0.000112},
  "attempts": 5, "retries": 0, "degraded": false }
```

**`provenance`**

```
{ "pipeline_version":"1.3.0", "schema_version":"1",
  "config_hash":"…", "config_ref":"config/pipeline/v1.3.0.toml",
  "config_document": { … the full parsed config, embedded verbatim … },
  "code_fingerprint":"…", "git_commit":"…", "git_dirty":false,
  "env_fingerprint":"…", "lock_hash":"…",
  "libraries":{"python":"3.12.7","numpy":"2.1.3","scipy":"1.14.1","scikit-image":"0.24.0",
               "h5py":"3.12.1","pillow":"11.0.0"},
  "platform_tag":"linux-x86_64-openblas",
  "seed":  1734928…, "seed_derivation":"int(image_sha256[:16],16) ^ config.seed_salt",
  "run_key":"…", "result_sha256":"…", "od_sha256":"…",
  "replay_of": null, "superseded_by": null,
  "determinism_tier":"tier1" }
```

`config_document` is embedded **verbatim, not by reference**. A config file can go missing; a run must remain reproducible from its own record alone.

### 7.3.4 Refusal is a value, not a null (NN3)

Every stage has an abstention path and every abstention writes a `Refusal`. The reserved codes and their remedies:

| code | fires when | remedy shown to the chemist |
|---|---|---|
| `E_CLIP_PHOTOMETRY` | `green_clip_frac_in_plate > 0.15` | "Re-shoot with 1–2 stops less exposure or a shorter shutter. Positions below are still valid; areas are not." |
| `E_CLIP_UNUSABLE` | `> 0.60` | "Re-shoot. Nothing on this image can be measured." |
| `E_NO_FRONT` | always, on this corpus (F2) | "Rf cannot be computed without a drawn solvent front. Draw the front in pencil before imaging. Rst is reported instead." |
| `E_NO_ORIGIN` | origin dots not found in ≥2 lanes | "Mark the origin line in pencil, or set it manually in the review screen." |
| `E_FRAME_OVERRUN` | any edge overrun > 0.02 | "The plate is cut off at the {edge}. Re-shoot with the whole plate in frame." |
| `E_STREAK` | per-lane, `tau/sigma > 3.0` | "Lane {n} is streaking; the position of a streak is not defined. Reduce loading or change the solvent system." |
| `E_LANE_COUNT_UNKNOWN` | VLM abstained and no operator input | "Enter the number of lanes." |
| `E_UNCALIBRATED` | before Gate 7 | "Confidence is not yet calibrated on this instrument; treat ensemble agreement as an ordinal, not a probability." |
| `E_INSUFFICIENT_DATA` | correlations, n below the pre-registered minimum | "Not enough plates to test this. {k} of {n_min}." |
| `E_VLM_UNAVAILABLE` | circuit breaker open | "Structure was read from the default geometric convention, not from the image. Check the lane assignment." |

### 7.3.5 Correlations block

A **fixed, pre-registered** hypothesis list lives in the config; nothing else may be tested. Each finding:

```
{ "hypothesis_id":"H3_conversion_vs_time",
  "statement":"Product-band area fraction in lane R increases with reaction time.",
  "n_plates": 7, "n_min_required": 20,
  "verdict": "insufficient_data",             # supported | not_supported | insufficient_data
  "effect": null, "ci95": null,
  "p_raw": null, "p_adjusted": null, "adjustment": "benjamini_hochberg",
  "confounds_checked": ["operator","plate_batch","capture_session","exposure"],
  "confounds_unresolved": ["capture_session"],
  "suppressed_reason": {"code":"E_INSUFFICIENT_DATA","message":"7 plates; 20 required",
                        "remedy":"Label and run at least 13 more plates from ≥3 batches."} }
```

`correlations.suppressed: [...]` lists every hypothesis that was tested and did not survive, with its raw p-value. Hiding the suppressed list is how a false-discovery-rate control becomes theatre.

### 7.3.6 Worked example

A real plate from the corpus (`plate1`, MEHQ-P29). Truncated only where marked `…`; every block that exists is shown.

```json
{
  "schema_version": "1",
  "run_id": "run_01JB8Q2M7K4X0ZP3RCN9VH6TAD",
  "image_id": "img_9f2c41ab7de05c83b1a4",
  "created_at": "2026-08-26T09:14:22.104Z",
  "status": "degraded",

  "image": {
    "sha256": "9f2c41ab7de05c83b1a4e6072d1f88c5a3b90e14cc7f2a6d55b8e0913f4c72aa",
    "bytes": 3184922, "mime": "image/jpeg",
    "width_px": 3024, "height_px": 4032, "exif_orientation": 6,
    "decoder": "imageio.v3/pillow-11.0.0", "original_filename": "IMG_4471.jpg"
  },

  "capture_qc": {
    "green_clip_frac_in_plate": {"value":0.341,"unit":"frac","provenance":"measured"},
    "green_clip_frac_frame":    {"value":0.118,"unit":"frac","provenance":"measured"},
    "black_clip_frac_in_plate": {"value":0.0,"unit":"frac","provenance":"measured"},
    "channel_sat_frac":         {"value":0.352,"unit":"frac","provenance":"measured"},
    "plate_area_frac":          {"value":0.287,"unit":"frac","provenance":"measured"},
    "frame_overrun": {"top":{"value":0.000,"unit":"frac","provenance":"measured"},
                      "bottom":{"value":0.041,"unit":"frac","provenance":"measured"},
                      "left":{"value":0.000,"unit":"frac","provenance":"measured"},
                      "right":{"value":0.000,"unit":"frac","provenance":"measured"}},
    "tilt_deg":     {"value":3.72,"unit":"deg","provenance":"measured"},
    "focus_metric": {"value":0.0184,"unit":"1","provenance":"measured",
                     "note":"relative only; not comparable across cameras"},
    "mean_green_in_plate": {"value":0.897,"unit":"1","provenance":"measured"},
    "verdict": "positions_only",
    "gates_fired": ["E_CLIP_PHOTOMETRY","E_FRAME_OVERRUN"],
    "gate_thresholds": {
      "green_clip_max":      {"value":0.15,"unit":"frac","provenance":"chosen"},
      "green_clip_unusable": {"value":0.60,"unit":"frac","provenance":"chosen"},
      "frame_overrun_max":   {"value":0.02,"unit":"frac","provenance":"chosen"}
    }
  },

  "geometry": {
    "corners_src_px": [[402.1,988.7],[2611.4,845.3],[2698.0,3719.6],[489.2,3862.9]],
    "homography": [[0.7212,-0.0468,402.1],[0.0455,0.7189,988.7],[0.0,0.0,1.0]],
    "rectified_shape": [1104, 812],
    "tilt_deg": {"value":3.72,"unit":"deg","provenance":"measured"},
    "idempotency_residual_px": {"value":0.21,"unit":"px","provenance":"measured"},
    "valid_erosion_px": {"value":4,"unit":"px","provenance":"inferred",
                         "method":"ceil(2 + tilt_deg*0.55)"},
    "valid_frac": {"value":0.981,"unit":"frac","provenance":"measured"},
    "detection_method": "hsv_bright_green_largest_cc"
  },

  "annotation_bands": [
    {"kind":"header","y0_frac":{"value":0.000,"unit":"frac","provenance":"chosen"},
                     "y1_frac":{"value":0.262,"unit":"frac","provenance":"inferred",
                                "method":"vlm_median_of_5"},
     "provenance":"vlm","vlm_agreement":0.8,"vlm_iqr_frac":0.011,
     "source_detail":"gemini-2.5-flash-lite-001@prompt:bands/v3"},
    {"kind":"label_row","y0_frac":{"value":0.901,"unit":"frac","provenance":"inferred",
                                   "method":"vlm_median_of_5"},
                        "y1_frac":{"value":1.000,"unit":"frac","provenance":"chosen"},
     "provenance":"vlm","vlm_agreement":1.0,"vlm_iqr_frac":0.004,
     "source_detail":"gemini-2.5-flash-lite-001@prompt:bands/v3"}
  ],

  "lanes": [
    {"index":0,"label":"S","label_provenance":"vlm","label_agreement":1.0,
     "x_center_px":{"value":151.3,"unit":"px","provenance":"measured","method":"colprofile_argmax_in_vlm_window"},
     "x_center_frac":{"value":0.186,"unit":"frac","provenance":"inferred"},
     "half_width_px":{"value":56,"unit":"px","provenance":"chosen"},
     "x_seed_provenance":"vlm",
     "is_empty":{"value":false,"unit":"1","provenance":"measured"},
     "is_streaking":{"value":false,"unit":"1","provenance":"measured"},
     "quantified":false,
     "suppression":{"code":"E_CLIP_PHOTOMETRY",
       "message":"Areas suppressed: 34.1% of in-plate green pixels are clipped at 255.",
       "remedy":"Re-shoot 1-2 stops darker. Positions remain valid.",
       "evidence":{"green_clip_frac_in_plate":0.341,"gate":0.15}},
     "at_plate_edge":false},
    {"index":1,"label":"co","label_provenance":"vlm","label_agreement":0.8, "…":"…"},
    {"index":2,"label":"R","label_provenance":"vlm","label_agreement":1.0, "…":"…"},
    {"index":3,"label":"sd","label_provenance":"vlm","label_agreement":0.8,
     "x_center_px":{"value":631.8,"unit":"px","provenance":"measured","method":"colprofile_argmax_in_vlm_window"},
     "…":"…"}
  ],

  "reference": {
    "origin_row_px": {"value":958.4,"unit":"px","provenance":"measured",
                      "method":"origin_dot_blobs_median","ci95":[956.9,959.8],"n":4},
    "origin_provenance": "detected_dots",
    "origin_support_sigma": {"value":6.9,"unit":"1","provenance":"measured"},
    "origin_dots_found": {"value":4,"unit":"1","provenance":"measured"},

    "front_row_px": {"value":null,"unit":"px","provenance":"refused",
      "refusal":{"code":"E_NO_FRONT",
        "message":"No pencil solvent front is drawn on this plate.",
        "remedy":"Draw the solvent front in pencil immediately after development, before imaging.",
        "evidence":{"best_line_candidate_sigma":1.1,"required_sigma":4.0,"row_coverage":0.31}}},
    "front_provenance": "absent",

    "rst_anchor": {"spot_id":"sp_07","lane_index":3,"lane_label":"sd",
      "y_px":{"value":611.9,"unit":"px","provenance":"measured"},
      "selection_rule":"highest_confirmed_spot_in_standard_lane","provenance":"measured"},
    "rf_available": false,
    "rf_unavailable_reason": {"code":"E_NO_FRONT",
      "message":"Rf is undefined without a front. Across defensible conventions this plate's main band spans Rf 0.34-0.97.",
      "remedy":"Use the reported Rst, or draw a front and re-image.","evidence":{}}
  },

  "photometry": {
    "signal_channel":"green", "background_model":"poly3",
    "background_radius_px":{"value":97,"unit":"px","provenance":"chosen",
                            "note":"0.12 x max(H,W); primary member only"},
    "od_transform":"log10(I0/I)",
    "sigma_od":{"value":0.011213,"unit":"OD","provenance":"measured",
                "method":"mad_1.4826_prespot",
                "note":"measured once on the raw analysable band before any spot masking (F4)"},
    "sigma_stability_across_radii":{"value":0.081,"unit":"frac","provenance":"measured"},
    "clipped_px_frac_in_analysable":{"value":0.229,"unit":"frac","provenance":"measured"},
    "photometry_mode":"positions_only"
  },

  "densitograms": [
    {"lane_index":0,"y_px":{"start":0,"stop":1104,"step":1},"unit":"OD",
     "sampling":"mean over valid px in [x_center-56, x_center+56]","n_valid_columns":112,
     "ref":"h5://runs/run_01JB8Q2M7K4X0ZP3RCN9VH6TAD.h5#/densitograms/lane_00",
     "sha256":"4c1e…","preview":[0.0,0.0,0.00031,"…512 values…"]},
    {"lane_index":1,"…":"…"},{"lane_index":2,"…":"…"},{"lane_index":3,"…":"…"}
  ],

  "spots": [
    {"id":"sp_01","lane_index":0,"status":"confirmed",
     "y_px":{"value":615.2,"unit":"px","provenance":"measured","method":"emg_fit","ci95":[613.9,616.5]},
     "y_frac":{"value":0.557,"unit":"frac","provenance":"inferred"},
     "rst":{"value":0.991,"unit":"1","provenance":"inferred",
            "method":"(y_origin-y_spot)/(y_origin-y_anchor)","ci95":[0.983,0.999],"n":32},
     "rst_reference_spot_id":"sp_07",
     "rf":{"value":null,"unit":"1","provenance":"refused",
           "refusal":{"code":"E_NO_FRONT","message":"No solvent front.","remedy":"Draw the front.","evidence":{}}},
     "peak_model":"emg",
     "emg_sigma_px":{"value":8.4,"unit":"px","provenance":"measured","ci95":[7.6,9.3]},
     "emg_tau_px":{"value":4.1,"unit":"px","provenance":"measured","ci95":[2.9,5.6]},
     "fwhm_px":{"value":22.7,"unit":"px","provenance":"inferred","method":"numeric_fwhm_of_fitted_emg"},
     "amplitude_od":{"value":null,"unit":"OD","provenance":"refused",
       "refusal":{"code":"E_CLIP_PHOTOMETRY","message":"Photometry suppressed: 34.1% green clipping.",
                  "remedy":"Re-shoot darker.","evidence":{"green_clip_frac_in_plate":0.341}}},
     "area_od_px":{"value":null,"unit":"OD*px","provenance":"refused",
       "refusal":{"code":"E_CLIP_PHOTOMETRY","message":"Photometry suppressed: 34.1% green clipping.",
                  "remedy":"Re-shoot darker.","evidence":{"green_clip_frac_in_plate":0.341}}},
     "area_frac_of_lane":{"value":null,"unit":"frac","provenance":"refused",
       "refusal":{"code":"E_CLIP_PHOTOMETRY","message":"Photometry suppressed.",
                  "remedy":"Re-shoot darker.","evidence":{}}},
     "snr":{"value":7.9,"unit":"1","provenance":"measured",
            "note":"biased low where the local background is clipped; detection statistic only"},
     "ensemble_agreement":{"value":0.78,"unit":"1","provenance":"measured"},
     "ensemble_n_total":32,"ensemble_n_hit":25,
     "ensemble_y_spread_px":{"value":0.9,"unit":"px","provenance":"measured"},
     "confidence":{"value":null,"unit":"1","provenance":"refused",
       "refusal":{"code":"E_UNCALIBRATED",
         "message":"Confidence is not calibrated on this instrument yet.",
         "remedy":"Complete 30 labelled plates (Gate 6) and run calibration (Gate 7).",
         "evidence":{"labelled_plates":7,"required":30}}},
     "calibration_version":null,
     "fit_residual_rms_od":{"value":0.0041,"unit":"OD","provenance":"measured"},
     "vlm_proposed":false,"vlm_confirmation":null,
     "flags":["clipped_neighbourhood"]},

    {"id":"sp_04","lane_index":2,"status":"confirmed",
     "y_px":{"value":700.3,"unit":"px","provenance":"measured","method":"emg_fit","ci95":[698.1,702.5]},
     "y_frac":{"value":0.634,"unit":"frac","provenance":"inferred"},
     "rst":{"value":0.746,"unit":"1","provenance":"inferred",
            "method":"(y_origin-y_spot)/(y_origin-y_anchor)","ci95":[0.733,0.759],"n":32},
     "rst_reference_spot_id":"sp_07",
     "peak_model":"emg",
     "emg_sigma_px":{"value":11.2,"unit":"px","provenance":"measured","ci95":[9.8,12.9]},
     "emg_tau_px":{"value":9.6,"unit":"px","provenance":"measured","ci95":[7.1,12.8]},
     "ensemble_agreement":{"value":0.75,"unit":"1","provenance":"measured"},
     "ensemble_n_total":32,"ensemble_n_hit":24,
     "snr":{"value":5.6,"unit":"1","provenance":"measured"},
     "vlm_proposed":false,"vlm_confirmation":null,
     "flags":[], "…":"…"},

    {"id":"sp_05","lane_index":2,"status":"candidate",
     "y_px":{"value":845.7,"unit":"px","provenance":"measured","method":"emg_fit","ci95":[840.2,851.2]},
     "rst":{"value":0.325,"unit":"1","provenance":"inferred","ci95":[0.283,0.367],"n":32},
     "rst_reference_spot_id":"sp_07",
     "ensemble_agreement":{"value":0.44,"unit":"1","provenance":"measured"},
     "ensemble_n_total":32,"ensemble_n_hit":14,
     "snr":{"value":3.3,"unit":"1","provenance":"measured"},
     "vlm_proposed":false,"vlm_confirmation":null,
     "flags":["below_confirm_agreement"], "…":"…"},

    {"id":"sp_06","lane_index":2,"status":"proposed_unconfirmed",
     "y_px":{"value":null,"unit":"px","provenance":"refused",
       "refusal":{"code":"E_VLM_UNCONFIRMED",
         "message":"The model reported a band here; the pixels do not support one.",
         "remedy":"Inspect lane R at 42% of plate height in the review screen.",
         "evidence":{"proposed_y_frac":0.420,"pixel_support_sigma":1.8,"required_sigma":3.0}}},
     "rst":{"value":null,"unit":"1","provenance":"refused",
       "refusal":{"code":"E_VLM_UNCONFIRMED","message":"Unconfirmed hypothesis.",
                  "remedy":"Inspect visually.","evidence":{}}},
     "ensemble_agreement":{"value":0.09,"unit":"1","provenance":"measured"},
     "ensemble_n_total":32,"ensemble_n_hit":3,
     "vlm_proposed":true,
     "vlm_confirmation":{"proposed_y_frac":0.420,"pixel_support_sigma":1.8,"confirmed":false},
     "flags":["vlm_hallucination_candidate"]},

    {"id":"sp_07","lane_index":3,"status":"confirmed",
     "y_px":{"value":611.9,"unit":"px","provenance":"measured","method":"emg_fit","ci95":[610.8,613.0]},
     "rst":{"value":1.0,"unit":"1","provenance":"inferred",
            "method":"anchor_by_definition","ci95":[1.0,1.0],
            "note":"this spot defines the Rst scale; its interval is degenerate by construction"},
     "rst_reference_spot_id":"sp_07",
     "ensemble_agreement":{"value":0.78,"unit":"1","provenance":"measured"},
     "ensemble_n_total":32,"ensemble_n_hit":25,
     "snr":{"value":8.4,"unit":"1","provenance":"measured"},
     "vlm_proposed":false,"vlm_confirmation":null,"flags":[], "…":"…"}
  ],

  "flags": [
    {"code":"green_clipping_high","severity":"block",
     "message":"34.1% of in-plate green pixels read 255. Optical density is undefined there.",
     "remedy":"Re-shoot 1-2 stops darker or with a shorter shutter.",
     "evidence":{"green_clip_frac_in_plate":0.341,"gate":0.15}},
    {"code":"frame_overrun","severity":"warn",
     "message":"The plate runs off the bottom edge of the photograph (4.1% of the bottom border).",
     "remedy":"Re-shoot with the whole plate, including the origin line, inside the frame.",
     "evidence":{"bottom":0.041,"gate":0.02}},
    {"code":"no_solvent_front","severity":"warn",
     "message":"No solvent front is drawn, so Rf is not reported. Positions are given as Rst against lane sd.",
     "remedy":"Draw the front in pencil before imaging.","evidence":{}},
    {"code":"low_ensemble_agreement","severity":"warn",
     "message":"The strongest feature on this plate is found by 78% of the 32 pipeline variants. No feature reaches 80%.",
     "remedy":"Treat single-pipeline spot counts from this plate as unreliable.",
     "evidence":{"max_agreement":0.78,"per_lane_max":[0.78,0.75,0.66,0.56]}},
    {"code":"uncalibrated_confidence","severity":"info",
     "message":"Confidence values are withheld until the calibration set exists.",
     "remedy":"Label 30 plates.","evidence":{"labelled_plates":7,"required":30}}
  ],

  "correlations": {
    "hypotheses_tested": 3, "adjustment":"benjamini_hochberg", "fdr_target":0.10,
    "findings": [],
    "suppressed": [
      {"hypothesis_id":"H1_conversion_vs_time","n_plates":7,"n_min_required":20,
       "verdict":"insufficient_data","p_raw":null,
       "confounds_checked":["operator","plate_batch","capture_session","exposure"],
       "confounds_unresolved":["capture_session"],
       "suppressed_reason":{"code":"E_INSUFFICIENT_DATA",
         "message":"7 plates; 20 required, from at least 3 batches.",
         "remedy":"Run and label 13 more plates.","evidence":{"batches_available":1}}},
      {"hypothesis_id":"H2_rst_shift_vs_solvent","n_plates":7,"n_min_required":20,
       "verdict":"insufficient_data","p_raw":null,"confounds_checked":["operator","plate_batch"],
       "confounds_unresolved":[],"suppressed_reason":{"code":"E_INSUFFICIENT_DATA",
         "message":"Only two solvent systems are represented and they are confounded with date.",
         "remedy":"Randomise solvent system within a session.","evidence":{}}},
      {"hypothesis_id":"H3_area_vs_loading","n_plates":0,"n_min_required":20,
       "verdict":"insufficient_data","p_raw":null,"confounds_checked":[],
       "confounds_unresolved":[],"suppressed_reason":{"code":"E_CLIP_PHOTOMETRY",
         "message":"No plate in the corpus has usable photometry.",
         "remedy":"Re-shoot the corpus with correct exposure.","evidence":{"plates_with_full_photometry":0}}}
    ]
  },

  "vlm": {
    "mode":"live","model_id":"gemini-2.5-flash-lite-001",
    "prompt_bundle":{"bands":"v3","lanes":"v4","front":"v2","header":"v3"},
    "n_samples":5,"temperature":1.0,
    "fields":{
      "lane_count":{"value":4,"agreement":1.0,"samples":[4,4,4,4,4]},
      "lane_labels":{"value":["S","co","R","sd"],"agreement":0.8,
                     "disagreements":[{"index":1,"votes":{"co":4,"Co":1}},
                                      {"index":3,"votes":{"sd":4,"Sd":1}}]},
      "bands":{"value":[0.262,0.901],"agreement":0.6,"iqr_frac":0.011},
      "front_present":{"value":false,"agreement":1.0},
      "header_text":{"value":"MEHQ-P29  4hr (30+ GA:MeOH)","agreement":0.4,"flagged_for_review":true}
    },
    "cache":{"hits":0,"misses":5,"bundle_hash":"7b2f19c4ad38…"},
    "cost":{"input_tokens":4820,"output_tokens":610,"cached_tokens":0,"usd":0.000112},
    "attempts":5,"retries":0,"degraded":false
  },

  "refusals": [
    {"code":"E_CLIP_PHOTOMETRY","message":"All photometry suppressed on this plate.",
     "remedy":"Re-shoot with correct exposure.","evidence":{"green_clip_frac_in_plate":0.341}},
    {"code":"E_NO_FRONT","message":"Rf not reported.","remedy":"Draw the front.","evidence":{}},
    {"code":"E_UNCALIBRATED","message":"Confidence withheld.","remedy":"Complete Gate 6 and 7.",
     "evidence":{"labelled_plates":7,"required":30}}
  ],

  "storage": {
    "od_h5":"blobs/runs/run_01JB8Q2M7K4X0ZP3RCN9VH6TAD.h5",
    "image":"blobs/sha256/9f/2c/9f2c41ab…","preview_png":"cache/previews/img_9f2c41ab7de05c83b1a4_1024.png"
  },

  "provenance": {
    "pipeline_version":"1.3.0","schema_version":"1",
    "config_hash":"a71c0e5f9b2d…","config_ref":"config/pipeline/v1.3.0.toml",
    "config_document":{"…":"full parsed config embedded verbatim…"},
    "code_fingerprint":"3d8ba0…","git_commit":"c41f9a2","git_dirty":false,
    "env_fingerprint":"e2907b…","lock_hash":"5fa31c…",
    "libraries":{"python":"3.12.7","numpy":"2.1.3","scipy":"1.14.1","scikit-image":"0.24.0",
                 "h5py":"3.12.1","pillow":"11.0.0"},
    "platform_tag":"linux-x86_64-openblas",
    "seed":11497285633021448106,"seed_derivation":"int(image_sha256[:16],16) ^ config.seed_salt",
    "run_key":"c0d4e1…","result_sha256":"8a4b17…","od_sha256":"f309cc…",
    "replay_of":null,"superseded_by":null,"determinism_tier":"tier1"
  }
}
```

Note what this example demonstrates, deliberately: a plate that produces **positions but no quantities**, an Rf that is a refusal object rather than a number, an Rst anchored to a named spot, a VLM band that failed pixel confirmation and is retained as `proposed_unconfirmed` with its 1.8σ support recorded, a confidence field that refuses because calibration has not happened, and a correlations block whose only honest output at n=7 is "insufficient data".

---

## 7.4 Storage of the 2-D optical-density field

**The OD field is the primary scientific record. The JPEG is an input; the JSON is a conclusion; the OD field is the evidence.**

### 7.4.1 Format decision

| candidate | verdict |
|---|---|
| `.npz` | **Rejected.** No chunking, so a tile viewer must load 12 MB to draw a thumbnail; no attributes; no groups. |
| Zarr v3 | **Rejected for now.** Correct for object-store-scale data; at lab scale a directory store explodes into thousands of chunk files per run and nothing outside Python reads it. Kept as a future `ODStore` backend. |
| Float32 TIFF | **Rejected as the store, adopted as an export.** ImageJ can open it, which is genuinely valuable — so `GET /runs/{id}/od?format=tiff` emits one. But TIFF carries no groups and its metadata story is a swamp. |
| **HDF5 (h5py), one file per run** | **Adopted.** One file, chunked, compressed, groups, typed attributes, readable from Python/MATLAB/ImageJ/R, and stable for twenty years. |

### 7.4.2 Layout

```
runs/run_01JB8Q….h5
  attrs: schema="tlc-od/1", run_id, image_sha256, pipeline_version, config_hash,
         od_sha256, units_od="log10(I0/I)", rectified_shape, created_at, platform_tag
  /od                float32 [H,W]   chunks=(256,256)  gzip=4 + shuffle   # THE record
  /valid_mask        uint8   [H,W]   chunks=(256,256)  gzip=4             # 1 = analysable
  /clip_mask         uint8   [H,W]   chunks=(256,256)  gzip=4             # 1 = source green >= 254
  /background        float32 [H,W]   chunks=(256,256)  gzip=4             # regenerable
  /signal_green      uint16  [H,W]   chunks=(256,256)  gzip=4 + shuffle   # rectified green x 65535
  /densitograms/lane_00..NN  float32 [H]                                  # authoritative traces
  /ensemble/agreement_map    float32 [H,W]  chunks=(256,256) gzip=4       # regenerable
  /ensemble/member_peaks     compound[]     (member_id, lane, y_px, amp)  # regenerable
```

`shuffle` before gzip on float arrays typically buys 25–40% on this kind of smooth field, for free.

### 7.4.3 Integrity and determinism

The determinism anchor is the **decoded array**, never the container bytes: a zlib version bump changes the compressed stream without changing a single value.

```python
od_sha256 = hashlib.sha256(
    np.ascontiguousarray(od, dtype="<f4").tobytes()
).hexdigest()
```

`od_sha256` goes in the HDF5 attrs, in the result JSON, and on the run row. `tlc verify --run <id>` re-reads and re-hashes. A nightly job verifies a rotating 5% sample of the archive and alarms on mismatch — this is bit-rot detection, and on a NAS over five years it will eventually fire.

### 7.4.4 Retention

| dataset | retention | rationale |
|---|---|---|
| original image blob | **forever**, content-addressed, deduped | it is the raw observation; ALCOA+ "Original" |
| `/od`, `/valid_mask`, `/clip_mask`, `/densitograms` | **forever** | the scientific record; a re-derivation from JPEG is not the same object |
| `/background`, `/ensemble/*`, `/signal_green` | **180 days**, then dropped by `tlc gc` | deterministic functions of `/od`'s inputs; regenerable by replay |
| result JSON | forever, immutable, content-addressed | |
| preview PNGs, thumbnails | pure cache, evict at will | |
| VLM response cache | forever | it is what makes replay possible (§7.9.5) |

Sizing: a 1104×812 float32 field is 3.6 MB raw, ~1.4 MB after shuffle+gzip; the full `.h5` with masks and previews lands near 3 MB. At 2,000 plates/year that is **~6 GB/year with everything, ~3 GB/year after the 180-day trim**. This is a non-problem, which is precisely why "forever" is the correct answer for the primary arrays.

Deletion is never implicit. `DELETE /runs/{id}` does not exist. `POST /runs/{id}/supersede` marks a run superseded with a reason and a `superseded_by`; the bytes remain.

---

## 7.5 API surface

Base: `/api/v1`. Auth: `Authorization: Bearer <API_TOKEN>` on everything except `/healthz`. Reviewer attribution: `X-Reviewer-Id: <id>` required on all write endpoints under `/corrections` and `/labels`. All timestamps ISO-8601 UTC with `Z`. All list endpoints are cursor-paginated: `?limit=50&cursor=<opaque>` → `{items: [...], next_cursor: "..."|null, total_estimate: int}`.

### 7.5.1 Images

```
POST /api/v1/images                                   multipart/form-data: file, notes?
 201 {"image_id","sha256","bytes","width_px","height_px","deduplicated":false,
      "capture_qc_preview":{"green_clip_frac_frame":0.118,"tilt_deg":3.7,
                            "verdict_hint":"positions_only",
                            "advice":"34% of the plate looks clipped. Consider re-shooting darker."},
      "existing_runs":[]}
 200 (same body, "deduplicated":true, "existing_runs":[{"run_id","pipeline_version","status"}])
```

The QC preview is computed **synchronously at upload** (it is a ~60 ms operation on a downscaled copy) so the chemist learns the photo is over-exposed while still standing at the UV cabinet. This is the single highest-value latency decision in the API.

```
GET    /api/v1/images/{image_id}                  -> metadata + run list
GET    /api/v1/images/{image_id}/content          -> original bytes, ETag = sha256, immutable cache
GET    /api/v1/images/{image_id}/preview?w=1024   -> PNG, generated once, cached
GET    /api/v1/images?cursor=&limit=&uploaded_after=&has_run=&verdict=
```

### 7.5.2 Runs

```
POST /api/v1/runs
 body {"image_id":"img_…",
       "pipeline_version":"1.3.0"|null,          # null = current default
       "vlm_mode":"live"|"replay"|"off",         # default from RuntimeSettings
       "operator_hints":{                        # optional; each becomes provenance="operator"
          "n_lanes":4,"lane_labels":["S","co","R","sd"],
          "bands":{"header":[0.0,0.26],"label_row":[0.90,1.0]},
          "origin_row_frac":null,"front_row_frac":null},
       "config_override":null,                   # dict; forces version 0.0.0-adhoc, non-validatable
       "priority":"normal"|"high"}
 202 {"run_id","status":"queued","run_key","queue_position":2,"deduplicated":false}
 200 {"run_id","status":"succeeded","deduplicated":true}          # run_key already exists
```

```
GET /api/v1/runs/{run_id}
 200 {"run_id","image_id","status","pipeline_version","config_hash","run_key",
      "created_at","started_at","finished_at","duration_ms","progress":1.0,
      "stage":"done","error":null,
      "summary":{"n_spots_confirmed":4,"n_candidate":1,"n_proposed_unconfirmed":1,
                 "max_ensemble_agreement":0.78,"photometry_mode":"positions_only",
                 "blocking_flags":["green_clipping_high"]},
      "links":{"result":"…/result","od":"…/od","events":"…/events"}}

GET /api/v1/runs/{run_id}/result
 200 <the Result document of §7.3>       ETag: "<result_sha256>", Cache-Control: immutable
 409 {"code":"E_RUN_NOT_COMPLETE","status":"running","stage":"ensemble","progress":0.62}

GET /api/v1/runs/{run_id}/events          text/event-stream
 event: stage   data: {"stage":"geometry","progress":0.15,"detail":"corners: rmse 0.21 px"}
 event: flag    data: {"code":"green_clipping_high","severity":"block"}
 event: done    data: {"status":"degraded","result_sha256":"8a4b17…"}
```

SSE, not WebSockets: the stream is one-directional and one-shot, and SSE reconnects itself.

```
GET /api/v1/runs
 ?image_id= &status= &pipeline_version= &created_after= &created_before=
 &lane_label= &flag= &min_agreement= &photometry_mode=
 &has_corrections=true|false &label_partition=tune|calibrate
 &sample_id=            # matched against the reviewer-confirmed sample id, never the VLM's raw guess
 &sort=created_at:desc|max_agreement:desc
 200 {"items":[{…run summary…}],"next_cursor":null,"total_estimate":143}
```

```
POST /api/v1/runs/{run_id}/replay        (see §7.2.5)
GET  /api/v1/runs/compare?a=&b=&tolerance_rst=0.005   (see §7.2.5)
POST /api/v1/runs/{run_id}/supersede     body {"reason":"…"} -> 200
POST /api/v1/runs/{run_id}/cancel        -> 200 (queued/running only)
```

### 7.5.3 Arrays and traces

```
GET /api/v1/runs/{run_id}/od?format=h5            -> application/x-hdf5, the whole file
GET /api/v1/runs/{run_id}/od?format=tiff          -> float32 TIFF of /od (ImageJ-openable)
GET /api/v1/runs/{run_id}/od?format=png&stretch=p1_p99  -> 8-bit visualisation; header
      X-TLC-Warning: "visualisation only; not a measurement"
GET /api/v1/runs/{run_id}/od/tile?z=&x=&y=        -> 256px PNG tiles for the viewer
GET /api/v1/runs/{run_id}/densitograms/{lane}?format=json|csv
    csv columns: y_px,y_frac,od   (full float32 precision, 6 dp)
```

### 7.5.4 Corrections, labels, export, admin

```
POST /api/v1/runs/{run_id}/corrections            (§7.7.2)
GET  /api/v1/corrections?run_id=&reviewer_id=&status=&image_id=
GET  /api/v1/adjudications?status=open
POST /api/v1/adjudications/{id}/resolve

GET  /api/v1/labels?partition=tune|calibrate      -> label records
GET  /api/v1/labels?partition=holdout             -> 403 without X-Holdout-Token; every call audited
GET  /api/v1/labels/stats                         -> counts, inter-reviewer agreement, partition sizes

POST /api/v1/exports    body {"run_ids":[…] | "filter":{…}, "include":["result","od","image","csv"]}
 202 {"export_id":"exp_…"}
GET  /api/v1/exports/{export_id}                  -> status, then a signed local path
GET  /api/v1/exports/{export_id}/download         -> .zip

GET  /api/v1/pipeline/versions                    -> [{version,config_hash,released_at,is_default,notes}]
GET  /api/v1/pipeline/config/{version}            -> the config document + its hash
GET  /api/v1/schemas/result/{schema_version}      -> the JSON Schema
GET  /healthz  /readyz  /metrics
```

The export zip is the regulatory package: `manifest.json` (run manifests), `results/*.json`, `od/*.h5`, `images/*`, `spots.csv`, `SHA256SUMS`, and `README.txt` describing how to reproduce (`tlc replay --manifest manifest.json`).

### 7.5.5 Error envelope

Every 4xx/5xx returns the same shape, and it reuses `Refusal` so the frontend has one renderer:

```json
{"error":{"code":"E_CLIP_UNUSABLE","message":"…","remedy":"…",
          "evidence":{"green_clip_frac_in_plate":0.71},
          "request_id":"req_01JB…"}}
```

---

## 7.6 Data model (SQLite DDL)

The **result JSON blob is authoritative**; the flattened tables are a rebuildable index (`tlc reindex`). Never patch a flattened row without regenerating it from the blob.

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE images (
  image_id          TEXT PRIMARY KEY,
  sha256            TEXT NOT NULL UNIQUE,
  bytes             INTEGER NOT NULL,
  mime              TEXT NOT NULL,
  width_px          INTEGER NOT NULL,
  height_px         INTEGER NOT NULL,
  exif_orientation  INTEGER,
  blob_path         TEXT NOT NULL,
  original_filename TEXT,
  uploaded_at       TEXT NOT NULL,
  uploaded_by       TEXT,
  capture_qc_json   TEXT NOT NULL,
  batch_key         TEXT            -- operator-confirmed sample/batch id; drives partitioning
);

CREATE TABLE pipeline_versions (
  version      TEXT PRIMARY KEY,
  config_hash  TEXT NOT NULL,
  config_toml  TEXT NOT NULL,          -- verbatim
  released_at  TEXT NOT NULL,
  is_default   INTEGER NOT NULL DEFAULT 0,
  notes        TEXT
);

CREATE TABLE runs (
  run_id            TEXT PRIMARY KEY,
  run_key           TEXT NOT NULL,
  image_id          TEXT NOT NULL REFERENCES images(image_id),
  pipeline_version  TEXT NOT NULL REFERENCES pipeline_versions(version),
  config_hash       TEXT NOT NULL,
  code_fingerprint  TEXT NOT NULL,
  env_fingerprint   TEXT NOT NULL,
  platform_tag      TEXT NOT NULL,
  vlm_mode          TEXT NOT NULL,
  vlm_bundle_hash   TEXT,
  status            TEXT NOT NULL
      CHECK (status IN ('queued','running','succeeded','degraded','refused','failed','cancelled')),
  stage             TEXT,
  progress          REAL NOT NULL DEFAULT 0.0,
  created_at        TEXT NOT NULL,
  started_at        TEXT,
  finished_at       TEXT,
  duration_ms       INTEGER,
  result_sha256     TEXT,
  result_path       TEXT,
  od_sha256         TEXT,
  od_path           TEXT,
  error_code        TEXT,
  error_json        TEXT,
  replay_of         TEXT REFERENCES runs(run_id),
  superseded_by     TEXT REFERENCES runs(run_id),
  superseded_reason TEXT,
  -- denormalised for list filtering; regenerable from the blob
  n_spots_confirmed INTEGER,
  max_agreement     REAL,
  photometry_mode   TEXT,
  blocking_flags    TEXT            -- JSON array
);
CREATE UNIQUE INDEX ux_runs_key ON runs(run_key)
  WHERE status IN ('succeeded','degraded','refused') AND superseded_by IS NULL;
CREATE INDEX ix_runs_image   ON runs(image_id, created_at DESC);
CREATE INDEX ix_runs_status  ON runs(status, created_at DESC);

CREATE TABLE spots (                  -- INDEX ONLY. Rebuildable. Never the source of truth.
  run_id      TEXT NOT NULL REFERENCES runs(run_id),
  spot_id     TEXT NOT NULL,
  lane_index  INTEGER NOT NULL,
  lane_label  TEXT,
  status      TEXT NOT NULL,
  y_px        REAL, y_frac REAL,
  rst         REAL, rst_lo REAL, rst_hi REAL,
  agreement   REAL, snr REAL,
  area_od_px  REAL,                   -- NULL when refused
  confidence  REAL,
  PRIMARY KEY (run_id, spot_id)
);
CREATE INDEX ix_spots_rst ON spots(rst) WHERE status = 'confirmed';

CREATE TABLE jobs (
  job_id     TEXT PRIMARY KEY,
  run_id     TEXT NOT NULL REFERENCES runs(run_id),
  state      TEXT NOT NULL CHECK (state IN ('queued','leased','done','failed')),
  priority   INTEGER NOT NULL DEFAULT 100,
  attempts   INTEGER NOT NULL DEFAULT 0,
  lease_until TEXT,
  worker_id  TEXT,
  enqueued_at TEXT NOT NULL,
  last_error TEXT
);
CREATE INDEX ix_jobs_ready ON jobs(state, priority, enqueued_at);

CREATE TABLE reviewers (
  reviewer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('reviewer','adjudicator')), active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE corrections (            -- APPEND-ONLY. No UPDATE, no DELETE. Trigger-enforced.
  correction_id  TEXT PRIMARY KEY,
  run_id         TEXT NOT NULL REFERENCES runs(run_id),
  image_id       TEXT NOT NULL REFERENCES images(image_id),
  reviewer_id    TEXT NOT NULL REFERENCES reviewers(reviewer_id),
  viewed_result_sha256 TEXT NOT NULL,   -- exactly what they were looking at
  blind          INTEGER NOT NULL DEFAULT 0,
  ops_json       TEXT NOT NULL,         -- [CorrectionOp]
  review_seconds INTEGER,
  submitted_at   TEXT NOT NULL,
  client_version TEXT
);
CREATE TRIGGER trg_corrections_immutable BEFORE UPDATE ON corrections
  BEGIN SELECT RAISE(ABORT,'corrections are append-only'); END;
CREATE TRIGGER trg_corrections_nodelete BEFORE DELETE ON corrections
  BEGIN SELECT RAISE(ABORT,'corrections are append-only'); END;

CREATE TABLE label_records (          -- ground truth, keyed by IMAGE not run
  label_id       TEXT PRIMARY KEY,
  image_id       TEXT NOT NULL REFERENCES images(image_id),
  status         TEXT NOT NULL CHECK (status IN ('provisional','agreed','adjudicated','disputed')),
  n_reviewers    INTEGER NOT NULL,
  agreement_json TEXT,
  payload_json   TEXT NOT NULL,       -- the canonical labelled truth for this plate
  partition      TEXT NOT NULL CHECK (partition IN ('tune','calibrate','holdout')),
  derived_from   TEXT NOT NULL,       -- JSON array of correction_ids
  created_at     TEXT NOT NULL,
  superseded_by  TEXT REFERENCES label_records(label_id)
);
CREATE UNIQUE INDEX ux_label_current ON label_records(image_id) WHERE superseded_by IS NULL;

CREATE TABLE adjudications (
  adjudication_id TEXT PRIMARY KEY,
  image_id TEXT NOT NULL REFERENCES images(image_id),
  correction_ids TEXT NOT NULL, disagreement_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','resolved','abandoned')),
  resolved_by TEXT REFERENCES reviewers(reviewer_id),
  resolution_json TEXT, opened_at TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE vlm_calls (
  cache_key TEXT PRIMARY KEY,
  run_id TEXT REFERENCES runs(run_id),
  provider TEXT NOT NULL, model_id TEXT NOT NULL,
  prompt_id TEXT NOT NULL, prompt_version TEXT NOT NULL,
  schema_id TEXT NOT NULL, schema_version TEXT NOT NULL,
  crop_sha256 TEXT NOT NULL, crop_rule_version TEXT NOT NULL,
  sample_index INTEGER NOT NULL, temperature REAL NOT NULL,
  request_json TEXT NOT NULL, response_json TEXT, response_sha256 TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
  cost_usd REAL, latency_ms INTEGER, attempts INTEGER, error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE audit_log (              -- ALCOA+ / Part 11 §11.10(e)
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  before_sha256 TEXT, after_sha256 TEXT, detail_json TEXT
);
```

**Migrate to Postgres when — and only when —** any of: more than one machine writes; the DB exceeds ~20 GB; two chemists routinely review simultaneously from different sites; or you need row-level access control on the hold-out. Until then, SQLite is the right answer and swapping it early is the classic over-engineering failure.

### 7.6.1 Worker and queue

```python
# tlc/jobs/worker.py  (sketch)
def lease_one(db, worker_id: str) -> Job | None:
    with db.begin_immediate():                 # BEGIN IMMEDIATE: serialises leasing
        row = db.execute(
            "SELECT job_id FROM jobs WHERE state='queued' "
            "   OR (state='leased' AND lease_until < :now) "
            "ORDER BY priority, enqueued_at LIMIT 1", {"now": utcnow()}).first()
        if not row: return None
        db.execute("UPDATE jobs SET state='leased', worker_id=:w, attempts=attempts+1,"
                   " lease_until=:until WHERE job_id=:j",
                   {"w": worker_id, "until": utcnow_plus(seconds=900), "j": row.job_id})
        return load(row.job_id)
```

Leases, not locks: a crashed worker's job returns to the queue after 15 minutes. `attempts >= 3` → `failed` with the last error; never retry forever. Pipeline work runs in a `ProcessPoolExecutor` child with `determinism` imported first; the parent handles DB and SSE. Graceful shutdown drains leases.

---

## 7.7 The human-in-the-loop correction loop

This is the critical path (Gate 6, F12). Design it for **reviewer minutes**, because reviewer minutes are the scarce resource in the whole project.

### 7.7.1 Principles

1. **A correction never mutates a run.** A run is what the pipeline produced, forever. The "reviewed view" is `result ⊕ corrections`, composed at read time.
2. **Labels are keyed by image, not by run.** Truth is a property of the plate; a pipeline version is a property of an attempt to read it. A label survives every pipeline upgrade — which is exactly what makes cross-version accuracy comparison possible.
3. **The reviewer's view is recorded.** `viewed_result_sha256` pins what they were looking at. Without it a correction is uninterpretable six months later.
4. **Blind review is a first-class mode.** Inter-reviewer agreement measured on reviewers who both saw the machine's answer is inflated by anchoring, and the inflated number would then be used to certify the machine. Second reviews default to `blind=true`: image plus densitogram, no overlay, no spot list.

### 7.7.2 The correction document

```python
class OpSpotConfirm(BaseModel):   op: Literal["spot.confirm"]; spot_id: str
class OpSpotReject(BaseModel):    op: Literal["spot.reject"];  spot_id: str
                                  reason: Literal["artefact","handwriting","background",
                                                  "duplicate","edge","other"]; note: str|None
class OpSpotMove(BaseModel):      op: Literal["spot.move"];    spot_id: str; y_frac: float
class OpSpotAdd(BaseModel):       op: Literal["spot.add"];     lane_index: int; y_frac: float
                                  strength: Literal["strong","faint","trace"]; note: str|None
class OpLaneRelabel(BaseModel):   op: Literal["lane.relabel"]; lane_index: int; label: str
class OpLaneSetCount(BaseModel):  op: Literal["lane.set_count"]; n_lanes: int
                                  x_centers_frac: list[float]|None
class OpLaneStreak(BaseModel):    op: Literal["lane.mark_streaking"]; lane_index: int; streaking: bool
class OpBandSet(BaseModel):       op: Literal["band.set"]; kind: Literal["header","label_row","footer"]
                                  y0_frac: float; y1_frac: float
class OpOriginSet(BaseModel):     op: Literal["origin.set"]; y_frac: float
class OpFrontSet(BaseModel):      op: Literal["front.set"]; y_frac: float|None   # None = confirmed absent
class OpSampleId(BaseModel):      op: Literal["sample.set_id"]; sample_id: str; conditions: str|None
class OpPlateReject(BaseModel):   op: Literal["plate.reject"]
                                  reason: Literal["not_a_plate","unreadable","wrong_experiment","other"]
                                  note: str|None

CorrectionOp = Annotated[Union[…], Field(discriminator="op")]

class CorrectionDoc(BaseModel):
    run_id: str
    viewed_result_sha256: str
    blind: bool = False
    ops: list[CorrectionOp]
    review_seconds: int | None = None
    reviewer_comment: str | None = None
```

Positions are submitted in `y_frac` of the **rectified** plate, never in source pixels: the rectification is deterministic and versioned, so `y_frac` survives a re-run whereas a source pixel does not.

`POST /api/v1/runs/{run_id}/corrections` returns `201 {"correction_id", "label_status":"provisional"|"agreed"|"disputed", "adjudication_id": null|"adj_…"}`.

### 7.7.3 From corrections to ground truth

On every correction submit, the promoter runs:

```
1. Materialise this reviewer's TRUTH for the image:
     truth = apply_ops(result_at(viewed_result_sha256), ops)
     -> canonical form: {n_lanes, lane_labels[], bands, origin_y_frac, front_y_frac|absent,
                         spots: [{lane_index, y_frac, strength}], plate_rejected, sample_id}
     Machine spots the reviewer neither confirmed nor rejected are recorded as `unreviewed`,
     NOT as implicitly confirmed. Silence is not assent.
2. Collect all non-superseded reviewer truths for this image.
3. If exactly one -> label_records(status='provisional'). Usable for tuning. NEVER for hold-out.
4. If two or more -> compute agreement (§7.7.4).
     agreed      -> status='agreed',  payload = merge (median y_frac per matched spot)
     disagreed   -> status='disputed', open an adjudication
5. Adjudicator resolves -> status='adjudicated', payload = adjudicator's truth,
     agreement_json retains the original disagreement (never overwritten).
6. Assign partition (§7.7.5) on FIRST promotion only; it is then frozen for the image's lifetime.
```

### 7.7.4 Agreement metric

Spot matching between two reviewers is greedy nearest-neighbour within a lane at tolerance `Δy_frac ≤ 0.015` (≈ 17 px on a 1104-px plate — roughly one spot FWHM, and roughly human reading precision per F9). Reported:

```
{ "lane_count_agree": true,
  "lane_label_agree": 4/4,
  "spot_count_delta_per_lane": [0,0,1,0],
  "matched": 4, "only_a": 1, "only_b": 0,
  "jaccard": 0.80,
  "position_delta_rst": {"mean":0.008,"max":0.019,"n":4},
  "origin_delta_frac": 0.004,
  "verdict": "disputed",              # rule below
  "krippendorff_alpha_positions": 0.91 }
```

**Agreed** iff: lane count equal, labels equal, per-lane spot-count delta ≤ 0 for `strong` spots, Jaccard ≥ 0.8, and max matched position delta ≤ 0.02 Rst. Anything else is **disputed**. Faint/trace spots are matched but a disagreement confined to `trace` spots produces `agreed_with_trace_dissent`, which is usable for tuning but excluded from the recall denominator — because "did a human see a trace band" is genuinely not a well-posed question, and pretending it is would poison the metric.

`GET /api/v1/labels/stats` reports inter-reviewer agreement with a bootstrap 95% interval and an explicit `n_double_labelled`, which is the number Gate 6 asks for. Report Krippendorff's α for spot presence on a common grid alongside raw agreement: raw agreement on a sparse detection task is flattering and α is not.

### 7.7.5 Partitioning — grouped, deterministic, growth-stable

```python
def partition_for(batch_key: str, salt: str) -> Literal["tune","calibrate","holdout"]:
    h = int(hashlib.sha256(f"{salt}:{batch_key}".encode()).hexdigest()[:8], 16) % 100
    return "tune" if h < 60 else ("calibrate" if h < 80 else "holdout")
```

- **Grouped by `batch_key`, not by image.** Plates from one reaction, one session, one operator, one camera share nuisance structure; splitting them across partitions leaks. `batch_key` comes from the reviewer-confirmed sample id root (e.g. `MEHQ-P32` groups `plate2`, `plate3`, `plate5`), falling back to `capture_session` (date + operator) when the id is `UNREADABLE`.
- **Hash-based, so adding plates never moves existing ones.** A shuffle-on-growth split silently invalidates every previously reported accuracy number.
- `salt` lives in the config and is versioned; changing it is a MAJOR event and requires re-reporting every metric.
- **Only `agreed` or `adjudicated` labels may enter `holdout`.** `provisional` single-reviewer labels are tune-only.

### 7.7.6 Hold-out protection (mechanical, not a promise)

1. Hold-out label payloads live in a **separate SQLite file**, `${TLC_HOLDOUT_DIR}/holdout.sqlite`, whose path is only in the environment, never in `config/`.
2. Access requires `HOLDOUT_TOKEN`; every read appends to `holdout_access_log` (`who, when, why, n_records`) and is echoed into `EVALUATION.md`. If the log shows 40 reads during Phase 7, the hold-out was a tuning set and the report must say so.
3. **Import guard:** `holdout/` is a top-level package outside `tlc/`. A CI test walks the import graph and fails if anything under `tlc/pipeline/`, `tlc/vlm/` or `tlc/api/` reaches it. Only `tools/evaluate.py` may import it.
4. `tlc labels export --partition holdout` refuses without `--i-am-running-final-evaluation` and a reason string.

### 7.7.7 Making review fast

Target **under 90 seconds per plate.** Concretely: keyboard-only operation (`j/k` between spots, `y/n` confirm/reject, `a` add at cursor, `Enter` submit); the densitogram and the plate crop side by side with a linked cursor; the machine's spots pre-selected so the common case is four keystrokes; and a queue endpoint `GET /api/v1/review/next?reviewer_id=&mode=blind|open` that hands out the next plate by an explicit policy — first fill `n_double_labelled` to 10, then maximise coverage of distinct `batch_key`s, then prefer plates where `max_agreement` is in the ambiguous 0.4–0.7 band, since those are the ones the calibration actually needs.

---

## 7.8 (reserved — merged into §7.6)

---

## 7.9 The VLM integration layer

§10 governs what is asked and which model. This section governs the plumbing. **NN1 holds throughout: nothing here produces a number that reaches the record.**

### 7.9.1 Provider abstraction

```python
# tlc/vlm/provider.py
class VLMRequest(BaseModel):
    prompt_id: str; prompt_version: str          # e.g. "bands", "v3"
    schema_id: str; schema_version: str
    crop: CropSpec                               # rule + resulting box in source px
    crop_bytes_sha256: str
    model_id: str
    temperature: float
    max_output_tokens: int
    sample_index: int

class VLMResponse(BaseModel):
    parsed: dict                                 # validated against the declared schema
    raw_text: str
    response_sha256: str
    input_tokens: int; output_tokens: int; cached_tokens: int
    cost_usd: float
    latency_ms: int
    attempts: int
    from_cache: bool

class VLMProvider(Protocol):
    name: str
    async def complete(self, req: VLMRequest, image: bytes) -> VLMResponse: ...
```

Implementations: `AnthropicProvider` (tool-use with `input_schema`), `GeminiProvider` (`responseSchema` + `responseMimeType="application/json"`), `ReplayProvider` (cache-only; raises `E_VLM_CACHE_MISS` on a miss — **never** falls through to the network), `NullProvider` (returns the all-`UNREADABLE` document; used by `vlm_mode="off"`).

`CropSpec` carries `rule_version`, because a changed crop rule must invalidate the cache:

```python
class CropSpec(BaseModel):
    rule_id: Literal["header_band","label_row","full_plate"]
    rule_version: str                 # "v2"
    box_src_px: tuple[int,int,int,int]
    resample: Literal["none"]         # F13/F15: crop at native resolution, never upscale
```

### 7.9.2 Structured output and validation

The JSON Schema for each `schema_id` is a committed file. Every enum contains `UNREADABLE` (§10.3). On receipt:

```
parse -> pydantic validate
  ok            -> return
  ValidationErr -> ONE repair attempt: resend with the validator's error text appended
                   and temperature unchanged; on second failure the sample is discarded
                   and counted in `vlm.invalid_samples`. It does NOT count toward agreement.
```

If fewer than 3 of 5 samples validate, the field abstains with `E_VLM_INVALID_OUTPUT`.

### 7.9.3 Sampling and aggregation

- **5 samples at `temperature=1.0`.** Temperature 0 would produce five near-identical samples and an agreement of 1.0 that means nothing; self-consistency requires diversity to be informative. This is a common and expensive mistake — write it into a comment at the call site.
- **Categorical fields** (lane labels, lane count, front present): modal vote; `agreement = mode_count / n_valid`; abstain if `agreement < 0.6` or `mode == "UNREADABLE"`.
- **Quasi-continuous fields** (band fractions): median across samples; report IQR; abstain if `IQR > 0.05` of plate height.
- **Free text** (header): modal after normalisation (case-fold, collapse whitespace); always `flagged_for_review=true` regardless of agreement — F8 says this field is not solved, and the schema should never let it look solved.

`vlm_bundle_hash = sha256(canonical_json(sorted(cache_keys) + sorted(response_sha256s)))`. This is what makes a VLM-informed run replayable despite `temperature=1.0`: the *cache* is the determinism mechanism, and the bundle hash pins exactly which responses were used.

### 7.9.4 Retry, timeout, circuit breaker, budget

| policy | value |
|---|---|
| connect timeout | 5 s |
| read timeout | 60 s per sample |
| total VLM budget per run | 300 s wall, `max_cost_usd_per_run = 0.05` |
| retry on | 408, 429, 500, 502, 503, 504, connection error, read timeout |
| no retry on | 400, 401, 403, 422 (except the single schema-repair attempt) |
| backoff | exponential with **full jitter**: `sleep = uniform(0, min(30, 1.0 * 2**attempt))` |
| max attempts | 4 per sample |
| honour `Retry-After` | yes, when present, capped at 60 s |
| circuit breaker | 5 consecutive provider failures → open 120 s → half-open with 1 probe |

**Degradation, never failure.** When the budget is exhausted or the breaker is open, the run does not fail. It completes with `vlm.degraded = true`, `annotation_bands[*].provenance = "convention"` (from the config defaults), lane geometry from `uniform_grid` seeded by the operator hint or the config `default_n_lanes`, and a plate flag `vlm_unavailable` whose remedy tells the chemist to check the lane assignment. A readout with honestly-labelled assumed geometry is worth more than a 502.

### 7.9.5 Caching

```
cache_key = sha256(canonical_json({
  "provider": "google", "model_id": "gemini-2.5-flash-lite-001",
  "prompt_id": "bands", "prompt_version": "v3",
  "schema_id": "bands", "schema_version": "v2",
  "crop_rule": "header_band", "crop_rule_version": "v2",
  "crop_bytes_sha256": "…",          # the CROP's hash, not the image's
  "temperature": 1.0, "max_output_tokens": 512,
  "sample_index": 3,
}))
```

Keying on the crop's bytes (not the source image) is the load-bearing detail: it means a changed crop rule correctly misses, and an unchanged crop rule correctly hits even if the source image was re-uploaded under a new id. Cache rows live in `vlm_calls` and are **never evicted** — they are part of the reproducibility record, and 5 rows × a few KB per plate is nothing.

Re-runs are therefore free in both money and time, and `tlc replay` on a year-old run reproduces the exact model output that informed it even if the model has since been retired.

### 7.9.6 Offline determinism

- `TLC_VLM_MODE ∈ {live, replay, off}`, default `replay` in tests, `live` in the app.
- `conftest.py` installs a socket guard: `socket.socket.connect` raises `NetworkDisabledInTests` unless the test is marked `@pytest.mark.live_vlm` (excluded from the default run). This *proves* Gate 8's offline requirement rather than asserting it.
- The provider registry raises at construction time if `mode != "live"` and a live provider is requested — you cannot accidentally hit the network from a replay run.
- Fixtures: `tests/fixtures/vlm_cache.sqlite`, committed, populated by `tlc vlm record --corpus dataset/`, containing every response for the whole corpus. The full suite runs from it with the network down.
- A separate **nightly** job (`pytest -m live_vlm`) hits real endpoints with a fixed prompt-regression set and diffs against the recorded responses. Silent model updates are a real risk; this is how you find out.

---

## 7.10 Configuration and secrets

### 7.10.1 Two config objects, one hashed

| | `PipelineConfig` | `RuntimeSettings` |
|---|---|---|
| Source | `config/pipeline/v<semver>.toml`, in git | environment / `.env` |
| Contains | every number that can change a result | paths, ports, tokens, worker count, log level |
| Hashed into `run_key` | **yes** | **no** |
| Mutable | never, once released | freely |
| Loaded by | `tlc.config.loader.load_pipeline(version)` | `pydantic_settings.BaseSettings` |

If a value can change a number, it belongs on the left. If it can only change where bytes land or how fast, it belongs on the right. There is no third category, and "just this one env var in the pipeline" is how reproducibility dies.

### 7.10.2 The pipeline config (excerpt, real values)

```toml
# config/pipeline/v1.3.0.toml   IMMUTABLE ONCE RELEASED
version = "1.3.0"
schema_version = "1"
seed_salt = 20260826

[capture_gates]
green_clip_max          = 0.15   # above -> photometry_mode = positions_only          (F1)
green_clip_unusable     = 0.60   # above -> refuse the plate entirely
frame_overrun_max       = 0.02
tilt_max_deg            = 12.0
min_plate_area_frac     = 0.05

[geometry]
plate_hue_range         = [0.20, 0.52]
plate_min_saturation    = 0.10
plate_min_value_floor   = 0.35
erosion_base_px         = 2
erosion_per_deg_tilt    = 0.55   # M-007: not a fixed constant
idempotency_max_px      = 0.5

[photometry]
signal_channel          = "green"                 # F: green carries the signal
od_transform            = "log10"                 # kubelka_munk FORBIDDEN in this regime (F5)
od_epsilon              = 1e-4
primary_background      = "poly3"                 # D-014
primary_radius_frac     = 0.12
sigma_method            = "mad_prespot"           # F4: measured ONCE, before masking
sigma_max_relative_spread = 0.15                  # Gate 3

[ensemble]                                        # F6: 4 x 4 x 2 = 32 members
radii_px      = [8, 16, 24, 35]
models        = ["poly3", "iterative", "gauss", "median"]   # rolling_ball excluded (F5)
thresholds_sigma = [3.0, 5.0]
match_tol_px  = 6
confirm_agreement   = 0.70    # >= -> confirmed
candidate_agreement = 0.30    # >= -> candidate; below -> rejected
# NOTE: on the cleanest plate the observed max agreement was 0.78. Do not raise
# confirm_agreement above 0.78 without re-measuring; it would confirm nothing.

[peaks]
model            = "emg"                          # F11
prominence_sigma = 3.0
tail_ratio_max   = 3.0        # tau/sigma above this -> streak, lane not quantified
max_peaks_per_lane = 8

[lanes]
default_n_lanes     = 4
allowed_labels      = ["S", "co", "R", "sd", "blank", "UNREADABLE"]
halfwidth_frac      = 0.55    # of the lane pitch
refine_window_frac  = 0.05    # search radius around the VLM/operator seed  (F10)

[bands]                       # used only when the VLM is unavailable         (F7)
default_header   = [0.00, 0.26]
default_label_row = [0.90, 1.00]

[rst]
anchor_rule       = "highest_confirmed_spot_in_standard_lane"
anchor_lane_label = "sd"
bootstrap_n       = 1000      # consumes the seeded RNG only

[correlations]
fdr_target = 0.10
min_plates = 20
min_batches = 3
hypotheses = ["H1_conversion_vs_time", "H2_rst_shift_vs_solvent", "H3_area_vs_loading"]

[vlm]
model_id       = "gemini-2.5-flash-lite-001"
n_samples      = 5
temperature    = 1.0
agreement_min  = 0.60
band_iqr_max   = 0.05
prompt_bundle  = { bands = "v3", lanes = "v4", front = "v2", header = "v3" }
max_cost_usd_per_run = 0.05

[labels]
partition_salt   = "mstack-2026-08"
split            = { tune = 60, calibrate = 20, holdout = 20 }
match_tol_rst    = 0.015
agree_jaccard_min = 0.80
```

Every one of these numbers is echoed into the result's `provenance.config_document` and, where it directly gates a reported value, into the result body as a `provenance="chosen"` quantity. A chemist reading the JSON can see the gate that fired and the threshold it fired against, without opening a config file.

### 7.10.3 Runtime settings

```python
class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TLC_", env_file=".env", extra="forbid")
    data_dir: Path = Path("./data")
    db_url: str = "sqlite:///./data/tlc.sqlite"
    blob_dir: Path = Path("./data/blobs")
    holdout_dir: Path | None = None
    default_pipeline_version: str = "1.3.0"
    vlm_mode: Literal["live","replay","off"] = "live"
    vlm_provider: Literal["google","anthropic"] = "google"
    worker_processes: int = 3
    api_token: SecretStr
    holdout_token: SecretStr | None = None
    google_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    offline: bool = False
    log_level: str = "INFO"
```

### 7.10.4 Secrets

- **Environment only.** `.env` is gitignored; `.env.example` is committed with empty values and a comment per key. No secret ever enters SQLite, a result JSON, an export bundle, or a log line.
- `SecretStr` everywhere, plus a `structlog` processor that redacts any value matching a key pattern (`*_key`, `*_token`, `authorization`) — belt and braces, because the one time it leaks will be in an exception's `repr`.
- Startup asserts: if `vlm_mode == "live"` and the relevant key is unset, the app **refuses to start** rather than silently degrading every run.
- No secrets service, no vault, no KMS. This is a lab tool on a LAN.

### 7.10.5 Fully offline operation

`TLC_OFFLINE=1` forces `vlm_mode=replay`, disables every outbound HTTP client at construction, and prints a startup banner naming the mode. `make offline-check` runs `docker run --network=none` over the full suite plus a corpus replay, and asserts every `result_sha256` matches the committed golden file. That command is Gate 8's and Gate 10's evidence, and it should run in CI on every commit.

---

## 7.11 What NOT to build

Named explicitly, because an autonomous agent with time will build all of these unprompted:

1. **No user accounts, roles, or OAuth.** A bearer token and a reviewer-id list.
2. **No Redis, no Celery, no Docker Compose stack of five services.** One process, one SQLite file, one blob directory.
3. **No Kubernetes, no autoscaling, no S3.** `LocalFSStore` and a nightly `rsync` to the NAS.
4. **No GraphQL.** The frontend needs eight endpoints.
5. **No websocket layer.** SSE for progress, done.
6. **No plugin architecture for background models.** Five functions in one module and a dict.
7. **No ORM-level soft-delete framework.** Runs are superseded; corrections are append-only; that is the whole model.
8. **No image "enhancement" endpoint, no thumbnail super-resolution, not even for the viewer.** F15. A viewer that sharpens is a viewer that lies, and someone will screenshot it into a report.
9. **No caching layer in front of SQLite.** It answers a run list in under a millisecond.
10. **No multi-tenancy.** One lab.

The build agent's instinct will be to add these because they look like engineering. They are the opposite: every one of them is a component that must be validated under §7.2.6 and none of them moves any gate.

---

## 7.12 Definition of done for the backend (Gate 10 evidence)

- `schemas/result_v1.schema.json` is generated from the Pydantic tree, committed, and **100% of outputs validate against it** — asserted in the worker, not just in tests.
- Every scientific scalar in every emitted result carries a `provenance` of `measured`, `chosen`, `inferred` or `refused`. A test walks the schema tree and fails if any `Q[...]` field is missing one (NN2 enforced mechanically).
- `tlc replay --all --assert-identical` reproduces `result_sha256` for every historical run, twice, on two containers (NN5).
- `make offline-check` passes with `--network=none`.
- `GET /runs/compare` produces a structured diff for any two runs of the same image, and `reports/comparability_*.md` exists for every version bump.
- Every plate in `dataset/` yields either a result or a refusal carrying a code, a message and a remedy — **zero silent nulls, zero NaNs, zero zeros standing in for "unknown"** (NN3).
- The hold-out import guard test is green, and `holdout_access_log` is empty or its every row is explained in `EVALUATION.md`.