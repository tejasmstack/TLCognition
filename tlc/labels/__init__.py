"""Labelling / correction module (§7.7): pure functions, no storage."""

from tlc.labels.agreement import AgreementReport, compare_truths, krippendorff_alpha_nominal
from tlc.labels.corrections import CorrectionDoc, CorrectionOp, CorrectionOpAdapter
from tlc.labels.partition import batch_key_for, eligible_for_holdout, partition_for
from tlc.labels.promote import AdjudicationDraft, LabelRecordDraft, promote, resolve_adjudication
from tlc.labels.stats import label_stats
from tlc.labels.truth import ReviewerTruth, apply_ops, truth_from_doc

__all__ = [
    "AdjudicationDraft",
    "AgreementReport",
    "CorrectionDoc",
    "CorrectionOp",
    "CorrectionOpAdapter",
    "LabelRecordDraft",
    "ReviewerTruth",
    "apply_ops",
    "batch_key_for",
    "compare_truths",
    "eligible_for_holdout",
    "krippendorff_alpha_nominal",
    "label_stats",
    "partition_for",
    "promote",
    "resolve_adjudication",
    "truth_from_doc",
]
