// API client for backend services. Talks to the api-gateway, which proxies
// /<service>/<path> to the upstream services (paths mirror each service's
// mounted /api/v1 (or /billing, /search) prefixes).
import axios, { AxiosInstance } from 'axios';
import type {
  AuthTokens,
  BackendContent,
  BackendContentListItem,
  BackendContentPayload,
  BackendSearchContentDocument,
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

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ||
  // Derive from the current host so localhost and LAN-IP access both work
  // without per-device config (the dev cert carries both SANs).
  (typeof window !== 'undefined' ? `https://${window.location.hostname}:8000` : 'https://localhost:8000');

// A public HLS test stream used when the platform has no packaged media for
// the title yet (media-pipeline currently emits stub manifests only). The
// real manifest is preferred whenever it exists.
// Self-hosted demo asset served by streaming-service through the gateway —
// no external stream dependency, works on localhost and LAN hosts alike.
export const DEMO_HLS_URL = `${API_BASE_URL}/streaming/static/demo/hls/master.m3u8`;

const ACCESS_KEY = 'accessToken';
const REFRESH_KEY = 'refreshToken';
const USER_KEY = 'user';

// Auth endpoints that MUST NOT trigger the 401 refresh loop.
// A failed login/register/MFA/refresh/logout should surface the error directly.
const AUTH_ENDPOINTS = [
  '/auth/api/v1/auth/login',
  '/auth/api/v1/auth/register',
  '/auth/api/v1/auth/mfa/login-verify',
  '/auth/api/v1/auth/refresh',
  '/auth/api/v1/auth/logout',
];

function isAuthEndpoint(url: string | undefined): boolean {
  return !!url && AUTH_ENDPOINTS.some((p) => url.includes(p));
}

/**
 * In-memory access token. The refresh token is persisted as an HttpOnly
 * cookie by the /auth-session route handler and never lives in localStorage.
 * This prevents XSS from extracting credentials.
 */
let accessToken: string | null = null;

/**
 * Remove any legacy localStorage/cookie tokens left by pre-HttpOnly builds.
 * Runs on module load (browser) and after every successful token rotation.
 */
function sweepLegacyTokenStorage(): void {
  if (typeof window === 'undefined' || typeof localStorage === 'undefined') return;
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
  // Also expire the old non-HttpOnly cookie the middleware used to read.
  if (typeof document !== 'undefined') {
    document.cookie = `${ACCESS_KEY}=; path=/; max-age=0; samesite=strict`;
  }
}

// Run once on load so upgraded users are migrated immediately.
if (typeof window !== 'undefined' && typeof localStorage !== 'undefined') {
  sweepLegacyTokenStorage();
}

export function getAccessToken(): string | null {
  return accessToken;
}


async function persistRefreshToken(token: string): Promise<void> {
  const response = await fetch('/auth-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: token }),
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not persist session');
}

/**
 * Persist tokens after login/register/MFA. Awaits the HttpOnly-cookie write so
 * an immediate client-side navigation cannot abort the request before the
 * browser commits the cookie (previously a fire-and-forget POST raced
 * router.push, leaving middleware to bounce hard navigations to /login).
 */
export async function setTokens(tokens: AuthTokens): Promise<void> {
  if (!tokens.access_token || !tokens.refresh_token) throw new Error('Invalid authentication response');
  await persistRefreshToken(tokens.refresh_token);
  accessToken = tokens.access_token;
  sweepLegacyTokenStorage();
}

export function clearTokens(): void {
  accessToken = null;
  sweepLegacyTokenStorage();
}

// ---- Normalization: backend DTOs -> UI types ----

/**
 * Map the search-service Elasticsearch document to the content payload
 * consumed by the UI. Search documents are not BackendContent objects:
 * genres are strings and rating is the service's canonical score.
 */
export function normalizeSearchContentDocument(
  item: BackendSearchContentDocument
): BackendContentPayload {
  const title = String(item.title || '');
  const genres = (Array.isArray(item.genres) ? item.genres : [])
    .filter((genre): genre is string => typeof genre === 'string' && genre.length > 0)
    .map((name) => {
      const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
      return { id: slug, name, slug };
    });

  const rating = Number(item.rating || 0);

  return {
    id: String(item.id || ''),
    title,
    slug: title.toLowerCase().replace(/\s+/g, '-'),
    description: String(item.description || ''),
    content_type: String(item.content_type || 'movie'),
    status: String(item.status || 'published'),
    poster_url: null,
    backdrop_url: null,
    audience_score: rating,
    // The search document has no separate audience score: its canonical
    // `rating` is already on the 0-100 scale, so it is both the score and the
    // match percentage. Without this the UI's Match label reads undefined.
    matchPercentage: rating,
    is_premium: false,
    genres,
  };
}

export function normalizeContent(item: BackendContentPayload): Content {
  const type = item.content_type === 'series' || item.content_type === 'show' ? 'show' : 'movie';

  // The API's audience_score is 0-100; keep the UI's existing rating field at 0-10
  // for star displays, and expose the original scale separately for Match labels.
  const audienceScore = Math.min(100, Math.max(0, Number(item.audience_score) || 0));
  const imdbRating = Math.min(10, Math.max(0, Number(item.imdb_rating) || 0));
  const hasAudienceScore = audienceScore > 0;
  const rating = hasAudienceScore ? audienceScore / 10 : imdbRating;
  const matchPercentage = hasAudienceScore ? audienceScore : imdbRating * 10;

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
    rating,
    matchPercentage,
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

export function normalizeUser(payload: Record<string, unknown>): User {
  return {
    id: String(payload.id),
    email: String(payload.email || ''),
    firstName: (payload.first_name as string) || '',
    lastName: (payload.last_name as string) || '',
    emailVerified: Boolean(payload.email_verified),
    role: (payload.role as User['role']) || 'user',
  };
}

/**
 * Convert an arbitrary error from axios/fetch into a user-facing message.
 * Handles rate-limit (429) and unauthorized (401) with tailored copy.
 */
export function getApiErrorMessage(
  error: unknown,
  fallback: string,
  unauthorizedMessage?: string
): string {
  const status = (error as { response?: { status?: number } })?.response?.status;
  if (status === 429) {
    return 'Too many attempts. Please wait a minute and try again.';
  }
  if (status === 401) {
    return unauthorizedMessage ?? fallback;
  }
  return fallback;
}

let refreshPromise: Promise<string | null> | null = null;

class APIClient {
  readonly client: AxiosInstance;

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
        // Skip refresh on auth endpoints to avoid infinite loops on bad credentials.
        if (
          error.response?.status !== 401 ||
          !original ||
          (original as { _retried?: boolean })._retried ||
          isAuthEndpoint(original.url)
        ) {
          return Promise.reject(error);
        }
        (original as { _retried?: boolean })._retried = true;
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
        try {
          const response = await fetch('/auth-session', {
            method: 'GET',
            credentials: 'same-origin',
            cache: 'no-store',
          });
          if (!response.ok) return null;
          const data = (await response.json()) as { access_token?: unknown };
          if (typeof data.access_token !== 'string' || !data.access_token) return null;
          accessToken = data.access_token;
          return data.access_token;
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
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
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
    await setTokens(data as AuthTokens);
    return data as AuthTokens;
  }

  async login(email: string, password: string) {
    const { data } = await this.client.post('/auth/api/v1/auth/login', { email, password });
    if ((data as { requires_mfa?: boolean }).requires_mfa) {
      return data as { requires_mfa: true; mfa_challenge: string; expires_in: number };
    }
    await setTokens(data as AuthTokens);
    return data as AuthTokens;
  }

  async verifyMfaLogin(mfaChallenge: string, code: string) {
    const { data } = await this.client.post('/auth/api/v1/auth/mfa/login-verify', {
      mfa_challenge: mfaChallenge,
      code,
    });
    await setTokens(data as AuthTokens);
    return data as AuthTokens;
  }

  async logout() {
    // Let any rotation commit its cookie before revoking that credential.
    await refreshPromise;
    const token = getAccessToken();
    const response = await fetch('/auth-session', {
      method: 'DELETE',
      credentials: 'same-origin',
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new Error('Could not end session. Please try again.');
    clearTokens();
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
  } = {}): Promise<BackendContentListItem[]> {
    // `response_model=list[ContentListResponse]` on the content-service route --
    // these declared fields are the whole response, not a guess.
    return this.unwrap(
      this.client.get('/content/api/v1/content', { params: { page: 1, page_size: 50, ...params } })
    );
  }

  async getGenres(): Promise<BackendGenre[]> {
    return this.unwrap(this.client.get('/content/api/v1/genres'));
  }

  async getContentById(id: string): Promise<BackendContent> {
    // `response_model=ContentResponse`. Every field `BackendContentPayload`
    // requires is required here too, so callers can pass this straight to
    // `normalizeContent` with no cast.
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

  async searchContent(query: string): Promise<BackendContentPayload[]> {
    try {
      const results = await this.unwrap<{ query: string; results: Record<string, unknown>[] }>(
        this.client.get('/search/api/v1/search/query', { params: { q: query, limit: 30 } })
      );
      return results.results
        .filter((r) => r && r.title)
        .map((r) => normalizeSearchContentDocument(r as unknown as BackendSearchContentDocument));
    } catch {
      const all = await this.getContentList({ page_size: 100 });
      const q = query.toLowerCase();
      return all.filter(
        (c) => c.title.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)
      );
    }
  }

  async getTrending(): Promise<BackendContentPayload[]> {
    try {
      const data = await this.unwrap<{ trending: BackendSearchContentDocument[]; total: number }>(
        this.client.get('/search/api/v1/search/trending', { params: { limit: 20 } })
      );
      if (data.trending?.length) return data.trending.map(normalizeSearchContentDocument);
    } catch {
      // ignore - fall back to score-sorted catalog below
    }
    const all = await this.getContentList({ page_size: 100 });
    return [...all].sort((a, b) => (b.audience_score || 0) - (a.audience_score || 0)).slice(0, 20);
  }

  async getRecommendations(userId: string, limit = 20): Promise<BackendContentPayload[]> {
    try {
      const data = await this.unwrap<{
        recommendations: { content_id: string; score: number; reason?: string }[];
        total: number;
      }>(this.client.get(`/recommendations/api/v1/recommendations/for-user/${userId}`, {
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
        // `filter(Boolean)` does not narrow, which is why this line previously carried
        // an `as` cast. A type predicate is the honest form: these are per-item
        // lookups that can individually fail, and null means "skip this one".
        //
        // The predicate has to name the array's *element* type (`BackendContent`),
        // not this method's return type. A predicate's type must be assignable to
        // its parameter's type, and `BackendContentPayload` leaves `status`
        // optional while `ContentResponse.status` is required. `BackendContent[]`
        // still satisfies the declared `Promise<BackendContentPayload[]>` return.
        const content = items.filter((item): item is BackendContent => item !== null);
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
    return this.unwrap(this.client.get(`/billing/api/v1/billing/subscription/${userId}`));
  }

  async subscribe(userId: string, tier: 'avod' | 'svod' | 'tvod') {
    return this.unwrap(this.client.post(`/billing/api/v1/billing/subscribe/${userId}`, { tier }));
  }

  async cancelSubscription(userId: string) {
    return this.unwrap(this.client.post(`/billing/api/v1/billing/cancel/${userId}`));
  }

  // ---- Analytics ----

  async logEvent(userId: string, eventType: string, eventData?: Record<string, unknown>) {
    try {
      await this.client.post('/analytics/api/v1/analytics/events', {
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