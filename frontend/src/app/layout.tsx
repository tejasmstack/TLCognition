import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "TLC plate readout",
  description: "Read a TLC plate: what the lanes say, what can be measured, and what cannot.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        <header className="border-b border-line">
          <div className="mx-auto flex max-w-[1400px] items-baseline gap-6 px-6 py-3">
            <Link href="/" className="text-[15px] font-semibold tracking-tight">
              TLC plate readout
            </Link>
            <Link href="/" className="text-[13px] text-muted hover:text-ink">New plate</Link>
            <Link href="/runs" className="text-[13px] text-muted hover:text-ink">Plates</Link>
            <span className="ml-auto text-[11px] text-muted">
              no accuracy claim is made until the calibration set exists · confidence is not calibrated
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-[1400px] px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
