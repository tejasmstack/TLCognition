# Research index (Phase 0)

Every PDF in `research/`, two lines each, with a relevance verdict. Deep-read budget spent on:
`TLC_method_evaluation.pdf` (fully extracted to `reference/EVAL_REPORT_EXTRACT.md` — it is the
sole porting source for the lost evaluation bundle, see decisions.md D-002) and
`TLC_System_Design_Visual.pdf` (image-dominant; figures re-examined at the phases that need them).

| File | Verdict | Summary |
|---|---|---|
| `TLC_method_evaluation.pdf` | **binding — fully extracted** | The prior evaluation report (40 pp): 8 densitometry methods, 819 OCR runs, 32-pipeline ensemble, two null tests, resolution/tilt study on the 7 MEHQ plates. Source of the brief's §3 findings; every method parameter transcribed to `reference/EVAL_REPORT_EXTRACT.md` with 31 explicitly-unstated parameters flagged for ASSUMPTIONS. |
| `TLC_Analysis.pdf` | **binding** | In-house problem statement: 4-lane S/Co/R/Sd plate format, five signals to extract, and the decision outputs (progressing / stalled / complete; conversion estimate; by-product flags) with the stall rule ΔSM≈0 ∧ Δproduct≈0 across ~2 intervals. Its Rf-with-front framing is superseded by F2/Rst; the product requirements are not in the brief and bind Phase 9–11 outputs. |
| `TLC_System_Flow.pdf` | **binding** | Behavioural spec: Flow A (single plate) and Flow B (time series), 16 edge-case behaviours (overexposed → quantities OFF, streaks → % withheld with likely cause, missing timepoint → gap never interpolated, different solvents → refuse trend), 5 stated limits. Binds refusal microcopy and series logic. |
| `TLC_System_Design_Visual.pdf` | **binding** | Stage-by-stage design (GATE→…→CONCLUDE) with real prototype outputs; carries the correlation rule set (matrix shift two-way agreement, reference anchoring ≥3σ, impurity inheritance, co-spot decomposition Co≈αS+βR with R² as self-consistency, streak guard >55%) and the two-hard-rejections principle. Its symmetric-Gaussian detail is superseded by F11 (EMG). Figures worth revisiting at Phases 5/9/11. |
| `TLC_Plate_Reading.pdf` | relevant | 17-question field survey: measurement is commodity (30+ tools since 2007), naming is the gap; prescribes deterministic-measurement + LLM-from-numbers-table split. Carries acceptance criteria: agree with blind chemist 9/10, false alarms <5%, one-photo effort — used at Gate 11/12. |
| `TLC_Brief.pdf` | relevant | Market landscape (6 pp): prior tools' shared pipeline and their universal miss (no naming); the cautionary 2024 field-trial (sensitivity up, specificity 75% → not deployable) that motivates the false-alarm-first reporting rule. |
| `TLC_Standard_SOP.pdf` | relevant | Bench SOP for future captures: 14 rules (pencil front within seconds, ≥8 mm spot spacing, loading parity for reference lanes, photo spec 60–80% frame fill) and the software-fixability table (missing front / under-loaded reference / streaks: never software-fixable). Aligns with `reports/CAPTURE_PROTOCOL.md`; defines what future input images look like. |

Notes recorded during indexing:
- The sample prefix is **MEHQ** (the commissioning brief's "MCHQ" was a typo caught by the evaluation report).
- The evaluation's OCR "accuracy" reference transcription was itself a frontier-VLM reading — those CER numbers measure disagreement with a VLM, not truth (NN4 discipline applies).
- The ensemble radii (12/20/35/55 px) and the null-test radii (8/16/35/60 px) are different grids sharing only 35 px — a port must not conflate them.
