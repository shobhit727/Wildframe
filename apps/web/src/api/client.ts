// API client for backend services. Talks to the api-gateway, which proxies
// /<service>/<path> to the upstream services (paths mirror each service's
// mounted /api/v1 (or /billing, /search) prefixes).
import axios, { AxiosInstance } from 'axios';
import type {
  AuthTokens,
  BackendContent,
  BackendEpisode,
  BackendGenre,
  BackendSeason,
  Content,
  PlaybackSession,
  Subscription,
  User,
  UserDevice,
  UserPreferences,
  UserProfile,
  VideoManifest,
} from '@/types';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// A public HLS test stream used when the platform has no packaged media for
// the title yet (media-pipeline currently emits stub manifests only). The
// real manifest is preferred whenever it exists.
export const DEMO_HLS_URL = 'https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8';

const ACCESS_KEY = 'accessToken';
const REFRESH_KEY = 'refreshToken';
const USER_KEY = 'user';

export function getAccessToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(tokens: AuthTokens): void {
  localStorage.setItem(ACCESS_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
}

// ---- Normalization: backend DTOs -> UI types ----

export function normalizeContent(item: BackendContent): Content {
  const type = item.content_type === 'series' || item.content_type === 'show' ? 'show' : 'movie';
  return {
    id: item.id,
    title: item.title,
    description: item.description,
    genre: item.genres?.[0]?.name || 'Other',
    genres: item.genres?.map((g) => g.name) || [],
    poster: item.poster_url || '',
    backdrop: item.backdrop_url || item.poster_url || '',
    duration: item.duration_minutes || 0,
    releaseDate: item.release_date || '',
    rating: item.audience_score || item.imdb_rating || 0,
    type: type as Content['type'],
    content_type: item.content_type,
    maturityRating: item.content_rating || undefined,
    isPremium: item.is_premium,
    isHd: item.is_hd,
    trailerUrl: item.trailer_url || undefined,
    cast: item.cast_members?.map((c) => c.name) || [],
    seasonsCount: item.seasons?.length || 0,
    episodesCount: item.seasons?.reduce((n, s) => n + (s.episode_count || 0), 0) || 0,
  };
}

function normalizeUser(payload: Record<string, unknown>): User {
  return {
    id: String(payload.id),
    email: String(payload.email || ''),
    firstName: (payload.first_name as string) || '',
    lastName: (payload.last_name as string) || '',
    emailVerified: Boolean(payload.email_verified),
    role: (payload.role as User['role']) || 'user',
  };
}

let refreshPromise: Promise<string | null> | null = null;

class APIClient {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      timeout: 15000,
      headers: { 'Content-Type': 'application/json' },
    });

    this.client.interceptors.request.use((config) => {
      const token = getAccessToken();
      if (token) config.headers.Authorization = `Bearer ${token}`;
      return config;
    });

    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        const original = error.config;
        if (error.response?.status !== 401 || !original || original._retried) {
          return Promise.reject(error);
        }
        original._retried = true;
        const token = await this.refreshAccessToken();
        if (token) {
          original.headers.Authorization = `Bearer ${token}`;
          return this.client(original);
        }
        this.clearAuth();
        return Promise.reject(error);
      }
    );
  }

  async refreshAccessToken(): Promise<string | null> {
    if (!refreshPromise) {
      refreshPromise = (async () => {
        const refreshToken = getRefreshToken();
        if (!refreshToken) return null;
        try {
          const { data } = await axios.post(`${API_BASE_URL}/auth/api/v1/auth/refresh`, {
            refresh_token: refreshToken,
          });
          const tokens = data as AuthTokens;
          setTokens(tokens);
          return tokens.access_token;
        } catch {
          return null;
        } finally {
          refreshPromise = null;
        }
      })();
    }
    return refreshPromise;
  }

  clearAuth() {
    clearTokens();
    if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  }

  private async unwrap<T>(promise: Promise<{ data: T }>): Promise<T> {
    return (await promise).data;
  }

  // ---- Auth ----

  async register(email: string, password: string, firstName: string, lastName: string) {
    const { data } = await this.client.post('/auth/api/v1/auth/register', {
      email,
      password,
      first_name: firstName,
      last_name: lastName,
    });
    setTokens(data as AuthTokens);
    return data as AuthTokens;
  }

  async login(email: string, password: string) {
    const { data } = await this.client.post('/auth/api/v1/auth/login', { email, password });
    setTokens(data as AuthTokens);
    return data as AuthTokens;
  }

  async logout() {
    try {
      await this.client.post('/auth/api/v1/auth/logout');
    } finally {
      clearTokens();
    }
  }

  async getMe(): Promise<User> {
    const data = await this.unwrap<Record<string, unknown>>(
      this.client.get('/auth/api/v1/auth/me')
    );
    return normalizeUser(data);
  }

  async getProfile(userId: string): Promise<UserProfile> {
    try {
      return await this.unwrap(this.client.get(`/users/api/v1/profiles/${userId}`));
    } catch (err) {
      // Profile not provisioned yet — create it for the JWT user, then retry.
      const is404 = (err as { response?: { status?: number } })?.response?.status === 404;
      if (is404) {
        const created = await this.unwrap<UserProfile>(
          this.client.post('/users/api/v1/profiles')
        );
        if (created?.id) return created;
      }
      throw err;
    }
  }

  async updateProfile(userId: string, data: Record<string, unknown>): Promise<UserProfile> {
    return this.unwrap(this.client.patch(`/users/api/v1/profiles/${userId}`, data));
  }

  async getDevices(userId: string): Promise<UserDevice[]> {
    return this.unwrap(this.client.get(`/users/api/v1/devices/${userId}`));
  }

  async registerDevice(
    _userId: string,
    device: { device_id: string; device_name: string; device_type: string }
  ) {
    return this.unwrap(this.client.post('/users/api/v1/devices', device));
  }

  async getPreferences(userId: string): Promise<UserPreferences> {
    return this.unwrap(this.client.get(`/users/api/v1/preferences/${userId}`));
  }

  async updatePreferences(userId: string, data: Partial<UserPreferences>): Promise<UserPreferences> {
    return this.unwrap(this.client.patch(`/users/api/v1/preferences/${userId}`, data));
  }

  // ---- Content ----

  async getContentList(params: {
    page?: number;
    page_size?: number;
    content_type?: string;
    status?: string;
    genre_id?: string;
  } = {}): Promise<BackendContent[]> {
    return this.unwrap(
      this.client.get('/content/api/v1/content', { params: { page: 1, page_size: 50, ...params } })
    );
  }

  async getGenres(): Promise<BackendGenre[]> {
    return this.unwrap(this.client.get('/content/api/v1/genres'));
  }

  async getContentById(id: string): Promise<BackendContent> {
    return this.unwrap(this.client.get(`/content/api/v1/content/${id}`));
  }

  async getSeasons(contentId: string): Promise<BackendSeason[]> {
    return this.unwrap(this.client.get(`/content/api/v1/content/${contentId}/seasons`));
  }

  async getEpisodes(contentId: string, seasonId: string): Promise<BackendEpisode[]> {
    return this.unwrap(
      this.client.get(`/content/api/v1/content/${contentId}/seasons/${seasonId}/episodes`)
    );
  }

  async searchContent(query: string): Promise<BackendContent[]> {
    try {
      const results = await this.unwrap<{ query: string; results: Record<string, unknown>[] }>(
        this.client.get('/search/search/query', { params: { q: query, limit: 30 } })
      );
      // search-service returns ES _source docs; map loosely to our shape
      return results.results
        .filter((r) => r.title)
        .map(
          (r): BackendContent => ({
            id: String(r.id || r.content_id || ''),
            title: String(r.title),
            slug: String(r.slug || r.title || '').toLowerCase().replace(/\s+/g, '-'),
            description: String(r.description || ''),
            content_type: String(r.content_type || 'movie'),
            status: 'published',
            poster_url: r.poster ? String(r.poster) : null,
            backdrop_url: r.backdrop ? String(r.backdrop) : null,
            audience_score: Number(r.audience_score || r.rating || 0),
            genres: [],
          })
        );
    } catch {
      // Fallback: filter the content catalog client-side.
      const all = await this.getContentList({ page_size: 100 });
      const q = query.toLowerCase();
      return all.filter(
        (c) => c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)
      );
    }
  }

  async getTrending(): Promise<BackendContent[]> {
    try {
      const data = await this.unwrap<{ trending: Record<string, unknown>[]; total: number }>(
        this.client.get('/search/search/trending', { params: { limit: 20 } })
      );
      if (data.trending?.length) return data.trending as unknown as BackendContent[];
    } catch {
      // ignore - fall back to score-sorted catalog below
    }
    const all = await this.getContentList({ page_size: 100 });
    return [...all].sort((a, b) => (b.audience_score || 0) - (a.audience_score || 0)).slice(0, 20);
  }

  async getRecommendations(userId: string, limit = 20): Promise<BackendContent[]> {
    try {
      const data = await this.unwrap<{
        recommendations: { content_id: string; score: number; reason?: string }[];
        total: number;
      }>(this.client.get(`/recommendations/recommendations/for-user/${userId}`, {
        params: { limit },
      }));
      if (data.recommendations?.length) {
        const items = await Promise.all(
          data.recommendations.slice(0, limit).map(async (rec) => {
            try {
              return await this.getContentById(rec.content_id);
            } catch {
              return null;
            }
          })
        );
        const content = items.filter(Boolean) as BackendContent[];
        if (content.length) return content;
      }
    } catch {
      // ignore - fall back to top-rated below
    }
    const all = await this.getContentList({ page_size: 100 });
    return [...all].sort((a, b) => (b.audience_score || 0) - (a.audience_score || 0)).slice(0, limit);
  }

  // ---- Streaming ----

  async startPlaybackSession(params: {
    user_id: string;
    content_id: string;
    episode_id?: string;
    device_id: string;
  }): Promise<PlaybackSession> {
    return this.unwrap(
      this.client.post('/streaming/api/v1/playback-sessions', {
        user_id: params.user_id,
        content_id: params.content_id,
        episode_id: params.episode_id,
        device_id: params.device_id,
      })
    );
  }

  async getPlaybackSession(sessionId: string): Promise<PlaybackSession> {
    return this.unwrap(this.client.get(`/streaming/api/v1/playback-sessions/${sessionId}`));
  }

  async updatePlaybackPosition(sessionId: string, positionSeconds: number): Promise<PlaybackSession> {
    return this.unwrap(
      this.client.patch(`/streaming/api/v1/playback-sessions/${sessionId}`, {
        current_position_seconds: Math.round(positionSeconds),
      })
    );
  }

  async endPlaybackSession(sessionId: string): Promise<void> {
    await this.client.post(`/streaming/api/v1/playback-sessions/${sessionId}/end`);
  }

  async getManifestForEpisode(episodeId: string): Promise<VideoManifest | null> {
    try {
      return await this.unwrap(
        this.client.get(`/streaming/api/v1/episodes/${episodeId}/manifest`, {
          params: { protocol: 'hls' },
        })
      );
    } catch {
      return null;
    }
  }

  async getWatchHistory(userId: string): Promise<
    { session: PlaybackSession; content: BackendContent | null }[]
  > {
    const sessions = await this.unwrap<PlaybackSession[]>(
      this.client.get(`/streaming/api/v1/users/${userId}/playback-sessions`)
    );
    const items: { session: PlaybackSession; content: BackendContent | null }[] = [];
    for (const session of sessions) {
      if (session.status === 'ended') continue;
      try {
        const content = await this.getContentById(session.content_id);
        items.push({ session, content });
      } catch {
        // orphaned session - skip
      }
    }
    return items;
  }

  // ---- Billing ----

  async getSubscription(userId: string): Promise<Subscription> {
    return this.unwrap(this.client.get(`/billing/billing/subscription/${userId}`));
  }

  async subscribe(userId: string, tier: 'avod' | 'svod' | 'tvod') {
    return this.unwrap(this.client.post(`/billing/billing/subscribe/${userId}`, { tier }));
  }

  async cancelSubscription(userId: string) {
    return this.unwrap(this.client.post(`/billing/billing/cancel/${userId}`));
  }

  // ---- Analytics ----

  async logEvent(userId: string, eventType: string, eventData?: Record<string, unknown>) {
    try {
      await this.client.post('/analytics/analytics/events', {
        user_id: userId,
        event_type: eventType,
        event_data: eventData || {},
      });
    } catch {
      // analytics is best-effort
    }
  }
}

export const apiClient = new APIClient();
