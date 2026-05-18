/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output keeps the production Docker image small (only what's
  // needed at runtime). Toggle via OUTPUT_MODE=standalone — disabled by
  // default because Next 14.2 + standalone has a known bug that fails
  // static export of /404/500 with a Pages-Router "<Html>" error.
  output: process.env.OUTPUT_MODE === "standalone" ? "standalone" : undefined,
  reactStrictMode: true,
  // Server-side rewrites: Next API routes can act as a proxy to the FastAPI backend.
  // Configure via BACKEND_URL env var (set to http://backend:8000 inside docker).
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [
      {
        source: "/api/backend/:path*",
        destination: `${backend}/:path*`,
      },
    ];
  },
  experimental: {
    // optimizePackageImports trips a known Next 14.2 + React 18.3 bug:
    // static-export of /_error/_not-found errors with "<Html> should not
    // be imported outside of pages/_document" referencing the *development*
    // react-dom-server. Disabled until we move to Next 14.2.25+.
    // optimizePackageImports: ["lucide-react", "recharts", "date-fns"],
  },
};

export default nextConfig;
