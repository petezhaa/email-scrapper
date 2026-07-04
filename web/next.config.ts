import type { NextConfig } from "next";

// The Python pipeline (scrape / draft / Gmail send) runs as a local Flask API.
// Proxy everything under /py/* to it so the browser only ever talks to the
// Next origin — no CORS, no second hostname to configure.
const PY_API = process.env.PY_API_BASE ?? "http://127.0.0.1:5000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/py/:path*", destination: `${PY_API}/api/:path*` }];
  },
};

export default nextConfig;
