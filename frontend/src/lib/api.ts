import type { Reaction, RunResult } from "./types";

const base = "/backend";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${base}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return (await r.json()) as T;
}

export const api = {
  plateUrl: (runId: string) => `${base}/runs/${runId}/plate.png`,
  result: (runId: string) => get<RunResult>(`/runs/${runId}.json`),
  reaction: (runId: string) => get<Reaction>(`/runs/${runId}/reaction.json`),
  findings: (runId: string) => get<unknown[]>(`/runs/${runId}/findings.json`),
  runs: () => get<{ runs: Record<string, unknown>[] }>(`/api/v1/runs`),
  async upload(file: File, nLanes: number, labels: string[]): Promise<string> {
    const fd = new FormData();
    fd.set("file", file);
    fd.set("n_lanes", String(nLanes));
    fd.set("labels", labels.join(","));
    const r = await fetch(`${base}/upload`, { method: "POST", body: fd, redirect: "manual" });
    // the backend answers a form post with 303 to /runs/{id}; opaqueredirect hides the header, so the
    // run is found by asking for the newest run instead
    if (r.type === "opaqueredirect" || r.status === 0 || r.status === 303) {
      const list = await get<{ runs: { run_id: string }[] }>(`/api/v1/runs?limit=1`);
      if (list.runs.length) return list.runs[0].run_id;
    }
    if (!r.ok) throw new Error(`upload failed: ${r.status}`);
    const list = await get<{ runs: { run_id: string }[] }>(`/api/v1/runs?limit=1`);
    return list.runs[0].run_id;
  },
};

/** The one formatter. Round the value so its last digit sits at the decade of the half-interval's
 *  first significant digit; never print an interval finer than the value; never print a bare centre. */
export function fmt(value: number | null | undefined, ci?: [number, number] | null): string {
  if (value === null || value === undefined) return "—";
  if (ci) {
    const half = Math.abs(ci[1] - ci[0]) / 2;
    const nd = half > 0 ? Math.max(0, Math.min(6, -Math.floor(Math.log10(half)))) : 3;
    return `${value.toFixed(nd)} ±${half.toFixed(nd)}`;
  }
  return `≈${value.toFixed(2)}`;
}

export function pct(x: number | null | undefined, digits = 0): string {
  return x === null || x === undefined ? "—" : `${(100 * x).toFixed(digits)}%`;
}
