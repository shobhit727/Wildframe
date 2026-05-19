// API Response Types
export interface ApiResponse<T> {
  data: T;
  message?: string;
  timestamp: string;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, string[]>;
  timestamp: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

// Auth Types
export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  confirmPassword: string;
  firstName: string;
  lastName: string;
}

export interface TokenResponse {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  tokenType: "Bearer";
}

export interface AuthContext {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  error: string | null;
}

// User Types
export interface User {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  profilePicture?: string;
  createdAt: string;
  updatedAt: string;
}

export interface UserProfile extends User {
  bio?: string;
  birthDate?: string;
  country?: string;
  preferences: UserPreferences;
}

export interface UserPreferences {
  language: string;
  subtitles: boolean;
  subtitleLanguage: string;
  videoQuality: "auto" | "480p" | "720p" | "1080p" | "4k";
  autoPlay: boolean;
}

// Content Types
export interface Genre {
  id: string;
  name: string;
  description?: string;
  slug: string;
}

export interface Content {
  id: string;
  title: string;
  description: string;
  thumbnail: string;
  posterImage?: string;
  backdropImage?: string;
  releaseDate: string;
  rating: number;
  contentType: "movie" | "show";
  duration?: number; // For movies, in minutes
  genres: Genre[];
  cast: CastMember[];
  createdAt: string;
  updatedAt: string;
}

export interface MovieContent extends Content {
  contentType: "movie";
  duration: number;
}

export interface ShowContent extends Content {
  contentType: "show";
  totalEpisodes: number;
  seasons: Season[];
}

export interface Season {
  id: string;
  showId: string;
  seasonNumber: number;
  title: string;
  description?: string;
  releaseDate: string;
  episodes: Episode[];
}

export interface Episode {
  id: string;
  seasonId: string;
  episodeNumber: number;
  title: string;
  description?: string;
  thumbnail?: string;
  duration: number;
  releaseDate: string;
  videoId: string;
}

export interface CastMember {
  id: string;
  name: string;
  character: string;
  profileImage?: string;
}

// Watchlist Types
export interface WatchlistItem {
  id: string;
  userId: string;
  contentId: string;
  content: Content;
  addedAt: string;
}

// Playback Types
export interface PlaybackSession {
  id: string;
  userId: string;
  contentId: string;
  videoId: string;
  startedAt: string;
  currentPosition: number;
  duration: number;
  quality: string;
  deviceId: string;
}

export interface PlaybackEvent {
  id: string;
  sessionId: string;
  eventType: "play" | "pause" | "seek" | "ended" | "quality_changed";
  timestamp: string;
  metadata: Record<string, any>;
}

export interface WatchProgress {
  contentId: string;
  lastWatchedAt: string;
  watchedPercentage: number;
  lastPosition: number;
  duration: number;
}

// Search Types
export interface SearchResult {
  content: Content[];
  suggestions: string[];
  total: number;
}

// Recommendation Types
export interface Recommendation {
  id: string;
  reason: string;
  content: Content;
  confidence: number;
}

// Device Types
export interface Device {
  id: string;
  userId: string;
  name: string;
  deviceType: "web" | "mobile" | "tablet" | "tv";
  userAgent: string;
  lastUsedAt: string;
  createdAt: string;
}

// Subscription Types
export interface SubscriptionPlan {
  id: string;
  name: string;
  price: number;
  currency: string;
  features: string[];
  maxResolution: string;
  maxStreams: number;
  offline: boolean;
}

export interface UserSubscription {
  id: string;
  userId: string;
  planId: string;
  plan: SubscriptionPlan;
  status: "active" | "cancelled" | "expired";
  startDate: string;
  endDate: string;
  autoRenew: boolean;
}

// UI State Types
export interface Modal {
  isOpen: boolean;
  type?: string;
  data?: Record<string, any>;
}

export interface Toast {
  id: string;
  type: "success" | "error" | "warning" | "info";
  message: string;
  duration?: number;
}

// Video Player Types
export interface QualityOption {
  label: string;
  value: string;
  bitrate: number;
}

export interface SubtitleTrack {
  kind: "subtitles" | "captions";
  src: string;
  srclang: string;
  label: string;
}

export interface VideoManifest {
  url: string;
  type: "hls" | "dash";
  subtitles: SubtitleTrack[];
}
