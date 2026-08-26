"""Grouped, deterministic, growth-stable partitioning — §7.7.5, verbatim.

``batch_key_for``: the reviewer-confirmed sample-id ROOT groups plates from one
reaction. The root is defined as the first two dash-separated tokens
(``"MEHQ-P32-4hr-..." -> "MEHQ-P32"``); a sample id with fewer than two tokens is its
own root. ``None`` / empty / ``"UNREADABLE"`` sample ids fall back to
``capture_session`` (date + operator). If neither is available a ``ValueError`` is
raised: an image with no group must not be silently partitioned on its own.
"""

import hashlib
from typing import Literal

Partition = Literal["tune", "calibrate", "holdout"]
UNREADABLE = "UNREADABLE"


def partition_for(batch_key: str, salt: str) -> Partition:
    h = int(hashlib.sha256(f"{salt}:{batch_key}".encode()).hexdigest()[:8], 16) % 100
    return "tune" if h < 60 else ("calibrate" if h < 80 else "holdout")


def sample_id_root(sample_id: str) -> str:
    parts = [p for p in sample_id.strip().split("-") if p]
    return "-".join(parts[:2]) if len(parts) >= 2 else "-".join(parts)


def batch_key_for(sample_id: str | None, capture_session: str | None) -> str:
    if sample_id and sample_id.strip() and sample_id.strip().upper() != UNREADABLE:
        return sample_id_root(sample_id)
    if capture_session and capture_session.strip():
        return capture_session.strip()
    raise ValueError("batch_key_for: neither a readable sample_id nor a capture_session")


def eligible_for_holdout(status: str) -> bool:
    """Only ``agreed`` or ``adjudicated`` labels may enter holdout; provisional is tune-only."""
    return status in ("agreed", "adjudicated")


def effective_partition(batch_key: str, salt: str, status: str) -> Partition:
    """Hash partition, demoted to ``tune`` when the label is not hold-out eligible.

    The hash value itself never changes (growth stability); only the label's
    eligibility gates whether the image may be SERVED as hold-out.
    """
    p = partition_for(batch_key, salt)
    if p == "holdout" and not eligible_for_holdout(status):
        return "tune"
    return p
