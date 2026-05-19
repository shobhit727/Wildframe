/**
 * Application constants
 */

// HTTP Status Codes
export const HTTP_STATUS = {
  OK: 200,
  CREATED: 201,
  BAD_REQUEST: 400,
  UNAUTHORIZED: 401,
  FORBIDDEN: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  UNPROCESSABLE_ENTITY: 422,
  RATE_LIMITED: 429,
  SERVER_ERROR: 500,
  SERVICE_UNAVAILABLE: 503,
} as const;

// Error Messages
export const ERROR_MESSAGES = {
  NETWORK_ERROR: "Network error. Please try again.",
  UNAUTHORIZED: "Please log in to continue.",
  FORBIDDEN: "You don't have permission to access this.",
  NOT_FOUND: "The requested resource was not found.",
  SERVER_ERROR: "Something went wrong. Please try again.",
  VALIDATION_ERROR: "Please check your input and try again.",
  TIMEOUT: "Request timed out. Please try again.",
  RATE_LIMITED: "Too many requests. Please try again later.",
  UNKNOWN: "An unexpected error occurred.",
} as const;

// Success Messages
export const SUCCESS_MESSAGES = {
  LOGIN_SUCCESS: "Logged in successfully.",
  LOGOUT_SUCCESS: "Logged out successfully.",
  REGISTER_SUCCESS: "Account created successfully.",
  PASSWORD_CHANGED: "Password changed successfully.",
  EMAIL_VERIFIED: "Email verified successfully.",
  PROFILE_UPDATED: "Profile updated successfully.",
  ITEM_ADDED: "Item added successfully.",
  ITEM_REMOVED: "Item removed successfully.",
} as const;

// API Routes
export const API_ROUTES = {
  AUTH: {
    LOGIN: "/auth/login",
    LOGOUT: "/auth/logout",
    REGISTER: "/auth/register",
    REFRESH: "/auth/refresh",
    VERIFY_EMAIL: "/auth/verify-email",
    FORGOT_PASSWORD: "/auth/forgot-password",
    RESET_PASSWORD: "/auth/reset-password",
  },
  USERS: {
    PROFILE: "/users/me",
    UPDATE_PROFILE: "/users/me",
    CHANGE_PASSWORD: "/users/me/password",
    PREFERENCES: "/users/me/preferences",
  },
  CONTENT: {
    LIST: "/content",
    DETAIL: (id: string) => `/content/${id}`,
    GENRES: "/genres",
    SEARCH: "/content/search",
    TRENDING: "/content/trending",
    NEW_RELEASES: "/content/new-releases",
    TOP_RATED: "/content/top-rated",
  },
  WATCHLIST: {
    LIST: "/watchlist",
    ADD: "/watchlist",
    REMOVE: (id: string) => `/watchlist/${id}`,
  },
  PLAYBACK: {
    SESSIONS: "/playback/sessions",
    CREATE_SESSION: "/playback/sessions",
    GET_SESSION: (id: string) => `/playback/sessions/${id}`,
    MANIFEST: (id: string) => `/playback/sessions/${id}/manifest`,
    EVENTS: (id: string) => `/playback/sessions/${id}/events`,
    PROGRESS: (id: string) => `/playback/progress/${id}`,
  },
  RECOMMENDATIONS: {
    PERSONALIZED: "/recommendations",
    SIMILAR: (id: string) => `/recommendations/similar/${id}`,
  },
  DEVICES: {
    LIST: "/devices",
    ADD: "/devices",
    REMOVE: (id: string) => `/devices/${id}`,
  },
  SUBSCRIPTIONS: {
    PLANS: "/subscriptions/plans",
    CURRENT: "/subscriptions/current",
    HISTORY: "/subscriptions/history",
  },
} as const;

// Content Types
export const CONTENT_TYPES = {
  MOVIE: "movie",
  SHOW: "show",
} as const;

// Video Qualities
export const VIDEO_QUALITIES = {
  AUTO: "auto",
  P480: "480p",
  P720: "720p",
  P1080: "1080p",
  P2160: "2160p", // 4K
} as const;

// Video Quality Bitrates (in kbps)
export const QUALITY_BITRATES = {
  auto: 0,
  "480p": 1200,
  "720p": 2500,
  "1080p": 5000,
  "2160p": 15000,
} as const;

// Device Types
export const DEVICE_TYPES = {
  WEB: "web",
  MOBILE: "mobile",
  TABLET: "tablet",
  TV: "tv",
} as const;

// Subscription Status
export const SUBSCRIPTION_STATUS = {
  ACTIVE: "active",
  CANCELLED: "cancelled",
  EXPIRED: "expired",
} as const;

// Toast Duration (ms)
export const TOAST_DURATION = {
  SHORT: 3000,
  MEDIUM: 5000,
  LONG: 8000,
} as const;

// Keyboard Shortcuts
export const KEYBOARD_SHORTCUTS = {
  PLAY_PAUSE: "Space",
  MUTE: "m",
  VOLUME_UP: "ArrowUp",
  VOLUME_DOWN: "ArrowDown",
  FULLSCREEN: "f",
  SEEK_FORWARD: "ArrowRight",
  SEEK_BACKWARD: "ArrowLeft",
  SKIP_INTRO: "i",
  SKIP_OUTRO: "e",
  NEXT_EPISODE: "n",
  SUBTITLES: "c",
  SETTINGS: ".",
  THEATRE_MODE: "t",
} as const;

// Animation Durations (ms)
export const ANIMATION_DURATION = {
  FAST: 150,
  MEDIUM: 300,
  SLOW: 500,
} as const;

// Pagination Sizes
export const PAGINATION_SIZES = {
  SMALL: 10,
  MEDIUM: 20,
  LARGE: 50,
} as const;

// Cache Keys
export const CACHE_KEYS = {
  USER: "user",
  WATCHLIST: "watchlist",
  CONTENT_LIST: "content:list",
  CONTENT_DETAIL: (id: string) => `content:${id}`,
  RECOMMENDATIONS: "recommendations",
  GENRES: "genres",
  SEARCH: (query: string) => `search:${query}`,
  CONTINUE_WATCHING: "continue-watching",
} as const;

// Regular Expressions
export const REGEX = {
  EMAIL: /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
  PASSWORD: /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$/,
  URL: /^https?:\/\/.+/,
  SLUG: /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
} as const;

// Date Formats
export const DATE_FORMATS = {
  SHORT: "MMM d, yyyy",
  LONG: "MMMM d, yyyy",
  FULL: "EEEE, MMMM d, yyyy",
  TIME: "h:mm a",
  DATE_TIME: "MMM d, yyyy h:mm a",
} as const;

// Localization
export const SUPPORTED_LANGUAGES = {
  EN: "en",
  ES: "es",
  FR: "fr",
  DE: "de",
  IT: "it",
  PT: "pt",
  RU: "ru",
  JA: "ja",
  KO: "ko",
  ZH: "zh",
} as const;

// Breakpoints (matching Tailwind)
export const BREAKPOINTS = {
  XS: 0,
  SM: 640,
  MD: 768,
  LG: 1024,
  XL: 1280,
  "2XL": 1536,
} as const;

// Z-Index Scale
export const Z_INDEX = {
  DROPDOWN: 1000,
  STICKY: 1020,
  FIXED: 1030,
  MODAL_BACKDROP: 1040,
  MODAL: 1050,
  POPOVER: 1060,
  TOOLTIP: 1070,
} as const;
