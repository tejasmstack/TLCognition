"""SQLite (WAL) via SQLAlchemy 2.0 — the spec 03 §7.6 DDL, verbatim in substance.

The result JSON blob is authoritative; flattened tables are a rebuildable index. Corrections are
append-only, enforced by triggers (ALCOA+). One writer, one file you can `cp` for a backup.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text

DDL = """
CREATE TABLE IF NOT EXISTS images (
  image_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL, mime TEXT NOT NULL,
  width_px INTEGER NOT NULL, height_px INTEGER NOT NULL, exif_orientation INTEGER, blob_path TEXT NOT NULL,
  original_filename TEXT, uploaded_at TEXT NOT NULL, uploaded_by TEXT, capture_qc_json TEXT NOT NULL, batch_key TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_versions (
  version TEXT PRIMARY KEY, config_hash TEXT NOT NULL, config_toml TEXT NOT NULL, released_at TEXT NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0, notes TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, run_key TEXT NOT NULL, image_id TEXT NOT NULL REFERENCES images(image_id),
  pipeline_version TEXT NOT NULL REFERENCES pipeline_versions(version), config_hash TEXT NOT NULL,
  code_fingerprint TEXT NOT NULL, env_fingerprint TEXT NOT NULL, platform_tag TEXT NOT NULL,
  vlm_mode TEXT NOT NULL, vlm_bundle_hash TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','degraded','refused','failed','cancelled')),
  stage TEXT, progress REAL NOT NULL DEFAULT 0.0, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
  duration_ms INTEGER, result_sha256 TEXT, result_path TEXT, od_sha256 TEXT, od_path TEXT,
  error_code TEXT, error_json TEXT, replay_of TEXT REFERENCES runs(run_id), superseded_by TEXT REFERENCES runs(run_id),
  superseded_reason TEXT, n_spots_confirmed INTEGER, max_agreement REAL, photometry_mode TEXT, blocking_flags TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_key ON runs(run_key)
  WHERE status IN ('succeeded','degraded','refused') AND superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS ix_runs_image ON runs(image_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_status ON runs(status, created_at DESC);
CREATE TABLE IF NOT EXISTS spots (
  run_id TEXT NOT NULL REFERENCES runs(run_id), spot_id TEXT NOT NULL, lane_index INTEGER NOT NULL, lane_label TEXT,
  status TEXT NOT NULL, y_px REAL, y_frac REAL, rst REAL, rst_lo REAL, rst_hi REAL, agreement REAL, snr REAL,
  area_od_px REAL, confidence REAL, PRIMARY KEY (run_id, spot_id)
);
CREATE INDEX IF NOT EXISTS ix_spots_rst ON spots(rst) WHERE status = 'confirmed';
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
  state TEXT NOT NULL CHECK (state IN ('queued','leased','done','failed')), priority INTEGER NOT NULL DEFAULT 100,
  attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT, worker_id TEXT, enqueued_at TEXT NOT NULL, last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_ready ON jobs(state, priority, enqueued_at);
CREATE TABLE IF NOT EXISTS reviewers (
  reviewer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('reviewer','adjudicator')), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
  image_id TEXT NOT NULL REFERENCES images(image_id), reviewer_id TEXT NOT NULL REFERENCES reviewers(reviewer_id),
  viewed_result_sha256 TEXT NOT NULL, blind INTEGER NOT NULL DEFAULT 0, ops_json TEXT NOT NULL,
  review_seconds INTEGER, submitted_at TEXT NOT NULL, client_version TEXT
);
CREATE TRIGGER IF NOT EXISTS trg_corrections_immutable BEFORE UPDATE ON corrections
  BEGIN SELECT RAISE(ABORT,'corrections are append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_corrections_nodelete BEFORE DELETE ON corrections
  BEGIN SELECT RAISE(ABORT,'corrections are append-only'); END;
CREATE TABLE IF NOT EXISTS label_records (
  label_id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(image_id),
  status TEXT NOT NULL CHECK (status IN ('provisional','agreed','adjudicated','disputed')),
  n_reviewers INTEGER NOT NULL, agreement_json TEXT, payload_json TEXT NOT NULL,
  partition TEXT NOT NULL CHECK (partition IN ('tune','calibrate','holdout')), derived_from TEXT NOT NULL,
  created_at TEXT NOT NULL, superseded_by TEXT REFERENCES label_records(label_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_label_current ON label_records(image_id) WHERE superseded_by IS NULL;
CREATE TABLE IF NOT EXISTS adjudications (
  adjudication_id TEXT PRIMARY KEY, image_id TEXT NOT NULL REFERENCES images(image_id),
  correction_ids TEXT NOT NULL, disagreement_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','resolved','abandoned')),
  resolved_by TEXT REFERENCES reviewers(reviewer_id), resolution_json TEXT, opened_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS vlm_calls (
  cache_key TEXT PRIMARY KEY, run_id TEXT REFERENCES runs(run_id), provider TEXT NOT NULL, model_id TEXT NOT NULL,
  prompt_id TEXT NOT NULL, prompt_version TEXT NOT NULL, schema_id TEXT NOT NULL, schema_version TEXT NOT NULL,
  crop_sha256 TEXT NOT NULL, crop_rule_version TEXT NOT NULL, sample_index INTEGER NOT NULL, temperature REAL NOT NULL,
  request_json TEXT NOT NULL, response_json TEXT, response_sha256 TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cached_tokens INTEGER, cost_usd REAL, latency_ms INTEGER, attempts INTEGER, error TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, before_sha256 TEXT, after_sha256 TEXT, detail_json TEXT
);
"""


def make_engine(db_path: Path | str) -> Engine:
    """SQLite engine with WAL, NORMAL sync, foreign keys, busy timeout (spec 03 §7.6 pragmas)."""
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        cur.close()

    return engine


def init_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in [s.strip() for s in DDL.split(";\n") if s.strip()]:
            conn.execute(text(stmt))
