import type { NextConfig } from "next";

// Requests are proxied so the browser only ever talks to this origin. That removes CORS
// from local iPad testing and keeps the API's address and key out of client code.
const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Browser tests build elsewhere, so they can run alongside a dev server on the same checkout.
  ...(process.env.QUACK_DIST_DIR ? { distDir: process.env.QUACK_DIST_DIR } : {}),
  poweredByHeader: false,
  allowedDevOrigins: ["*.local", "192.168.*.*", "10.*.*.*", "172.*.*.*"],
  experimental: {
    // An alert can need a fast reading and a slower confirmation, with retries.
    proxyTimeout: 120_000,
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
        ],
      },
    ];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
