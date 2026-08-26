"""Run service: image bytes -> Result persisted (spec 03 §7.2, §7.5). The only place that
combines decode, pipeline, assembly, storage. Determinism: `tlc.core.determinism` is imported by
every entry point before numpy; the RNG seed derives from the image hash and config salt.

Idempotency: POST /runs and `tlc run` are idempotent on run_key — a repeat returns the existing
run. Replay: re-running at the recorded version MUST reproduce result_sha256 (E_REPLAY_DRIFT).
"""

import io
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from tlc.assemble import assemble, result_sha256
from tlc.config.loader import load_pipeline
from tlc.core.canonical_json import canonical_json
from tlc.core.hashing import sha256_bytes, sha256_canonical, tree_fingerprint
from tlc.pipeline.configs import Config
from tlc.pipeline.runner import RunConfig, run_plate
from tlc.storage.blobs import LocalFSStore
from tlc.storage.db import init_schema, make_engine
from tlc.storage.odstore import write_run_h5
from tlc.storage.repositories import Repo, utcnow

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PIPELINE_VERSION = "0.5.0"


@dataclass(frozen=True)
class Decoded:
    rgb: np.ndarray
    width: int
    height: int
    exif_orientation: int | None
    mime: str
    decoder: str


def decode_image(data: bytes) -> Decoded:
    """Decode with Pillow, apply EXIF orientation EXPLICITLY and record it (spec 03 §7.1.1)."""
    im = Image.open(io.BytesIO(data))
    mime = Image.MIME.get(im.format or "", "application/octet-stream")
    exif = None
    try:
        exif = im.getexif().get(274)
    except Exception:
        exif = None
    im = ImageOps.exif_transpose(im)
    rgb = np.asarray(im.convert("RGB"))
    return Decoded(rgb, rgb.shape[1], rgb.shape[0], int(exif) if exif else None, mime, f"pillow-{Image.__version__}")


