"""Persistence of runs, images, spots index, corrections, labels — SQLAlchemy Core statements
over the §7.6 DDL. Every mutation of a record that must be attributable is also written to
audit_log; runs are never UPDATEd after completion except to set superseded_by."""

import json
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from tlc.core.hashing import sha256_bytes


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class Repo:
    def __init__(self, engine: Engine):
        self.engine = engine

    # -- audit
    def audit(self, conn, actor: str, action: str, entity_type: str, entity_id: str, detail: dict | None = None,
              before: str | None = None, after: str | None = None) -> None:
        conn.execute(text("INSERT INTO audit_log(at, actor, action, entity_type, entity_id, before_sha256, after_sha256, detail_json)"
                          " VALUES (:at,:actor,:action,:et,:eid,:b,:a,:d)"),
                     {"at": utcnow(), "actor": actor, "action": action, "et": entity_type, "eid": entity_id,
                      "b": before, "a": after, "d": json.dumps(detail or {}, sort_keys=True)})

    # -- images
    def upsert_image(self, image_id: str, sha256: str, nbytes: int, mime: str, w: int, h: int, exif: int | None,
                     blob_path: str, filename: str | None, uploaded_by: str | None, capture_qc: dict,
                     batch_key: str | None = None) -> bool:
        with self.engine.begin() as conn:
            row = conn.execute(text("SELECT image_id FROM images WHERE sha256=:s"), {"s": sha256}).first()
            if row:
                return False
            conn.execute(text("INSERT INTO images(image_id, sha256, bytes, mime, width_px, height_px, exif_orientation, blob_path,"
                              " original_filename, uploaded_at, uploaded_by, capture_qc_json, batch_key)"
                              " VALUES (:id,:s,:b,:m,:w,:h,:e,:p,:f,:t,:u,:q,:k)"),
                         {"id": image_id, "s": sha256, "b": nbytes, "m": mime, "w": w, "h": h, "e": exif, "p": blob_path,
                          "f": filename, "t": utcnow(), "u": uploaded_by, "q": json.dumps(capture_qc, sort_keys=True), "k": batch_key})
            self.audit(conn, uploaded_by or "system", "image.upload", "image", image_id, {"sha256": sha256})
            return True

    def image_by_sha(self, sha256: str) -> dict | None:
        with self.engine.begin() as conn:
            r = conn.execute(text("SELECT * FROM images WHERE sha256=:s"), {"s": sha256}).mappings().first()
            return dict(r) if r else None

    # -- pipeline versions
    def ensure_pipeline_version(self, version: str, config_hash: str, config_toml: str, is_default: bool = True) -> None:
        with self.engine.begin() as conn:
            if not conn.execute(text("SELECT 1 FROM pipeline_versions WHERE version=:v"), {"v": version}).first():
                conn.execute(text("INSERT INTO pipeline_versions(version, config_hash, config_toml, released_at, is_default)"
                                  " VALUES (:v,:h,:t,:r,:d)"),
                             {"v": version, "h": config_hash, "t": config_toml, "r": utcnow(), "d": 1 if is_default else 0})

    # -- runs
    def existing_run_for_key(self, run_key: str) -> dict | None:
        with self.engine.begin() as conn:
            r = conn.execute(text("SELECT * FROM runs WHERE run_key=:k AND status IN ('succeeded','degraded','refused')"
                                  " AND superseded_by IS NULL"), {"k": run_key}).mappings().first()
            return dict(r) if r else None

    def insert_run(self, result: dict, result_path: str, od_path: str | None, run_key: str, vlm_mode: str,
                   started_at: str, duration_ms: int, replay_of: str | None = None,
                   status_override: str | None = None, error_code: str | None = None, error: dict | None = None) -> None:
        prov = result["provenance"]
        spots = result["spots"]
        confirmed = [s for s in spots if s["status"] == "confirmed"]
        agreements = [s["ensemble_agreement"]["value"] for s in spots if s["ensemble_agreement"]["value"] is not None]
        blocking = [f["code"] for f in result["flags"] if f["severity"] == "block"]
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO runs(run_id, run_key, image_id, pipeline_version, config_hash, code_fingerprint,"
                              " env_fingerprint, platform_tag, vlm_mode, vlm_bundle_hash, status, progress, created_at, started_at,"
                              " finished_at, duration_ms, result_sha256, result_path, od_sha256, od_path, replay_of,"
                              " n_spots_confirmed, max_agreement, photometry_mode, blocking_flags, error_code, error_json)"
                              " VALUES (:rid,:rk,:iid,:pv,:ch,:cf,:ef,:pt,:vm,:vb,:st,1.0,:ca,:sa,:fa,:dm,:rs,:rp,:os,:op,:ro,:ns,:ma,:pm,:bf,:ec,:ej)"),
                         {"rid": result["run_id"], "rk": run_key, "iid": result["image_id"], "pv": prov["pipeline_version"],
                          "ch": prov["config_hash"], "cf": prov["code_fingerprint"], "ef": prov["env_fingerprint"],
                          "pt": prov["platform_tag"], "vm": vlm_mode, "vb": None, "st": status_override or result["status"],
                          "ca": result["created_at"], "sa": started_at, "fa": utcnow(), "dm": duration_ms,
                          "rs": prov["result_sha256"], "rp": result_path, "os": prov["od_sha256"], "op": od_path, "ro": replay_of,
                          "ns": len(confirmed), "ma": max(agreements) if agreements else None,
                          "pm": result["photometry"]["photometry_mode"], "bf": json.dumps(blocking),
                          "ec": error_code, "ej": json.dumps(error, sort_keys=True) if error else None})
            for s in spots:
                rst = s["rst"]
                ci = rst.get("ci95") or (None, None)
                conn.execute(text("INSERT INTO spots(run_id, spot_id, lane_index, lane_label, status, y_px, y_frac, rst, rst_lo, rst_hi,"
                                  " agreement, snr, area_od_px, confidence) VALUES (:r,:s,:l,:ll,:st,:y,:yf,:rst,:lo,:hi,:a,:snr,:area,:c)"),
                             {"r": result["run_id"], "s": s["id"], "l": s["lane_index"],
                              "ll": next((L["label"] for L in result["lanes"] if L["index"] == s["lane_index"]), None),
                              "st": s["status"], "y": s["y_px"]["value"], "yf": s["y_frac"]["value"], "rst": rst["value"],
                              "lo": ci[0], "hi": ci[1], "a": s["ensemble_agreement"]["value"], "snr": s["snr"]["value"],
                              "area": s["area_od_px"]["value"], "c": s["confidence"]["value"]})
            self.audit(conn, "worker", "run.complete", "run", result["run_id"], {"status": result["status"]},
                       after=prov["result_sha256"])

    def supersede(self, old_run_id: str, new_run_id: str, reason: str) -> None:
        """The only permitted post-completion change to a run row (ALCOA+: superseded, never edited)."""
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE runs SET superseded_by=:n, superseded_reason=:r WHERE run_id=:o"),
                         {"n": new_run_id, "r": reason, "o": old_run_id})
            self.audit(conn, "worker", "run.supersede", "run", old_run_id, {"by": new_run_id, "reason": reason})

    def finalize_run_status(self, run_id: str, status: str) -> None:
        """Lifecycle transition (running -> terminal) — the queue's normal path, not an edit of a result."""
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE runs SET status=:s WHERE run_id=:r AND status IN ('queued','running')"), {"s": status, "r": run_id})

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.begin() as conn:
            r = conn.execute(text("SELECT * FROM runs WHERE run_id=:r"), {"r": run_id}).mappings().first()
            return dict(r) if r else None

    def list_runs(self, limit: int = 50, offset: int = 0, status: str | None = None) -> list[dict]:
        q = "SELECT r.*, i.original_filename FROM runs r JOIN images i ON i.image_id=r.image_id"
        params: dict = {"lim": limit, "off": offset}
        if status:
            q += " WHERE r.status=:st"
            params["st"] = status
        q += " ORDER BY r.created_at DESC LIMIT :lim OFFSET :off"
        with self.engine.begin() as conn:
            return [dict(r) for r in conn.execute(text(q), params).mappings().all()]

    # -- reviewers / corrections / labels
    def ensure_reviewer(self, reviewer_id: str, display_name: str, role: str = "reviewer") -> None:
        with self.engine.begin() as conn:
            if not conn.execute(text("SELECT 1 FROM reviewers WHERE reviewer_id=:r"), {"r": reviewer_id}).first():
                conn.execute(text("INSERT INTO reviewers(reviewer_id, display_name, role) VALUES (:r,:d,:ro)"),
                             {"r": reviewer_id, "d": display_name, "ro": role})

    def insert_correction(self, correction_id: str, run_id: str, image_id: str, reviewer_id: str, viewed_sha: str,
                          blind: bool, ops: list[dict], review_seconds: int | None, client_version: str | None) -> None:
        payload = json.dumps(ops, sort_keys=True)
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO corrections(correction_id, run_id, image_id, reviewer_id, viewed_result_sha256, blind,"
                              " ops_json, review_seconds, submitted_at, client_version) VALUES (:c,:r,:i,:rv,:v,:b,:o,:s,:t,:cv)"),
                         {"c": correction_id, "r": run_id, "i": image_id, "rv": reviewer_id, "v": viewed_sha, "b": 1 if blind else 0,
                          "o": payload, "s": review_seconds, "t": utcnow(), "cv": client_version})
            self.audit(conn, reviewer_id, "correction.submit", "correction", correction_id,
                       {"run_id": run_id, "blind": blind}, after=sha256_bytes(payload.encode()))

    def corrections_for_image(self, image_id: str) -> list[dict]:
        with self.engine.begin() as conn:
            return [dict(r) for r in conn.execute(text("SELECT * FROM corrections WHERE image_id=:i ORDER BY submitted_at"),
                                                  {"i": image_id}).mappings().all()]

    def upsert_label_record(self, label_id: str, image_id: str, status: str, n_reviewers: int, agreement: dict | None,
                            payload: dict, partition: str, derived_from: list[str]) -> None:
        with self.engine.begin() as conn:
            prev = conn.execute(text("SELECT label_id FROM label_records WHERE image_id=:i AND superseded_by IS NULL"),
                                {"i": image_id}).first()
            if prev:
                # the old row must point at the new id before the insert (one-current-per-image index), but the new id
                # does not exist yet: defer the FK check to commit for this transaction only (SQLite supports this).
                conn.execute(text("PRAGMA defer_foreign_keys = ON"))
                conn.execute(text("UPDATE label_records SET superseded_by=:n WHERE label_id=:p"), {"n": label_id, "p": prev[0]})
            conn.execute(text("INSERT INTO label_records(label_id, image_id, status, n_reviewers, agreement_json, payload_json,"
                              " partition, derived_from, created_at) VALUES (:l,:i,:s,:n,:a,:p,:pa,:d,:t)"),
                         {"l": label_id, "i": image_id, "s": status, "n": n_reviewers,
                          "a": json.dumps(agreement, sort_keys=True) if agreement is not None else None,
                          "p": json.dumps(payload, sort_keys=True), "pa": partition, "d": json.dumps(derived_from), "t": utcnow()})
            self.audit(conn, "promoter", "label.promote", "label", label_id, {"status": status, "partition": partition})

    def label_records(self, current_only: bool = True, partitions: tuple[str, ...] | None = None) -> list[dict]:
        """`partitions` restricts the result — fitting code must pass ('tune', 'calibrate') so that a
        hold-out plate cannot reach a model even before the payloads are sealed away (Gate 6)."""
        wheres = ["superseded_by IS NULL"] if current_only else []
        params: dict = {}
        if partitions:
            wheres.append("partition IN (" + ",".join(f":p{i}" for i in range(len(partitions))) + ")")
            params.update({f"p{i}": p for i, p in enumerate(partitions)})
        q = "SELECT * FROM label_records" + (" WHERE " + " AND ".join(wheres) if wheres else "")
        with self.engine.begin() as conn:
            return [dict(r) for r in conn.execute(text(q), params).mappings().all()]

    def seal_holdout(self, label_id: str, sealed_at: str) -> None:
        """Drop the truth payload from the database once it has been written outside it. The row
        stays — the system must know a plate is held out — but what the reviewer said is gone from
        anywhere the pipeline can reach."""
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE label_records SET payload_json='{}', sealed_at=:t WHERE label_id=:l"),
                         {"t": sealed_at, "l": label_id})
            self.audit(conn, "holdout", "label.seal", "label", label_id, {"sealed_at": sealed_at})
