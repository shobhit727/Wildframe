/** Frontend TypeScript types. */

export interface User {
  id: string;
  email: string;
  firstName: string;
  lastName: string;
  emailVerified?: boolean;
  avatar?: string;
  /** Admin gating. Absent on regular users. */
  role?: 'admin' | 'moderator' | 'user';
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type?: string;
  expires_in?: number;
}

export interface Content {
  id: string;
  title: string;
  description: string;
  genre: string;
  poster: string;
  backdrop: string;
  duration: number;
  releaseDate: string;
  rating: number;
  type: 'movie' | 'show';
  // Extra metadata surfaced by the detail page
  content_type?: string;
  maturityRating?: string;
  isPremium?: boolean;
  isHd?: boolean;
  trailerUrl?: string;
  seasonsCount?: number;
  episodesCount?: number;
  genres?: string[];
  cast?: string[];
}

export interface BackendGenre {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  icon_url?: string | null;
}

export interface BackendContent {
  id: string;
  title: string;
  slug: string;
  description: string;
  content_type: string;
  status: string;
  release_date?: string | null;
  duration_minutes?: number | null;
  original_language?: string;
  country?: string | null;
  poster_url?: string | null;
  backdrop_url?: string | null;
  trailer_url?: string | null;
  imdb_rating?: number | null;
  audience_score: number;
  total_votes?: number;
  content_rating?: string | null;
  is_premium?: boolean;
  is_hd?: boolean;
  can_download?: boolean;
  can_stream?: boolean;
  genres: BackendGenre[];
  cast_members?: { name: string }[];
  seasons?: BackendSeason[];
}

export interface BackendSeason {
  id: string;
  season_number: number;
  title?: string;
  description?: string | null;
  poster_url?: string | null;
  episode_count: number;
  episodes?: BackendEpisode[];
}

export interface BackendEpisode {
  id: string;
  episode_number: number;
  title: string;
  description?: string | null;
  duration_minutes: number;
  thumbnail_url?: string | null;
  release_date?: string | null;
  is_available?: boolean;
  audience_score?: number;
}

export interface PlaybackSession {
  id: string;
  user_id: string;
  content_id: string;
  episode_id?: string | null;
  device_id: string;
  status: string;
  current_position_seconds: number;
  total_duration_seconds: number;
  protocol: string;
  resolution: string;
  bitrate_kbps: number;
  started_at: string;
  last_activity_at: string;
  ended_at?: string | null;
}

export interface VideoManifest {
  id: string;
  episode_id: string;
  content_id: string;
  protocol: string;
  manifest_url: string;
  variants: string[];
  available_bitrates: number[];
  expires_at?: string | null;
}

export interface UserProfile {
  id: string;
  user_id: string;
  avatar_url?: string | null;
  bio?: string | null;
  phone_number?: string | null;
  country?: string | null;
  language?: string | null;
  timezone?: string | null;
  public_profile?: boolean;
  newsletter_subscribed?: boolean;
  marketing_emails?: boolean;
  completed_onboarding?: boolean;
  profile_completeness?: number;
  created_at?: string;
  updated_at?: string;
}

export interface UserDevice {
  id: string;
  device_id: string;
  device_name: string;
  device_type: string;
  os_name?: string;
  os_version?: string;
  browser_name?: string;
  browser_version?: string;
  ip_address?: string;
  is_active?: boolean;
  is_trusted?: boolean;
  can_stream?: boolean;
  can_download?: boolean;
  last_active_at?: string;
  registration_date?: string;
}

export interface UserPreferences {
  theme?: string;
  language?: string;
  subtitle_language?: string;
  subtitle_size?: string;
  closed_captions?: boolean;
  autoplay?: boolean;
  autoplay_next_episode?: boolean;
  default_video_quality?: string;
  default_audio_language?: string;
  content_rating?: string;
  allow_explicit_content?: boolean;
  share_viewing_activity?: boolean;
  allow_recommendations?: boolean;
  data_collection?: boolean;
  email_new_content?: boolean;
  email_recommendations?: boolean;
  push_notifications?: boolean;
}

export interface Subscription {
  id: string;
  tier: 'avod' | 'svod' | 'tvod';
  subscription_status: string;
  monthly_price?: string;
  max_concurrent_streams?: number;
  can_download?: boolean;
  can_use_4k?: boolean;
  ad_free?: boolean;
  current_period_start?: string;
  current_period_end?: string;
}

export interface StreamingSession {
  id: string;
  contentId: string;
  deviceId: string;
  status: 'active' | 'paused' | 'ended';
  currentPosition: number;
  startTime: string;
}

export interface Notification {
  id: string;
  title: string;
  message: string;
  read: boolean;
  timestamp: string;
}