class RunService:
    def __init__(self, data_dir: Path | str, pipeline_version: str = DEFAULT_PIPELINE_VERSION):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = make_engine(self.data_dir / "tlc.sqlite")
        init_schema(self.engine)
        self.repo = Repo(self.engine)
        self.blobs = LocalFSStore(self.data_dir / "blobs")
        self.pipeline_version = pipeline_version
        self.config_doc, self.config_hash, self.config_ref = load_pipeline(pipeline_version)
        grid_path = ROOT / self.config_doc["ensemble"]["grid_ref"]
        self.grid_doc = json.loads(grid_path.read_text())
        self.op_doc = json.loads((ROOT / self.config_doc["operating_point"]["ref"]).read_text())
        self.config_doc = {**self.config_doc, "grid_hash": self.grid_doc["hash"], "operating_point_id": self.op_doc["id"],
                           "gate_thresholds": {k: float(v) for k, v in self.config_doc["capture_gates"].items()}}
        self.repo.ensure_pipeline_version(pipeline_version, self.config_hash, (ROOT / self.config_ref).read_text())

    def run_config(self, n_lanes: int | None, labels: tuple[str, ...] | None) -> RunConfig:
        cfgs, ws = [], []
        for row in self.grid_doc["configs"]:
            m, rest = row["key"].split("@")
            r, sv, ex, pm = rest.split("/")
            cfgs.append(Config(m, int(r), sv, ex, pm))
            ws.append(row["weight"])
        t = self.op_doc["tiers"]
        return RunConfig(grid=tuple(cfgs), weights=tuple(ws), grid_id=self.grid_doc["id"],
                         reported_agreement_min=t["reported"]["agreement_min"], candidate_agreement_min=t["candidate"]["agreement_min"],
                         p_med_max=t["reported"]["p_med_max"], z_med_min=t["reported"]["z_med_min"],
                         n_surrogates=int(self.config_doc["ensemble"]["n_surrogates"]), n_lanes=n_lanes, lane_labels=labels,
                         header_frac=float(self.config_doc["bands"]["header_frac"]),
                         label_row_frac=float(self.config_doc["bands"]["label_row_frac"]))

    def ingest(self, data: bytes, filename: str | None, uploaded_by: str | None = None) -> dict:
        sha = sha256_bytes(data)
        dec = decode_image(data)
        blob_path = self.blobs.path_for(self.blobs.put(data))
        image_id = f"img_{sha[:20]}"
        self.repo.upsert_image(image_id, sha, len(data), dec.mime, dec.width, dec.height, dec.exif_orientation, blob_path,
                               filename, uploaded_by, {})
        return {"image_id": image_id, "sha256": sha, "width_px": dec.width, "height_px": dec.height, "mime": dec.mime}

    def run_key(self, image_sha: str, vlm_bundle_hash: str | None = None) -> tuple[str, str, str]:
        code_fp = tree_fingerprint(ROOT / "tlc" / "pipeline", ROOT / "tlc" / "core")
        lock_hash = sha256_bytes((ROOT / "uv.lock").read_bytes()) if (ROOT / "uv.lock").exists() else "unavailable"
        from tlc.assemble import environment_fingerprint

        env_hash, _, _ = environment_fingerprint(lock_hash)
        return sha256_canonical({"image_sha256": image_sha, "config_hash": self.config_hash, "code_fingerprint": code_fp,
                                 "env_fingerprint": env_hash, "vlm_bundle_hash": vlm_bundle_hash}), code_fp, env_hash

    def run(self, data: bytes, filename: str | None = None, n_lanes: int | None = None,
            labels: tuple[str, ...] | None = None, vlm_mode: str = "off", replay_of: str | None = None,
            expected_result_sha256: str | None = None) -> dict:
        info = self.ingest(data, filename)
        run_key, _, _ = self.run_key(info["sha256"])
        existing = self.repo.existing_run_for_key(run_key)
        if existing and replay_of is None:
            return {"run_id": existing["run_id"], "deduplicated": True, "status": existing["status"],
                    "result_sha256": existing["result_sha256"]}
        t0 = time.time()
        started = utcnow()
        dec = decode_image(data)
        seed = int(info["sha256"][:16], 16) ^ int(self.config_doc.get("seed_salt", 0))
        out = run_plate(dec.rgb, self.run_config(n_lanes, labels), seed=seed)
        run_id = f"run_{uuid.uuid4().hex[:24]}"
        result = assemble(out, data, {"width_px": dec.width, "height_px": dec.height, "mime": dec.mime,
                                      "exif_orientation": dec.exif_orientation, "decoder": f"imageio.v3/{dec.decoder}",
                                      "original_filename": filename},
                          self.config_doc, run_id, utcnow())
        sha_res = result_sha256(result)
        # persist OD + densitograms, then the JSON (with result_sha256 stamped in provenance)
        od_path = None
        if out.od is not None:
            od_path = str(self.data_dir / "runs" / f"{run_id}.h5")
            write_run_h5(od_path, out.od, out.od_valid, {L.index: L.profile for L in out.lanes},
                         {"run_id": run_id, "pipeline_version": self.pipeline_version, "config_hash": self.config_hash})
        d = result.model_dump(mode="json")
        d["provenance"]["result_sha256"] = sha_res
        if replay_of:
            d["provenance"]["replay_of"] = replay_of
        d["storage"] = {"od_h5": od_path, "image": self.blobs.path_for(info["sha256"]), "preview_png": None}
        result_path = self.data_dir / "results" / f"{run_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(canonical_json(d) + "\n")
        drift = expected_result_sha256 is not None and expected_result_sha256 != sha_res
        if replay_of is not None and drift:
            # spec 03 §7.2.5: a replay that does not reproduce result_sha256 is FAILED with E_REPLAY_DRIFT
            self.repo.insert_run(d, str(result_path), od_path, run_key, vlm_mode, started, int((time.time() - t0) * 1000),
                                 replay_of, status_override="failed", error_code="E_REPLAY_DRIFT",
                                 error={"expected": expected_result_sha256, "got": sha_res})
        elif replay_of is not None:
            # identical replay: insert as 'running' (outside the unique-key index), supersede the original
            # (never edited beyond superseded_by), then finalise — the queue's own lifecycle order
            self.repo.insert_run(d, str(result_path), od_path, run_key, vlm_mode, started, int((time.time() - t0) * 1000),
                                 replay_of, status_override="running")
            self.repo.supersede(replay_of, run_id, "replay_identical")
            self.repo.finalize_run_status(run_id, d["status"])
        else:
            self.repo.insert_run(d, str(result_path), od_path, run_key, vlm_mode, started, int((time.time() - t0) * 1000), replay_of)
        return {"run_id": run_id, "deduplicated": False, "status": d["status"], "result_sha256": sha_res,
                "replay_drift": drift, "expected_result_sha256": expected_result_sha256}

    def load_result(self, run_id: str) -> dict | None:
        row = self.repo.get_run(run_id)
        if not row or not row["result_path"]:
            return None
        return json.loads(Path(row["result_path"]).read_text())

    def replay(self, run_id: str) -> dict:
        row = self.repo.get_run(run_id)
        if not row:
            raise KeyError(run_id)
        img = self.repo.image_by_sha(json.loads(Path(row["result_path"]).read_text())["image"]["sha256"])
        data = self.blobs.get(img["sha256"])
        res = json.loads(Path(row["result_path"]).read_text())
        labels = tuple(L["label"] for L in res["lanes"]) if res["lanes"] and res["lanes"][0]["label_provenance"] == "operator" else None
        n_lanes = len(res["lanes"]) if labels else None
        return self.run(data, img["original_filename"], n_lanes=n_lanes, labels=labels, vlm_mode="replay",
                        replay_of=run_id, expected_result_sha256=row["result_sha256"])
