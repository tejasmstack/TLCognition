import type { Assignment, RunResult } from "@/lib/types";

/** One panel per lane, pixel-registered to the plate beside it: migration is vertical and increases
 *  upward, optical density increases to the right. All panels share one density scale, so a taller
 *  peak is more material and not a different axis. */
export function Densitograms({ res, assignments }: { res: RunResult; assignments: Assignment[] }) {
  const [h] = res.geometry.rectified_shape;
  const max = Math.max(
    0.02,
    ...res.densitograms.flatMap((d) => d.preview.map((v) => (Number.isFinite(v) ? v : 0))),
  );
  const byLane = new Map<number, Assignment[]>();
  for (const a of assignments) {
    const s = res.spots.find((x) => x.id === a.band_id);
    if (!s) continue;
    byLane.set(s.lane_index, [...(byLane.get(s.lane_index) ?? []), a]);
  }

  return (
    <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${res.densitograms.length}, minmax(0,1fr))` }}>
      {res.densitograms.map((d) => {
        const lane = res.lanes.find((l) => l.index === d.lane_index);
        const n = d.preview.length;
        const pts = d.preview
          .map((v, i) => `${(100 * Math.max(0, v)) / max},${(i * h) / Math.max(1, n - 1)}`)
          .join(" ");
        return (
          <div key={d.lane_index} className="border border-line">
            <div className="flex items-baseline gap-1 border-b border-line2 px-2 py-1">
              <span className="text-[11px] font-semibold">L{d.lane_index + 1}</span>
              <span className="text-[11px] text-muted">{lane?.label}</span>
              {lane && !lane.quantified ? (
                <span className="ml-auto text-[9px] uppercase tracking-wide text-muted">areas withheld</span>
              ) : null}
            </div>
            <div className={lane && !lane.quantified ? "hatch-refused" : ""}>
              <svg viewBox={`0 0 100 ${h}`} preserveAspectRatio="none" className="block h-[300px] w-full bg-paper">
                <polyline points={pts} fill="none" stroke="#16181c" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                {(byLane.get(d.lane_index) ?? []).map((a) => {
                  const s = res.spots.find((x) => x.id === a.band_id);
                  if (!s || s.y_px.value === null) return null;
                  return (
                    <line
                      key={a.band_id}
                      x1={0}
                      x2={100}
                      y1={s.y_px.value}
                      y2={s.y_px.value}
                      stroke={a.identity === "product" ? "#1f4e8c" : a.identity === "starting_material" ? "#8a6d3b" : "#9aa1ab"}
                      strokeWidth={1}
                      vectorEffect="non-scaling-stroke"
                    />
                  );
                })}
              </svg>
            </div>
            <div className="px-2 py-1 text-[10px] text-muted">OD →</div>
          </div>
        );
      })}
    </div>
  );
}
