import type { Reaction, ReactionValue } from "@/lib/types";
import { pct } from "@/lib/api";
import { RefusalCard, Value } from "./primitives";

const VERDICT_WORD: Record<Reaction["verdict"], string> = {
  complete: "reaction complete",
  in_progress: "reaction in progress",
  no_reaction_detected: "no conversion seen",
  cannot_conclude: "cannot conclude",
};

const IDENTITY_STYLE: Record<string, string> = {
  starting_material: "border-l-warn",
  product: "border-l-accent",
  impurity: "border-l-muted border-dotted",
  origin_residue: "border-l-muted border-dashed",
  unassigned: "border-l-line",
};

function bold(text: string, key: number) {
  // the reading marks exactly one sentence — the answer — and nothing else is markup
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g);
  return (
    <p key={key} className="mb-3 max-w-[68ch] text-[15px] leading-[1.6]">
      {parts.map((p, i) =>
        p.startsWith("**") ? (
          <strong key={i} className="font-semibold">{p.slice(2, -2)}</strong>
        ) : p.startsWith("*") && p.endsWith("*") && p.length > 2 ? (
          <em key={i}>{p.slice(1, -1)}</em>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </p>
  );
}

export function Reading({ r }: { r: Reaction }) {
  const conv = r.quantities.apparent_conversion as ReactionValue | undefined;
  const grade = r.confidence.grade;

  return (
    <section className="border border-line border-t-[3px] border-t-struct p-6">
      <div className="flex flex-wrap items-center gap-3">
        <span className="border border-struct px-2 py-[2px] text-[10px] uppercase tracking-[0.1em] text-struct">
          {VERDICT_WORD[r.verdict]}
        </span>
        <span className={`text-[10px] uppercase tracking-[0.08em] ${grade === "high" ? "text-ink2" : "text-muted"}`}>
          {grade} confidence
        </span>
        {grade !== "high" ? (
          <span className="text-[11px] text-muted">— {r.confidence.factors.slice(0, 2).join("; ")}</span>
        ) : null}
      </div>

      <h1 className="mt-3 max-w-[24ch] text-[28px] font-semibold leading-[1.2] tracking-[-0.01em]">
        {r.headline}
      </h1>

      <div className="mt-6 grid gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div>
          <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            What this plate says
          </h2>
          {r.plain_summary.map((p, i) => bold(p, i))}
        </div>

        <div>
          <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            Every band in the reaction lane
          </h2>
          {r.assignments.length ? (
            <table className="w-full border-collapse text-[13px]">
              <thead>
                <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
                  <th className="py-1 pr-2 font-medium">Band</th>
                  <th className="py-1 pr-2 font-medium">Rst</th>
                  <th className="py-1 pr-2 font-medium">Read as</th>
                  <th className="py-1 pr-2 text-right font-medium">Share</th>
                </tr>
              </thead>
              <tbody>
                {r.assignments.map((a) => (
                  <tr key={a.band_id} className="border-b border-line2 align-top">
                    <td className={`border-l-[3px] py-2 pl-2 pr-2 font-mono text-[11px] ${IDENTITY_STYLE[a.identity]}`}>
                      {a.band_id}
                    </td>
                    <td className="num py-2 pr-2">{a.rst?.toFixed(3) ?? "—"}</td>
                    <td className="py-2 pr-2">
                      <div className="font-medium">{a.label}</div>
                      <div className="mt-[2px] text-[11px] leading-snug text-muted">{a.basis}</div>
                      {a.confidence !== "high" ? (
                        <div className="mt-[2px] text-[11px] text-muted">
                          {a.confidence} confidence — {a.factors.slice(0, 2).join("; ")}
                        </div>
                      ) : null}
                    </td>
                    <td className="num py-2 pr-2 text-right">
                      {a.share_of_lane.value !== null ? (
                        pct(a.share_of_lane.value as number)
                      ) : (
                        <span className="text-[11px] text-muted">withheld</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-[13px] text-muted">No band in the reaction lane could be identified.</p>
          )}

          {conv ? (
            <div className="mt-4 border-l-[3px] border-l-struct bg-paper2 p-3 text-[13px]">
              {conv.value !== null ? (
                <>
                  <span className="font-semibold">Apparent conversion: {pct(conv.value as number)}</span>{" "}
                  <span className="text-muted">
                    — product against starting material, inside this lane only. A share of UV darkness,
                    not of moles.
                  </span>
                  {typeof r.quantities.linear_range_note === "string" ? (
                    <span className="text-muted"> {r.quantities.linear_range_note}</span>
                  ) : null}
                </>
              ) : (
                <>
                  <span className="font-semibold">Conversion not reported.</span>{" "}
                  <span className="text-muted">
                    {conv.refusal?.message} {conv.refusal?.remedy}
                  </span>
                </>
              )}
            </div>
          ) : null}
        </div>
      </div>

      {r.impurities.length ? (
        <div className="mt-8">
          <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">The other bands</h2>
          <ul className="max-w-[80ch] space-y-1 text-[13px]">
            {r.impurities.map((i) => (
              <li key={i.band_id}>
                <span className="num text-muted">Rst {i.rst?.toFixed(3) ?? "—"}</span> — {i.reading}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <details className="mt-8 border-t border-line2 pt-3">
        <summary className="text-[12px] text-muted">How this was worked out — the chemist&rsquo;s version</summary>
        <ul className="mt-2 max-w-[90ch] space-y-1 text-[13px]">
          {r.chemist_summary.map((line, i) => (
            <li key={i}>{line.replace(/\*\*/g, "")}</li>
          ))}
        </ul>
        {r.cospot.available ? (
          <p className="mt-2 max-w-[90ch] text-[12px] text-muted">
            Co-spot decomposition: co ≈ {r.cospot.alpha_S}·S + {r.cospot.beta_R}·R, R² ={" "}
            {r.cospot.r_squared}. {r.cospot.reading}
          </p>
        ) : null}
        <p className="mt-2 max-w-[90ch] text-[12px] text-muted">
          Matrix shift: <Value q={r.matrix_shift.applied} unit="Rst" /> — {r.matrix_shift.applied.basis}. Matching
          tolerance {r.matrix_shift.tolerance?.toFixed(3)} Rst.
        </p>
      </details>

      <div className="mt-6 grid gap-6 border-t border-line2 pt-4 md:grid-cols-2">
        <div>
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
            What would change this answer
          </h3>
          <ul className="mt-2 max-w-[60ch] list-disc space-y-1 pl-4 text-[13px]">
            {r.what_would_change_this.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
          {r.next_experiment ? (
            <p className="mt-3 max-w-[60ch] text-[13px]">
              <span className="font-medium">Suggested next step: </span>
              {r.next_experiment}
            </p>
          ) : null}
        </div>
        <div>
          <details>
            <summary className="text-[12px] text-muted">Caveats that always apply</summary>
            <ul className="mt-2 max-w-[60ch] list-disc space-y-1 pl-4 text-[12px] text-muted">
              {r.caveats.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </details>
          <details className="mt-2">
            <summary className="text-[12px] text-muted">Words used here</summary>
            <dl className="mt-2 max-w-[60ch] text-[12px]">
              {Object.entries(r.glossary).map(([k, v]) => (
                <div key={k} className="mb-2">
                  <dt className="font-medium">{k}</dt>
                  <dd className="text-muted">{v}</dd>
                </div>
              ))}
            </dl>
          </details>
        </div>
      </div>

      {r.refusals.length ? (
        <div className="mt-6 space-y-2">
          {r.refusals.map((x) => (
            <RefusalCard key={x.code} r={x} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
