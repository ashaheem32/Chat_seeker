/** @type {import('next').NextConfig} */
const nextConfig = {
  // Standalone output keeps the production Docker image small (only what's needed at runtime).
  output: "standalone",
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
    // Optimize package imports for tree-shaking on heavy libs.
    optimizePackageImports: ["lucide-react", "recharts", "date-fns"],
  },
};

export default nextConfig;
