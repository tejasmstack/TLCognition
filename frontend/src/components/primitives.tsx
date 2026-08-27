import type { Provenance, Q, ReactionValue, Refusal } from "@/lib/types";
import { fmt } from "@/lib/api";

/** A provenance chip. It rides with the number, never in a column of its own, so that a number
 *  cropped out of a screenshot cannot lose what qualifies it. */
export function Chip({ p, title }: { p: Provenance; title?: string }) {
  const letter = { measured: "m", chosen: "c", inferred: "i", refused: "r" }[p];
  const style: Record<Provenance, string> = {
    measured: "border-line text-muted",
    chosen: "border-line text-muted",
    inferred: "border-line text-muted",
    refused: "border-dashed border-struct text-struct",
  };
  return (
    <span
      title={title ?? p}
      className={`ml-1 inline-block rounded-[3px] border px-1 align-middle text-[9px] uppercase leading-[14px] ${style[p]}`}
    >
      {letter}
    </span>
  );
}

/** A quantity, or the reason there isn't one. Never a bare centre. */
export function Value({ q, unit }: { q: Q | ReactionValue | null | undefined; unit?: string }) {
  if (!q) return <span className="text-muted">—</span>;
  const refusal = "refusal" in q ? q.refusal : null;
  if (q.value === null || q.provenance === "refused") {
    return (
      <span className="text-muted" title={refusal ? `${refusal.message} ${refusal.remedy}` : "refused"}>
        — <span className="text-[11px]">withheld</span>
      </span>
    );
  }
  const ci = "ci95" in q ? q.ci95 : "interval" in q ? q.interval : null;
  const v = typeof q.value === "number" ? fmt(q.value, ci as [number, number] | null) : String(q.value);
  return (
    <span className="num">
      {v}
      {unit ? <span className="ml-1 text-[11px] text-muted">{unit}</span> : null}
      <Chip p={q.provenance} title={("basis" in q && q.basis) || ("method" in q && q.method) || q.provenance} />
    </span>
  );
}

/** The discrete tally. 25 of 32 invites you to ask which 32; 78% invites arithmetic it cannot carry. */
export function Tally({ hit, total, threshold }: { hit: number; total: number; threshold: number }) {
  return (
    <span className="inline-flex items-center gap-2 align-middle">
      <span className="num w-[52px] text-[12px]">{hit} of {total}</span>
      <span className="inline-flex gap-[1px]" aria-hidden>
        {Array.from({ length: total }, (_, i) => (
          <i
            key={i}
            className={`inline-block h-[11px] w-[3px] border ${
              i < hit ? "border-struct bg-struct" : "border-line bg-transparent"
            } ${i === threshold - 1 ? "relative after:absolute after:-bottom-[5px] after:left-[-1px] after:h-[3px] after:w-[5px] after:bg-ink" : ""}`}
          />
        ))}
      </span>
    </span>
  );
}

/** A refusal is a result: same card, same weight, never red, always ending on a physical action. */
export function RefusalCard({ r, title }: { r: Refusal; title?: string }) {
  return (
    <div className="border border-line border-l-[3px] border-l-struct bg-paper2 p-3">
      <div className="text-[13px] font-semibold">{title ?? r.message}</div>
      {title ? <div className="mt-1 text-[13px]">{r.message}</div> : null}
      <div className="mt-1 text-[13px] text-ink2">
        <span className="font-medium">Next at the bench: </span>
        {r.remedy}
      </div>
      <div className="mt-1 font-mono text-[10px] uppercase tracking-wide text-muted">{r.code}</div>
    </div>
  );
}

export function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">{title}</h2>
      {sub ? <p className="mt-1 max-w-[70ch] text-[13px] text-muted">{sub}</p> : null}
      <div className="mt-3">{children}</div>
    </section>
  );
}
