import type { NextConfig } from "next";

const internalApiOrigin = (process.env.INTERNAL_API_ORIGIN ?? "http://host.docker.internal:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // The floating "N" dev badge confuses the (non-developer) end user; dev-only
  // affordances live behind the in-app debug panel instead.
  devIndicators: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${internalApiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
