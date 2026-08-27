"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

const ROLES = ["S", "R", "co", "sd", "blank", "other"] as const;
const GUESS = ["S", "R", "co", "sd"];

interface QC {
  width: number;
  height: number;
  clipPct: number;
  sha8: string;
  thumb: string;
}

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [qc, setQc] = useState<QC | null>(null);
  const [nLanes, setNLanes] = useState<number | "">("");
  const [labels, setLabels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const analyse = useCallback(async (f: File) => {
    setFile(f);
    setError(null);
    const buf = await f.arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", buf);
    const sha8 = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 8);
    const img = new Image();
    img.src = URL.createObjectURL(f);
    await img.decode();
    const c = canvasRef.current!;
    const scale = Math.min(320 / img.naturalWidth, 420 / img.naturalHeight, 1);
    c.width = Math.round(img.naturalWidth * scale);
    c.height = Math.round(img.naturalHeight * scale);
    const ctx = c.getContext("2d")!;
    ctx.drawImage(img, 0, 0, c.width, c.height);
    // clipping in the green channel over the central 80%, painted magenta — magenta does not occur
    // on a 254 nm plate, so there is no ambiguity about what is overlay and what is sample
    const d = ctx.getImageData(0, 0, c.width, c.height);
    let n = 0;
    let clipped = 0;
    const x0 = c.width * 0.1;
    const x1 = c.width * 0.9;
    const y0 = c.height * 0.1;
    const y1 = c.height * 0.9;
    for (let y = 0; y < c.height; y++) {
      for (let x = 0; x < c.width; x++) {
        const i = (y * c.width + x) * 4;
        if (x >= x0 && x < x1 && y >= y0 && y < y1) {
          n++;
          if (d.data[i + 1] >= 254) {
            clipped++;
            d.data[i] = 255;
            d.data[i + 1] = 0;
            d.data[i + 2] = 255;
          }
        }
      }
    }
    ctx.putImageData(d, 0, 0);
    setQc({ width: img.naturalWidth, height: img.naturalHeight, clipPct: (100 * clipped) / Math.max(1, n), sha8, thumb: c.toDataURL() });
  }, []);

  const setLaneCount = (n: number) => {
    setNLanes(n);
    setLabels(Array.from({ length: n }, (_, i) => GUESS[i] ?? "other"));
  };

  const submit = async () => {
    if (!file || !nLanes) return;
    setBusy(true);
    setError(null);
    try {
      const runId = await api.upload(file, nLanes as number, labels);
      router.push(`/runs/${runId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  const clipBad = qc && qc.clipPct > 15;

  return (
    <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div>
        <h1 className="text-[26px] font-semibold tracking-[-0.01em]">Read a plate</h1>
        <p className="mt-2 max-w-[62ch] text-[14px] text-muted">
          One photograph, four lanes. The system measures what it can, refuses what it cannot, and tells
          you which is which.
        </p>

        <label
          className="mt-6 flex h-[180px] cursor-pointer items-center justify-center border border-dashed border-line bg-paper2 text-[13px] text-muted hover:border-struct"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            const f = e.dataTransfer.files?.[0];
            if (f) void analyse(f);
          }}
        >
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void analyse(f);
            }}
          />
          {file ? (
            <span>
              <span className="font-medium text-ink">{file.name}</span> — click or drop to replace
            </span>
          ) : (
            <span>Drop a photograph here, or click to choose. The original bytes are stored untouched.</span>
          )}
        </label>

        <div className="mt-8">
          <div className="text-[13px] font-medium">Lanes, left to right</div>
          <p className="mt-1 max-w-[62ch] text-[12px] text-muted">
            The lane count and the labels are yours, not the system&rsquo;s — they are recorded as{" "}
            <em>chosen by the operator</em>. A lane grid fitted to the signal slides toward the loaded
            lanes when one is empty, so this is never inferred.
          </p>
          <div className="mt-2 flex flex-wrap items-end gap-4">
            <label className="text-[13px]">
              <span className="mr-2 text-muted">count</span>
              <select
                value={nLanes}
                onChange={(e) => setLaneCount(Number(e.target.value))}
                className="border border-line bg-paper px-2 py-1 text-[13px]"
              >
                <option value="" disabled>
                  choose…
                </option>
                {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            {labels.map((v, i) => (
              <label key={i} className="text-[12px]">
                <span className="mr-1 text-muted">L{i + 1}</span>
                <select
                  value={ROLES.includes(v as (typeof ROLES)[number]) ? v : "other"}
                  onChange={(e) => {
                    const next = [...labels];
                    next[i] = e.target.value;
                    setLabels(next);
                  }}
                  className="border border-line bg-paper px-1 py-1 text-[12px]"
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          {labels.length ? (
            <p className="mt-2 text-[12px] text-muted">
              S = starting material · co = the co-spot (starting material with the reaction mixture) · R =
              the reaction · sd = an authentic sample of the product
            </p>
          ) : null}
        </div>

        <button
          disabled={!file || !nLanes || busy}
          onClick={() => void submit()}
          className="mt-8 border border-struct bg-struct px-4 py-2 text-[13px] text-white disabled:cursor-not-allowed disabled:border-line disabled:bg-line2 disabled:text-muted"
        >
          {busy ? "Reading the plate…" : "Read this plate"}
        </button>
        {busy ? (
          <p className="mt-2 text-[12px] text-muted">
            Twenty-four independent processing variants run on every lane. This takes a few seconds.
          </p>
        ) : null}
        {error ? <p className="mt-2 text-[13px] text-fault">{error}</p> : null}
      </div>

      <aside>
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted">
          Before you upload
        </h2>
        <canvas ref={canvasRef} className={qc ? "mt-2 w-full border border-line" : "hidden"} />
        {qc ? (
          <dl className="mt-3 space-y-2 text-[13px]">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Green clipping</dt>
              <dd className={`num ${clipBad ? "font-semibold text-warn" : ""}`}>{qc.clipPct.toFixed(1)}%</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Size</dt>
              <dd className="num">
                {qc.width} × {qc.height}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-muted">SHA-256</dt>
              <dd className="font-mono text-[11px]">{qc.sha8}</dd>
            </div>
          </dl>
        ) : (
          <p className="mt-2 text-[13px] text-muted">
            Choose a photograph and a magenta mask will show anything the sensor has clipped, before you
            leave the bench.
          </p>
        )}
        {clipBad ? (
          <div className="mt-3 border border-line border-l-[3px] border-l-struct bg-paper2 p-3 text-[12px]">
            <div className="font-semibold">This looks over-exposed</div>
            <p className="mt-1">
              Above roughly 15% clipping the background is at the sensor&rsquo;s maximum, and a band&rsquo;s
              depth cannot be measured against it. Positions will still be reported; areas will not.
            </p>
            <p className="mt-1 text-muted">Re-shoot one to two stops darker, keeping the framing.</p>
          </div>
        ) : null}
        <p className="mt-4 text-[11px] text-muted">
          This check runs in your browser on the decoded image and is advisory. The server measures the
          same quantity inside the plate outline and that is the one that decides.
        </p>
      </aside>
    </div>
  );
}
