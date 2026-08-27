import type { NextConfig } from "next";

/** The FastAPI service. Everything the browser needs is proxied through this origin, so there is no
 *  CORS surface and the plate images come from the same place as the JSON. */
const BACKEND = process.env.TLC_BACKEND ?? "http://127.0.0.1:8811";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/backend/:path*", destination: `${BACKEND}/:path*` }];
  },
};

export default nextConfig;
