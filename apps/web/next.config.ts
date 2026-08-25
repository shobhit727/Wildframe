type WebpackConfig = any;

const nextConfig = {
  // isn't installed in this environment. Type-checking (tsc) still runs in CI;
  // skip the lint gate during build so missing rule defs don't fail it.
  eslint: { ignoreDuringBuilds: true },

  // Allow local and LAN-IP access to dev assets (containerized browsers / device testing).
  // Dev-only setting; production builds are unaffected.
  allowedDevOrigins: ["localhost", "127.0.0.1", "::1", "192.168.1.14"],

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
        destination: `${process.env.NEXT_PUBLIC_API_URL || "https://localhost:8000"}/api/:path*`,
      },
    ],
  }),

  webpack: (config: WebpackConfig, { isServer }: { isServer: boolean }) => {
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

  turbopack: {},
};

export default nextConfig;