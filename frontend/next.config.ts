import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  allowedDevOrigins: [
    "192.168.1.15",
    "sukkot.316costello",
    "sukkot.tail957ef.ts.net",
    "cchost.i.oursilverfamily.com",
    "*.i.oursilverfamily.com",
  ],
};

export default nextConfig;
