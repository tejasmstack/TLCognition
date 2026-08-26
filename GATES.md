# Gate reviews

Per spec 05 §12.9: each phase gate is checked by an agent that did not write the code, working
only from the committed evidence. Pass/fail with specific numbers, appended here. A gate that
"passes" without committed numeric evidence has not passed.

---
## Gate 0 · Audit and orientation
**Reviewed:** 2026-08-26, independent agent, at commit db77eb1.
**Initial verdict: FAIL** on item 4 (CI red: ruff I001 in `scripts/dataset_audit.py`, committed
without re-running CI — see mistakes.md M-001; the auto-fix then introduced M-002, a silent
reorder of the determinism import below numpy, now guarded by
`tests/test_determinism_import_order.py`).
**Items 1–3, 5–6 PASSED with evidence:**
- audit.json: 66/66 PNGs covered, all five required fields non-null, 3 records spot-checked
  against pixels (dims + sha256 match). Verdicts: 35 photometry_ok / 15 partial / 16
  positions_only (per-file, duplicates included).
- INDEX.md: 7/7 PDFs.
- decisions.md D-005: explicit MIXED keep/port/discard with file:line reasons.
- Determinism: audit.json sha256 byte-identical across two re-runs
  (27eb4fd7… before, after run 1, after run 2).
- Reviewer caveat: evidence files cited by D-005 (`reference/*.json`, `GATES.md`) were untracked
  at review time — remediated by committing them with the fix.
**Remediation commit:** (see git log — lint fix, import-order guard test, reference files
tracked). Re-check of item 4 by the same reviewer follows below.
