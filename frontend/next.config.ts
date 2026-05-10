import type { NextConfig } from "next";
import path from "node:path";

const nextConfig: NextConfig = {
  devIndicators: false,
  allowedDevOrigins: [
    "192.168.1.15",
    "sukkot.316costello",
    "sukkot.tail957ef.ts.net",
    "cchost.i.oursilverfamily.com",
    "*.i.oursilverfamily.com",
  ],
  // Next 16 added stricter workspace-root inference for turbopack. The
  // backend/frontend split repo with no top-level package.json at /cc-web/
  // confuses it ("couldn't find next/package.json from src/app"). Pin the
  // root here so dev/build don't bail on cold start.
  turbopack: {
    root: path.resolve(__dirname),
  },
};

export default nextConfig;
