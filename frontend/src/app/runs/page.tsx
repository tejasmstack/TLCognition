import Link from "next/link";

const BACKEND = process.env.TLC_BACKEND ?? "http://127.0.0.1:8811";

interface Row {
  run_id: string;
  created_at: string;
  status: string;
  n_spots_confirmed: number;
  photometry_mode: string;
  original_filename: string | null;
}

export default async function RunsPage() {
  let runs: Row[] = [];
  try {
    const r = await fetch(`${BACKEND}/api/v1/runs?limit=100`, { cache: "no-store" });
    if (r.ok) runs = ((await r.json()) as { runs: Row[] }).runs;
  } catch {
    runs = [];
  }
  return (
    <div>
      <h1 className="text-[22px] font-semibold tracking-[-0.01em]">Plates</h1>
      <p className="mt-1 text-[13px] text-muted">{runs.length} read so far · accuracy not yet computed</p>
      <table className="mt-6 w-full border-collapse text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
            <th className="py-1 pr-3 font-medium">When</th>
            <th className="py-1 pr-3 font-medium">Plate</th>
            <th className="py-1 pr-3 font-medium">Bands</th>
            <th className="py-1 pr-3 font-medium">Photometry</th>
            <th className="py-1 pr-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id} className="border-b border-line2">
              <td className="num py-2 pr-3 text-muted">{r.created_at.slice(0, 16).replace("T", " ")}</td>
              <td className="py-2 pr-3">
                <Link href={`/runs/${r.run_id}`} className="text-accent underline">
                  {r.original_filename ?? r.run_id.slice(4, 12)}
                </Link>
              </td>
              <td className="num py-2 pr-3">{r.n_spots_confirmed}</td>
              <td className="py-2 pr-3 text-muted">{r.photometry_mode}</td>
              <td className="py-2 pr-3 text-muted">{r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!runs.length ? (
        <p className="mt-6 text-[13px] text-muted">
          Nothing yet — or the service is not running. Start it with{" "}
          <code className="font-mono text-[11px]">TLC_DATA_DIR=demo_data uv run uvicorn tlc.api.app:app --port 8811</code>.
        </p>
      ) : null}
    </div>
  );
}
