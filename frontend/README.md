This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

---

# TLC frontend

The product surface for the single-plate flow. It renders what the FastAPI service measures and never
computes a scientific number of its own: every value arrives inside its envelope (value, unit,
provenance, interval or refusal) and is rendered with what qualifies it attached, so a number cropped
out of a screenshot cannot lose its interval or its refusal.

## Running it

```bash
# 1. the measurement service
cd .. && TLC_DATA_DIR=demo_data uv run uvicorn tlc.api.app:app --port 8811

# 2. this
npm run dev            # http://localhost:3000
```

`next.config.ts` proxies `/backend/*` to the service, so the browser is same-origin: no CORS, and the
plate images come from the same place as the JSON. Point it elsewhere with `TLC_BACKEND`.

Node is installed user-locally at `~/.local/node` (Homebrew on this machine is owned by another
account). `~/.zshrc` puts it on PATH.

## What is here

| Route | What it is |
|---|---|
| `/` | Upload. Client-side capture QC on the decoded bitmap: green-clipping fraction with a magenta mask, size, SHA-256. Lane count and roles are an explicit operator choice — never inferred, never defaulted. |
| `/runs/[id]` | The reading first (verdict, plain-language answer, per-band identity, conversion), then the plate with its overlay, the densitograms, the band table with agreement tallies, the refusals, and the provenance. |
| `/runs` | Every plate read so far. |

## Rules this UI follows

- **Colour never carries meaning alone.** Every state also has a word and a fill pattern. Red is only
  for system faults, never for a refusal — a refusal is the system working correctly.
- **No probability appears anywhere.** Detection evidence is the discrete tally (`16 of 24`) with the
  threshold marked. Confidence is an ordinal grade with its factors named.
- **A refusal gets the same card and the same weight as a result**, and ends on a physical action.
- **Numbers carry their intervals inline**, never in a separate column, and the formatter is the only
  path to a rendered number.
