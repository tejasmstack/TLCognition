# Capture-protocol recommendations (from the Phase 0 audit)

The audit (`dataset/AUDIT.md`) shows capture quality — not chemistry and not code — decides
whether a plate can be quantified. 13 of 61 unique images are positions-only because of exposure
clipping. Every item below is cheaper than any software mitigation, and no software can recover
what these lose.

1. **Exposure: keep the green channel off the ceiling.** The plate's UV glow must peak below
   digital saturation — target the brightest plate region at ≤ 250 of 255 in the green channel.
   Where G reads 255, OD = log10(I0/I) is undefined and the error is worst exactly where the
   plate is brightest (F1). One stop of underexposure costs nothing; clipping costs the
   measurement.
2. **Export at native resolution.** The original 7-plate corpus was 71–158 px wide (~15–40 px
   per lane) — at that scale faint-band sensitivity degrades (F13) and text is unreadable by any
   engine (F8: glyphs 3–16 px against documented 12–15 px minimums). Photograph and export
   without downscaling; a phone sensor is more than sufficient.
3. **Whole plate in frame, with margin.** Four of the original seven plates were cut off in
   frame. A cut edge can remove the origin or the standard lane, and with it the Rst reference.
   Leave ~10% background margin on all four sides.
4. **Draw the solvent front in pencil.** Without it Rf is undefined — the same spot reads
   Rf 0.34–0.97 depending on convention (F2). The system reports Rst instead, but a marked front
   restores Rf as a second, portable scale.
5. **Keep the writing out of the chromatography.** Header text and lane labels are fine; ink
   inside the lane region is indistinguishable from analyte by optical density (F7).
6. **PNG or another lossless format, straight from the camera.** JPEG ringing around sharp edges
   can be sharpened into convincing spots by any downstream processing (F15 territory).

Re-run `uv run python scripts/dataset_audit.py` after adding images; it re-derives
`dataset/audit.json` and `dataset/AUDIT.md` from the directory contents.
