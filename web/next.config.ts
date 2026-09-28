import type { NextConfig } from "next";

// Where the Next.js server forwards /api/* requests. Inside docker compose this is the
// api service; for `npm run dev` on the host it is the API's published port.
const apiInternalUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  // Proxy /api/* to FastAPI so the browser only ever talks to one origin. That keeps the
  // session cookie first-party (SameSite=Lax works, no CORS). See docs/decisions.md.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiInternalUrl}/api/:path*` }];
  },
};

export default nextConfig;
