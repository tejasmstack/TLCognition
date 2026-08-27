import Link from "next/link";
import type { Reaction, RunResult } from "@/lib/types";
import { Densitograms } from "@/components/Densitograms";
import { PlateStack } from "@/components/PlateStack";
import { Reading } from "@/components/Reading";
import { RefusalCard, Section, Tally, Value } from "@/components/primitives";

const BACKEND = process.env.TLC_BACKEND ?? "http://127.0.0.1:8811";

async function load<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${BACKEND}${path}`, { cache: "no-store" });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

function Capability({ res }: { res: RunResult }) {
  const suppressed = res.lanes.filter((l) => !l.quantified).length;
  const mode = res.photometry.photometry_mode;
  const segs: { key: string; state: "measured" | "partial" | "refused" | "none"; word: string }[] = [
    {
      key: "Positions",
      state: res.status === "refused" || !res.lanes.length ? "refused" : "measured",
      word: res.status === "refused" ? "refused" : "measured",
    },
    mode === "full" && suppressed === 0
      ? { key: "Photometry", state: "measured", word: "measured" }
      : mode === "full" && suppressed < res.lanes.length
        ? { key: "Photometry", state: "partial", word: `partial — areas withheld in ${suppressed} of ${res.lanes.length} lanes` }
        : { key: "Photometry", state: "refused", word: "refused — the plate is too clipped to measure areas" },
    res.lanes.every((l) => l.label_provenance === "operator")
      ? { key: "Identity", state: "measured", word: "confirmed by operator" }
      : { key: "Identity", state: "none", word: "not read" },
    res.reference.rst_anchor
      ? { key: "Scale", state: "measured", word: "Rst — no solvent front, so Rf is not reported" }
      : { key: "Scale", state: "refused", word: "refused — no standard lane to anchor Rst" },
  ];
  return (
    <div className="space-y-[6px]">
      {segs.map((s) => (
        <div key={s.key} className="grid grid-cols-[76px_56px_1fr] items-center gap-2 text-[12px]">
          <span className="text-muted">{s.key}</span>
          <span
            className={`h-[10px] border border-struct ${
              s.state === "measured" ? "bg-struct" : s.state === "refused" ? "hatch-refused" : s.state === "partial" ? "hatch-partial" : "border-dotted"
            }`}
          />
          <span>{s.word}</span>
        </div>
      ))}
    </div>
  );
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const res = await load<RunResult>(`/runs/${id}.json`);
  const reaction = await load<Reaction>(`/runs/${id}/reaction.json`);

  if (!res) {
    return (
      <div className="max-w-[60ch]">
        <h1 className="text-[22px] font-semibold">That plate is not here</h1>
        <p className="mt-2 text-[14px] text-muted">
          No run with id <code className="font-mono text-[12px]">{id}</code>. It may not have finished, or the
          service may not be running.
        </p>
        <Link href="/" className="mt-4 inline-block text-[13px] text-accent underline">
          Read a new plate
        </Link>
      </div>
    );
  }

  const assignments = reaction?.assignments ?? [];
  const K = res.spots[0]?.ensemble_n_total ?? 24;
  const threshold = Math.ceil(0.5 * K);
  const counted = res.spots.filter((s) => s.status === "confirmed");
  const below = res.spots.filter((s) => s.status === "candidate");

  return (
    <div>
      {reaction ? <Reading r={reaction} /> : null}

      <Section title="The plate, as it ran" sub="Origin at the bottom, solvent front at the top, nothing cropped. The densitogram beside each lane is pixel-registered to it.">
        <div className="grid gap-6 lg:grid-cols-[minmax(260px,360px)_minmax(0,1fr)]">
          <PlateStack res={res} assignments={assignments} runId={id} />
          <Densitograms res={res} assignments={assignments} />
        </div>
      </Section>

      <Section
        title={`Bands — ${counted.length} counted, ${below.length} below the threshold`}
        sub="Agreement is a tally of how many independent processing variants found the band. It is ordinal evidence, not a probability: no probability is shown anywhere until the calibration set exists."
      >
        <table className="w-full border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="py-1 pr-3 font-medium">Lane</th>
              <th className="py-1 pr-3 font-medium">Band</th>
              <th className="py-1 pr-3 font-medium">Rst</th>
              <th className="py-1 pr-3 font-medium">Position (px)</th>
              <th className="py-1 pr-3 font-medium">Agreement</th>
              <th className="py-1 pr-3 font-medium">Area (OD·px)</th>
              <th className="py-1 pr-3 font-medium">Read as</th>
            </tr>
          </thead>
          <tbody>
            {[...counted, ...below].map((s) => {
              const lane = res.lanes.find((l) => l.index === s.lane_index);
              const a = assignments.find((x) => x.band_id === s.id);
              const weak = s.status !== "confirmed";
              return (
                <tr key={s.id} className={`border-b border-line2 align-top ${weak ? "text-muted" : ""}`}>
                  <td className={`py-2 pr-3 ${weak ? "border-l border-dotted border-l-struct pl-2" : "border-l-[3px] border-l-struct pl-2"}`}>
                    L{s.lane_index + 1} {lane?.label}
                  </td>
                  <td className="py-2 pr-3 font-mono text-[11px]">{s.id}</td>
                  <td className="py-2 pr-3"><Value q={s.rst} /></td>
                  <td className="py-2 pr-3"><Value q={s.y_px} /></td>
                  <td className="py-2 pr-3">
                    <Tally hit={s.ensemble_n_hit} total={s.ensemble_n_total} threshold={threshold} />
                  </td>
                  <td className="py-2 pr-3"><Value q={s.area_od_px} /></td>
                  <td className="py-2 pr-3">{a ? a.label : <span className="text-muted">—</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {below.length ? (
          <p className="mt-3 max-w-[80ch] text-[12px] text-muted">
            The rows below the rule sit under the threshold of {threshold} of {K}. On blank plates built
            from a plate&rsquo;s own noise, features at that level appear routinely. That does not mean nothing
            is there; it means this photograph cannot tell you.
          </p>
        ) : null}
      </Section>

      {res.refusals.length ? (
        <Section title="What this plate will not tell you" sub="A refusal is a result. Each one says what was measured, what is withheld, why, and what to change about the photograph.">
          <div className="grid gap-3 md:grid-cols-2">
            {res.refusals.map((r) => (
              <RefusalCard key={r.code} r={r} />
            ))}
          </div>
        </Section>
      ) : null}

      <Section title="Capability and provenance">
        <div className="grid gap-8 md:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
          <Capability res={res} />
          <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1 text-[12px]">
            <dt className="text-muted">plate</dt>
            <dd>{res.image.original_filename ?? res.image.sha256.slice(0, 8)}</dd>
            <dt className="text-muted">run</dt>
            <dd className="font-mono text-[11px]">{res.run_id}</dd>
            <dt className="text-muted">pipeline</dt>
            <dd className="font-mono text-[11px]">
              {String(res.provenance.pipeline_version)} · config {String(res.provenance.config_hash).slice(0, 8)}
            </dd>
            <dt className="text-muted">code</dt>
            <dd className="font-mono text-[11px]">
              {String(res.provenance.code_fingerprint).slice(0, 8)} · git {String(res.provenance.git_commit).slice(0, 8)}
              {res.provenance.git_dirty ? "*" : ""}
            </dd>
            <dt className="text-muted">result hash</dt>
            <dd className="font-mono text-[11px]">{String(res.provenance.result_sha256).slice(0, 16)}</dd>
            <dt className="text-muted">semantic layer</dt>
            <dd>{String((res as unknown as { vlm: { mode: string } }).vlm?.mode ?? "off")} — no value it produced is a measurement</dd>
          </dl>
        </div>
        <p className="mt-4 max-w-[80ch] text-[11px] text-muted">
          Re-running this plate at this pipeline version reproduces this result byte for byte; the hash
          above is how you check.
        </p>
      </Section>
    </div>
  );
}
