"use client";

import { useState } from "react";
import type { Assignment, RunResult } from "@/lib/types";
import { api } from "@/lib/api";

const IDENTITY_COLOUR: Record<string, string> = {
  starting_material: "#8a6d3b",
  product: "#1f4e8c",
  impurity: "#6b7280",
  origin_residue: "#6b7280",
  unassigned: "#6b7280",
};

/** The plate as it ran: origin at the bottom, front at the top, nothing cropped. A band is never a
 *  dot — it is a tick at the fitted centre plus a translucent band spanning the interval, drawn to
 *  scale across the lane, so the uncertainty occupies real plate distance. */
export function PlateStack({
  res,
  assignments,
  runId,
}: {
  res: RunResult;
  assignments: Assignment[];
  runId: string;
}) {
  const [layers, setLayers] = useState({ bands: true, intervals: true, origin: true, hatch: true });
  const [h, w] = res.geometry.rectified_shape;
  const byId = new Map(assignments.map((a) => [a.band_id, a]));
  const toggle = (k: keyof typeof layers) => setLayers((s) => ({ ...s, [k]: !s[k] }));

  return (
    <div>
      <div className="relative w-full border border-line bg-paper2">
        {/* eslint-disable-next-line @next/next/no-img-element -- the plate must be served exactly as
            the pipeline rectified it; Next's optimiser would re-encode the pixels a chemist is judging */}
        <img src={api.plateUrl(runId)} alt="the plate, rectified as it ran" className="block w-full" />
        <svg
          viewBox={`0 0 ${w} ${h}`}
          preserveAspectRatio="none"
          className="pointer-events-none absolute inset-0 h-full w-full"
        >
          {res.lanes.map((L) => {
            const x = L.x_center_px.value ?? 0;
            const hw = L.half_width_px.value ?? 0;
            return (
              <g key={L.index}>
                {layers.hatch && !L.quantified ? (
                  <rect
                    x={x - hw}
                    y={0}
                    width={2 * hw}
                    height={h}
                    fill="rgba(51,80,107,0.10)"
                    stroke="#33506b"
                    strokeDasharray="4 3"
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null}
              </g>
            );
          })}
          {res.spots.map((s) => {
            const L = res.lanes.find((l) => l.index === s.lane_index);
            if (!L || s.y_px.value === null) return null;
            const x = L.x_center_px.value ?? 0;
            const hw = L.half_width_px.value ?? 0;
            const a = byId.get(s.id);
            const colour = a ? IDENTITY_COLOUR[a.identity] : "#1f4e8c";
            const weak = s.status !== "confirmed";
            return (
              <g key={s.id}>
                {layers.intervals && s.y_px.ci95 ? (
                  <rect
                    x={x - hw}
                    y={s.y_px.ci95[0]}
                    width={2 * hw}
                    height={Math.max(0.5, s.y_px.ci95[1] - s.y_px.ci95[0])}
                    fill={colour}
                    opacity={0.18}
                  />
                ) : null}
                {layers.bands ? (
                  <line
                    x1={x - hw}
                    x2={x + hw}
                    y1={s.y_px.value}
                    y2={s.y_px.value}
                    stroke={colour}
                    strokeWidth={weak ? 1 : 1.6}
                    strokeDasharray={weak ? "3 2" : undefined}
                    opacity={weak ? 0.55 : 1}
                    vectorEffect="non-scaling-stroke"
                  />
                ) : null}
              </g>
            );
          })}
          {layers.origin && res.reference.origin_row_px.value !== null ? (
            <line
              x1={0}
              x2={w}
              y1={res.reference.origin_row_px.value}
              y2={res.reference.origin_row_px.value}
              stroke="#16181c"
              strokeDasharray="4 3"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
        </svg>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted">
        {(["bands", "intervals", "origin", "hatch"] as const).map((k) => (
          <label key={k} className="inline-flex cursor-pointer items-center gap-1">
            <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} className="accent-struct" />
            {k === "hatch" ? "lanes not quantified" : k}
          </label>
        ))}
        <span className="ml-auto">
          tick = fitted centre · shaded band = 95% interval, to scale · dashed line = origin (
          {res.reference.origin_provenance.replace(/_/g, " ")})
        </span>
      </div>
    </div>
  );
}
