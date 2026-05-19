import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  swcMinify: true,
  poweredByHeader: false,
  
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "images.wildframe.com",
      },
      {
        protocol: "https",
        hostname: "*.cloudfront.net",
      },
    ],
    formats: ["image/avif", "image/webp"],
    deviceSizes: [640, 750, 828, 1080, 1200, 1920, 2048, 3840],
    imageSizes: [16, 32, 48, 64, 96, 128, 256, 384],
  },

  headers: async () => [
    {
      source: "/:path*",
      headers: [
        {
          key: "X-Frame-Options",
          value: "DENY",
        },
        {
          key: "X-Content-Type-Options",
          value: "nosniff",
        },
        {
          key: "X-XSS-Protection",
          value: "1; mode=block",
        },
        {
          key: "Referrer-Policy",
          value: "strict-origin-when-cross-origin",
        },
        {
          key: "Permissions-Policy",
          value: "camera=(), microphone=(), geolocation=()",
        },
      ],
    },
  ],

  redirects: async () => [
    {
      source: "/home",
      destination: "/",
      permanent: true,
    },
  ],

  rewrites: async () => ({
    beforeFiles: [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL}/api/:path*`,
      },
    ],
  }),

  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.optimization.splitChunks.cacheGroups = {
        ...config.optimization.splitChunks.cacheGroups,
        vendor: {
          test: /[\\/]node_modules[\\/]/,
          name: "vendors",
          priority: 10,
          reuseExistingChunk: true,
        },
      };
    }
    return config;
  },

  experimental: {
    optimizePackageImports: [
      "@radix-ui/react-dialog",
      "@radix-ui/react-dropdown-menu",
      "@radix-ui/react-select",
    ],
  },
};

export default nextConfig;
