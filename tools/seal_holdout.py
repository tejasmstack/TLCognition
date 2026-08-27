"""Move hold-out label truths OUT of the database, into a store the pipeline cannot read (Gate 6).

Gate 6 requires the hold-out partition to be "written to a location the pipeline code cannot read".
A `partition = 'holdout'` column in the same SQLite file is a promise, not a location: any query that
forgets the filter reads it, and nothing stops a future fitting path from doing so.

This tool is the location. For every current label record in the hold-out partition it writes the
reviewer truth to `holdout/<image_id>.json` (read-only), then NULLs the payload in the database and
stamps `sealed_at`. The row stays — the system must know a plate is held out, and which — but what
the reviewer said is no longer anywhere `tlc/` can reach. Evaluation reads the sealed files directly,
by hand, at the moment a number is reported.

This file lives in `tools/`, not `tlc/`, and a test asserts that nothing under `tlc/` mentions the
hold-out directory.

    uv run python tools/seal_holdout.py [--data-dir data] [--holdout-dir holdout] [--dry-run]
"""

import argparse
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--holdout-dir", default="holdout")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    from tlc.storage.db import init_schema, make_engine
    from tlc.storage.repositories import Repo

    data_dir = Path(a.data_dir)
    if not (data_dir / "tlc.sqlite").exists():
        print(f"no database at {data_dir}/tlc.sqlite — nothing to seal")
        return 0
    engine = make_engine(data_dir / "tlc.sqlite")
    init_schema(engine)
    repo = Repo(engine)

    out_dir = ROOT / a.holdout_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r for r in repo.label_records(partitions=("holdout",)) if not r.get("sealed_at")]
    if not rows:
        print(f"no unsealed hold-out labels (hold-out dir holds {len(list(out_dir.glob('*.json')))} sealed plates)")
        return 0

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = []
    for r in rows:
        payload = {"label_id": r["label_id"], "image_id": r["image_id"], "status": r["status"],
                   "n_reviewers": r["n_reviewers"], "agreement": json.loads(r["agreement_json"] or "null"),
                   "payload": json.loads(r["payload_json"]), "derived_from": json.loads(r["derived_from"]),
                   "created_at": r["created_at"], "sealed_at": now}
        blob = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        digest = hashlib.sha256(blob.encode()).hexdigest()
        manifest.append({"image_id": r["image_id"], "label_id": r["label_id"], "sha256": digest})
        if a.dry_run:
            print(f"  would seal {r['image_id']} ({r['status']}, {r['n_reviewers']} reviewers)")
            continue
        path = out_dir / f"{r['image_id']}.json"
        if path.exists():
            os.chmod(path, stat.S_IWUSR | stat.S_IRUSR)
        path.write_text(blob)
        os.chmod(path, stat.S_IRUSR)          # read-only: sealing is not something to undo by accident
        repo.seal_holdout(r["label_id"], now)
        try:
            shown = path.relative_to(ROOT)
        except ValueError:      # a store outside the repo is fine, and is what a real lab would use
            shown = path
        print(f"  sealed {r['image_id']} -> {shown}  {digest[:12]}")

    if not a.dry_run:
        mpath = out_dir / "MANIFEST.json"
        existing = json.loads(mpath.read_text()) if mpath.exists() else []
        if mpath.exists():
            os.chmod(mpath, stat.S_IWUSR | stat.S_IRUSR)
        mpath.write_text(json.dumps(existing + manifest, indent=2, sort_keys=True) + "\n")
        os.chmod(mpath, stat.S_IRUSR)
    print(f"{len(manifest)} hold-out label(s) {'would be ' if a.dry_run else ''}sealed at {now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
