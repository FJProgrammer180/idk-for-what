/**
 * Next.js configuration.
 *
 * The browser only ever talks to this origin: `/api/v1/*` is proxied to the FastAPI
 * backend by the dev/production server, so no client-side code needs to know a backend
 * host (and the session cookie stays first-party, which is what makes the CSRF
 * double-submit cookie work).
 */
const BACKEND_ORIGIN = process.env.PLAB_BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // `next dev` otherwise answers its own host check with "unauthorized"; the sandbox
  // (and any tunnel) reaches the server through a host name we cannot know at build time.
  allowedDevOrigins: (process.env.PLAB_ALLOWED_DEV_ORIGINS ?? "")
    .split(",")
    .map((entry) => entry.trim())
    .filter(Boolean),
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${BACKEND_ORIGIN}/api/v1/:path*`,
      },
      // the OpenAPI document is served by the backend; linking to it from the UI must
      // not require a second origin in the browser
      { source: "/openapi.json", destination: `${BACKEND_ORIGIN}/openapi.json` },
      { source: "/health", destination: `${BACKEND_ORIGIN}/api/v1/health` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          // The markdown renderer sanitises at render time (src/lib/markdown.ts) and the
          // backend's security posture lists the absent CSP as a known gap; these are the
          // cheap, non-breaking pieces of that hardening.
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
