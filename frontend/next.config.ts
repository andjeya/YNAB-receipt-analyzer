import type { NextConfig } from "next";

const internalApiOrigin = (process.env.INTERNAL_API_ORIGIN ?? "http://host.docker.internal:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
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
