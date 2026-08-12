/**
 * Environment variables configuration
 */
export const config = {
  // API Configuration
  api: {
    baseUrl: process.env.NEXT_PUBLIC_API_URL || "https://localhost:8000",
    timeout: 30000,
  },

  // Auth Configuration
  auth: {
    tokenKey: "wildframe_access_token",
    refreshTokenKey: "wildframe_refresh_token",
    tokenExpiryKey: "wildframe_token_expiry",
    storageType: "localStorage" as const, // or 'sessionStorage'
  },

  // Video Configuration
  video: {
    defaultQuality: "auto" as const,
    autoplay: false,
    muted: false,
    preload: "metadata" as const,
    hlsConfig: {
      debug: false,
      enableWorker: true,
      lowLatencyMode: false,
    },
    dashConfig: {
      debug: false,
      lowLatencyMode: false,
    },
  },

  // UI Configuration
  ui: {
    theme: "dark" as const, // 'light' | 'dark'
    animationsEnabled: true,
    reducedMotion: false,
  },

  // Search Configuration
  search: {
    debounceMs: 300,
    minChars: 2,
    maxResults: 50,
  },

  // Pagination Configuration
  pagination: {
    defaultPageSize: 20,
    maxPageSize: 100,
  },

  // Content Configuration
  content: {
    imageBaseUrl: process.env.NEXT_PUBLIC_IMAGE_URL || "https://images.wildframe.com",
    thumbnailSizes: [250, 500, 750],
    posterSizes: [300, 500, 780],
    backdropSizes: [400, 800, 1280],
  },

  // Feature Flags
  features: {
    searchEnabled: true,
    recommendationsEnabled: true,
    watchlistEnabled: true,
    offlineDownloads: false,
    socialSharing: false,
    reviews: false,
  },

  // Tracking Configuration
  tracking: {
    googleAnalyticsId: process.env.NEXT_PUBLIC_GA_ID,
    enableTracking: process.env.NODE_ENV === "production",
  },

  // Payment Configuration
  payment: {
    stripePublishableKey: process.env.NEXT_PUBLIC_STRIPE_KEY,
  },

  // Retry Configuration
  retry: {
    maxAttempts: 3,
    delayMs: 1000,
    backoffMultiplier: 2,
  },

  // Cache Configuration
  cache: {
    // TanStack Query
    queryStaleTime: 5 * 60 * 1000, // 5 minutes
    queryGcTime: 10 * 60 * 1000, // 10 minutes
    // Browser
    localStoragePrefix: "wildframe_",
    sessionStoragePrefix: "wildframe_session_",
  },

  // Social Links
  social: {
    twitter: "https://twitter.com/wildframe",
    github: "https://github.com/wildframe",
    instagram: "https://instagram.com/wildframe",
  },

  // Support Links
  support: {
    helpCenter: "https://help.wildframe.com",
    contactEmail: "support@wildframe.com",
    privacy: "https://wildframe.com/privacy",
    terms: "https://wildframe.com/terms",
  },
};

// Type exports for config keys
export type Config = typeof config;
export type ConfigKey = keyof Config;
