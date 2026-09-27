/**
 * Shared E2E fixtures for the Wildframe web app.
 *
 * The suite runs against `next dev` with NO backend, so every spec must be
 * deterministic without a live api-gateway. Two things are faked here:
 *
 *  1. The session. `src/proxy.ts` (middleware) treats the presence of the
 *     HttpOnly `__Host-wf_refresh` cookie as an optimistic route hint, and
 *     `src/api/client.ts` keeps the access token in a module-level variable
 *     that is rehydrated by `GET /auth-session`. We therefore:
 *       - set the refresh cookie so middleware stops redirecting, and
 *       - intercept the *browser* call to `/auth-session` (it is a Next.js
 *         route handler, so the refresh itself happens server-side and cannot
 *         be intercepted — fulfilling the response is enough because the
 *         client only cares about the returned `access_token`).
 *
 *  2. The gateway. All `https://localhost:8000/**` traffic is routed to
 *     fixture payloads, so specs assert on real, specific titles rather than
 *     on a loading spinner. Unmocked endpoints return 404 so an accidental
 *     dependency on a new API shows up as a failed assertion.
 */
import { test as base, expect, type Page } from '@playwright/test';

export const API_ORIGIN = 'https://localhost:8000';
export const REFRESH_COOKIE = '__Host-wf_refresh';
export const MOCK_ACCESS_TOKEN = 'e2e-access-token';
export const MOCK_REFRESH_TOKEN = 'e2e-refresh-token';

export interface MockUser {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  email_verified: boolean;
  role: 'user' | 'admin' | 'moderator';
}

export const VIEWER: MockUser = {
  id: 'e2e-user-0001',
  email: 'ada@wildframe.test',
  first_name: 'Ada',
  last_name: 'Lovelace',
  email_verified: true,
  role: 'user',
};

export const ADMIN: MockUser = {
  id: 'e2e-admin-0001',
  email: 'grace@wildframe.test',
  first_name: 'Grace',
  last_name: 'Hopper',
  email_verified: true,
  role: 'admin',
};

// ---------------------------------------------------------------------------
// Catalog fixtures
// ---------------------------------------------------------------------------

export const MOVIE_ID = 'wf-movie-nightfall';
export const SHOW_ID = 'wf-show-meridian';

interface FixtureGenre {
  id: string;
  name: string;
}

interface FixtureEpisode {
  id: string;
  episode_number: number;
  title: string;
  duration_minutes: number;
  release_date: string;
}

interface FixtureSeason {
  id: string;
  season_number: number;
  title?: string;
  episode_count: number;
  episodes: FixtureEpisode[];
}

/** Mirrors the shape `apiClient` consumes (`BackendContent`). */
interface FixtureContent {
  id: string;
  title: string;
  slug: string;
  description: string;
  content_type: 'movie' | 'series';
  status: string;
  release_date?: string | null;
  duration_minutes?: number | null;
  audience_score: number;
  content_rating?: string | null;
  is_hd?: boolean;
  poster_url?: string | null;
  backdrop_url?: string | null;
  genres: FixtureGenre[];
  seasons?: FixtureSeason[];
}

export const MOVIES: FixtureContent[] = [
  {
    id: MOVIE_ID,
    title: 'Nightfall Protocol',
    slug: 'nightfall-protocol',
    description: 'A courier races a dying city to hand off a single reel of film.',
    content_type: 'movie',
    status: 'published',
    release_date: '2024-03-18',
    duration_minutes: 118,
    audience_score: 8.4,
    content_rating: 'PG-13',
    is_hd: true,
    poster_url: null,
    backdrop_url: null,
    genres: [{ id: 'g-1', name: 'Thriller' }],
  },
  {
    id: 'wf-movie-saltflats',
    title: 'Salt Flats',
    slug: 'salt-flats',
    description: 'Two surveyors race a storm across a dry lakebed.',
    content_type: 'movie',
    status: 'published',
    release_date: '2023-11-02',
    duration_minutes: 96,
    audience_score: 7.1,
    content_rating: 'R',
    is_hd: false,
    poster_url: null,
    backdrop_url: null,
    genres: [{ id: 'g-2', name: 'Drama' }],
  },
  {
    id: 'wf-movie-lantern',
    title: 'Lantern District',
    slug: 'lantern-district',
    description: 'A festival crowd discovers the lights are lying.',
    content_type: 'movie',
    status: 'published',
    release_date: '2025-01-09',
    duration_minutes: 104,
    audience_score: 9.0,
    content_rating: 'PG',
    is_hd: true,
    poster_url: null,
    backdrop_url: null,
    genres: [{ id: 'g-1', name: 'Thriller' }],
  },
  // Third Thriller title: `browse` only renders a genre row once a genre has
  // at least three items, so this entry is what makes the "Thriller" row
  // appear and keeps that assertion meaningful.
  {
    id: 'wf-movie-harbourlights',
    title: 'Harbour Lights',
    slug: 'harbour-lights',
    description: 'A lighthouse keeper logs a ship that never docked.',
    content_type: 'movie',
    status: 'published',
    release_date: '2024-09-27',
    duration_minutes: 88,
    audience_score: 7.9,
    content_rating: 'PG-13',
    is_hd: false,
    poster_url: null,
    backdrop_url: null,
    genres: [{ id: 'g-1', name: 'Thriller' }],
  },
];

export const SHOWS: FixtureContent[] = [
  {
    id: SHOW_ID,
    title: 'Meridian Station',
    slug: 'meridian-station',
    description: 'The last train out is already full of people who should not be on it.',
    content_type: 'series',
    status: 'published',
    release_date: '2022-06-14',
    duration_minutes: null,
    audience_score: 8.8,
    content_rating: 'TV-MA',
    is_hd: true,
    poster_url: null,
    backdrop_url: null,
    genres: [{ id: 'g-3', name: 'Sci-Fi' }],
    seasons: [
      {
        id: 'wf-season-1',
        season_number: 1,
        title: 'Platform 9',
        episode_count: 2,
        episodes: [
          {
            id: 'wf-ep-1',
            episode_number: 1,
            title: 'Departure Board',
            duration_minutes: 48,
            release_date: '2022-06-14',
          },
          {
            id: 'wf-ep-2',
            episode_number: 2,
            title: 'Sleeper Car',
            duration_minutes: 44,
            release_date: '2022-06-21',
          },
        ],
      },
    ],
  },
];

export const ALL_CONTENT: FixtureContent[] = [...MOVIES, ...SHOWS];

/** Highest-scoring title — the fallback ordering `getTrending` uses. */
export const TOP_RATED_TITLE = 'Lantern District';

export const PROFILE = {
  id: 'e2e-profile-0001',
  user_id: VIEWER.id,
  bio: 'Writes changelogs for fun.',
  phone_number: '+1-555-0142',
  country: 'New Zealand',
  language: 'en',
  avatar_url: null,
  profile_completeness: 72,
  created_at: '2024-02-01T00:00:00Z',
};

export const PREMIUM_SUBSCRIPTION = {
  id: 'e2e-subscription-0001',
  user_id: VIEWER.id,
  tier: 'svod',
  subscription_status: 'active',
  monthly_price: 7.99,
  started_at: '2024-02-01T00:00:00Z',
  renews_at: '2030-02-01T00:00:00Z',
};

export const DEVICES = [
  {
    id: 'e2e-device-0001',
    user_id: VIEWER.id,
    device_id: 'web-player',
    device_name: 'Living Room TV',
    device_type: 'tv',
    is_active: true,
    is_trusted: true,
    last_active_at: '2025-05-01T10:00:00Z',
  },
];

export const PREFERENCES = {
  user_id: VIEWER.id,
  autoplay: true,
  autoplay_next_episode: false,
  closed_captions: true,
  allow_explicit_content: false,
  email_new_content: true,
  language: 'en',
};

export const ADMIN_STATS = {
  total_users: 12480,
  active_users: 3120,
  flagged_content: 7,
  active_alerts: 2,
  system_uptime_hours: 51,
};

export const ADMIN_USERS = [
  {
    user_id: 'e2e-user-0001',
    email: 'ada@wildframe.test',
    first_name: 'Ada',
    last_name: 'Lovelace',
    status: 'active',
    reason: null,
    created_at: '2024-02-01T00:00:00Z',
  },
  {
    user_id: 'e2e-user-0002',
    email: 'linus@wildframe.test',
    first_name: 'Linus',
    last_name: 'Torvalds',
    status: 'suspended',
    reason: 'chargeback fraud',
    created_at: '2024-06-11T00:00:00Z',
  },
];

export const ADMIN_FLAGS = [
  {
    id: 'e2e-flag-1',
    content_id: MOVIE_ID,
    content_type: 'movie',
    reason: 'Explicit content',
    status: 'open',
    flagged_at: '2025-04-30T08:00:00Z',
  },
  {
    id: 'e2e-flag-2',
    content_id: SHOW_ID,
    content_type: 'series',
    reason: 'Copyright claim',
    status: 'review',
    flagged_at: '2025-05-01T08:00:00Z',
  },
];

export const ADMIN_ALERTS = [
  {
    id: 'e2e-alert-1',
    alert_type: 'high-latency',
    severity: 'warning',
    message: 'streaming-service p95 above 900ms',
    service: 'streaming-service',
    acknowledged: false,
    acknowledged_by: null,
    created_at: '2025-05-02T09:00:00Z',
  },
  {
    id: 'e2e-alert-2',
    alert_type: 'db-replication-lag',
    severity: 'critical',
    message: 'Replica falling behind primary by 40s',
    service: 'user-service',
    acknowledged: true,
    acknowledged_by: ADMIN.id,
    created_at: '2025-05-02T07:00:00Z',
  },
];

export const ADMIN_CONFIGS = [
  {
    id: 'e2e-config-1',
    key: 'rate_limit.rps',
    value: '120',
    config_type: 'integer',
    description: 'Gateway-wide requests per second',
  },
  {
    id: 'e2e-config-2',
    key: 'streaming.preview_url',
    value: 'https://cdn.wildframe.test/demo.m3u8',
    config_type: 'string',
    description: 'Fallback HLS asset for unpackaged titles',
  },
];

export const ADMIN_AUDIT = [
  {
    id: 'e2e-audit-1',
    admin_id: ADMIN.id,
    action: 'user_moderation',
    resource_type: 'user',
    resource_id: 'e2e-user-0002',
    changes: '{"status":"suspended"}',
    created_at: '2025-05-01T12:00:00Z',
  },
  {
    id: 'e2e-audit-2',
    admin_id: ADMIN.id,
    action: 'set_config',
    resource_type: 'config',
    resource_id: 'rate_limit.rps',
    changes: '{"value":"120"}',
    created_at: '2025-05-02T12:00:00Z',
  },
];

export const PLAYBACK_SESSION = {
  id: 'e2e-playback-0001',
  user_id: VIEWER.id,
  content_id: MOVIE_ID,
  episode_id: null,
  device_id: 'web-player',
  status: 'active',
  current_position_seconds: 0,
  total_duration_seconds: 7080,
  started_at: '2025-05-03T20:00:00Z',
};

/**
 * `apiClient.getWatchHistory` returns raw `PlaybackSession[]` and resolves each
 * `content_id` to a catalog entry, so My List receives `{session, content}`
 * pairs built from these.
 */
export const WATCH_HISTORY_SESSIONS = [
  {
    id: 'e2e-playback-resume-1',
    user_id: VIEWER.id,
    content_id: MOVIE_ID,
    episode_id: null,
    device_id: 'web-player',
    status: 'active',
    current_position_seconds: 3540,
    total_duration_seconds: 7080,
    started_at: '2025-05-03T20:00:00Z',
  },
  {
    // Ended sessions are filtered out by the client, so this one must never
    // reach the grid — it doubles as a regression guard.
    id: 'e2e-playback-ended-1',
    user_id: VIEWER.id,
    content_id: SHOW_ID,
    episode_id: null,
    device_id: 'web-player',
    status: 'ended',
    current_position_seconds: 100,
    total_duration_seconds: 2880,
    started_at: '2025-04-01T20:00:00Z',
  },
];

// ---------------------------------------------------------------------------
// Mocking helpers
// ---------------------------------------------------------------------------

export interface GatewayOptions {
  /** Identity returned by `/auth/api/v1/auth/me`. Omit for anonymous specs. */
  user?: MockUser;
  /**
   * Seed the HttpOnly refresh cookie + a working `GET /auth-session` so the
   * page boots already signed in. Set to `false` to exercise the sign-in form
   * itself: the gateway endpoints stay mocked but the visitor starts logged
   * out, exactly like a real first-time visitor.
   */
  withSessionCookie?: boolean;
  /** Catalog returned by `/content/api/v1/content`. Default: MOVIES + SHOWS. */
  content?: FixtureContent[] | null;
  /** Playback sessions returned by the user history endpoint. */
  watchHistory?: unknown[];
  /** Force every unmocked gateway endpoint to fail, for error-state specs. */
  failAll?: boolean;
}

type RouteHandler = (route: import('@playwright/test').Route) => void | Promise<void>;

async function json(route: import('@playwright/test').Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

/**
 * Install the fake session + gateway for a page. Call this before `goto`.
 */
export async function mockGateway(page: Page, options: GatewayOptions = {}): Promise<void> {
  const {
    user,
    content = ALL_CONTENT,
    watchHistory = [],
    failAll = false,
    withSessionCookie = true,
  } = options;
  const catalog = content === null ? [] : content;
  const hasSession = Boolean(user) && withSessionCookie;

  // ---- Session -----------------------------------------------------------
  // Providers -> hydrate() -> GET /auth-session -> access token -> GET /me.
  await page.route('**/auth-session', async (route) => {
    if (route.request().method() === 'GET') {
      if (!hasSession) return json(route, { error: 'no_session' }, 401);
      return json(route, { access_token: MOCK_ACCESS_TOKEN });
    }
    return json(route, { ok: true });
  });

  if (hasSession) {
    // Presence of the refresh cookie is what src/proxy.ts checks before it
    // decides whether to bounce a protected route to /login. `url` alone is
    // accepted by addCookies (specifying both url and path is rejected) and
    // yields a Secure, host-only cookie — which the `__Host-` prefix requires.
    await page.context().addCookies([
      {
        name: REFRESH_COOKIE,
        value: MOCK_REFRESH_TOKEN,
        url: 'https://localhost:3000',
        httpOnly: true,
        sameSite: 'Lax',
      },
    ]);
  }

  // ---- Gateway -----------------------------------------------------------
  const handler: RouteHandler = async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const path = url.pathname;
    const json_ = (body: unknown, status = 200) => json(route, body, status);

    if (failAll) return json_({ detail: 'Not Found' }, 404);

    // ---- auth-service ----
    if (path === '/auth/api/v1/auth/me') {
      if (!user) return json_({ detail: 'Unauthorized' }, 401);
      return json_(user);
    }
    if (path === '/auth/api/v1/auth/login') {
      return json_({ access_token: MOCK_ACCESS_TOKEN, refresh_token: MOCK_REFRESH_TOKEN });
    }
    if (path === '/auth/api/v1/auth/register') {
      return json_({ access_token: MOCK_ACCESS_TOKEN, refresh_token: MOCK_REFRESH_TOKEN });
    }
    if (path === '/auth/api/v1/auth/logout') {
      return json_({ ok: true });
    }

    // ---- content-service ----
    if (path === '/content/api/v1/content' && method === 'GET') {
      const type = url.searchParams.get('content_type');
      const filtered = type ? catalog.filter((c) => c.content_type === type) : catalog;
      return json_(filtered);
    }
    if (path.startsWith('/content/api/v1/content/') && method === 'GET') {
      // Strip the known prefix so the remaining segments are addressable by
      // name rather than by a hand-counted index.
      const rest = path.slice('/content/api/v1/content/'.length).split('/');
      const contentId = rest[0];
      if (rest[1] === 'seasons' && rest.length === 2) {
        const show = catalog.find((c) => c.id === contentId);
        return json_(show?.content_type === 'series' ? show.seasons ?? [] : []);
      }
      if (rest[1] === 'seasons' && rest[2] === 'episodes') {
        const show = catalog.find((c) => c.id === contentId);
        return json_(show?.seasons?.[0]?.episodes ?? []);
      }
      const found = catalog.find((c) => c.id === contentId);
      if (!found) return json_({ detail: 'Not Found' }, 404);
      return json_(found);
    }
    if (path === '/content/api/v1/genres') {
      const names = Array.from(
        new Set(catalog.flatMap((c) => c.genres.map((g) => g.name)))
      ).map((name, i) => ({ id: `g-${i}`, name }));
      return json_(names);
    }

    // ---- search-service ----
    if (path === '/search/api/v1/search/trending') {
      return json_({ trending: catalog, total: catalog.length });
    }
    if (path === '/search/api/v1/search/query') {
      const q = (url.searchParams.get('q') ?? '').toLowerCase();
      const results = catalog.filter(
        (c) =>
          c.title.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q)
      );
      return json_({ query: url.searchParams.get('q'), results, total: results.length });
    }

    // ---- recommendation-service ----
    if (path.startsWith('/recommendations/api/v1/recommendations/for-user/')) {
      return json_({ recommendations: [], total: 0 });
    }

    // ---- streaming-service ----
    if (path === '/streaming/api/v1/playback-sessions' && method === 'POST') {
      return json_(PLAYBACK_SESSION, 201);
    }
    if (path === '/streaming/api/v1/playback-sessions' && method === 'PATCH') {
      return json_(PLAYBACK_SESSION);
    }
    if (path.startsWith('/streaming/api/v1/users/') && path.endsWith('/playback-sessions')) {
      return json_(watchHistory);
    }
    if (path.startsWith('/streaming/api/v1/episodes/') && path.endsWith('/manifest')) {
      return json_({ manifest_url: null, protocol: 'hls' });
    }
    // Keep hls.js away from the real CDN/gateway; the player degrades to the
    // preview stream notice, which the spec asserts instead of video frames.
    if (path.includes('/streaming/static/demo/hls/')) {
      return route.fulfill({ status: 404, body: '' });
    }

    // ---- user-service ----
    if (path.startsWith('/users/api/v1/profiles/') && method === 'GET') {
      return json_({ ...PROFILE, user_id: user?.id ?? VIEWER.id });
    }
    if (path.startsWith('/users/api/v1/profiles/') && method === 'PATCH') {
      return json_({ ...PROFILE, user_id: user?.id ?? VIEWER.id });
    }
    if (path === '/users/api/v1/profiles' && method === 'POST') {
      return json_({ ...PROFILE, user_id: user?.id ?? VIEWER.id }, 201);
    }
    if (path.startsWith('/users/api/v1/devices/')) {
      return json_(user ? DEVICES : []);
    }
    if (path.startsWith('/users/api/v1/preferences/')) {
      return json_(user ? PREFERENCES : {});
    }

    // ---- billing-service ----
    if (path.startsWith('/billing/api/v1/billing/subscription/')) {
      return json_(PREMIUM_SUBSCRIPTION);
    }
    if (path.startsWith('/billing/api/v1/billing/subscribe/')) {
      return json_({ ...PREMIUM_SUBSCRIPTION, tier: 'avod' });
    }

    // ---- admin-service ----
    if (path === '/admin/api/v1/admin/stats') return json_(ADMIN_STATS);
    if (path === '/admin/api/v1/admin/users/moderated') return json_(ADMIN_USERS);
    if (path === '/admin/api/v1/admin/content/flagged') return json_(ADMIN_FLAGS);
    if (path === '/admin/api/v1/admin/alerts') return json_(ADMIN_ALERTS);
    if (path === '/admin/api/v1/admin/config') return json_(ADMIN_CONFIGS);
    if (path.startsWith('/admin/api/v1/admin/audit/admin/')) return json_(ADMIN_AUDIT);
    if (path.startsWith('/admin/api/v1/admin/audit/resource/')) return json_(ADMIN_AUDIT);

    return json_({ detail: 'Not Found' }, 404);
  };

  await page.route(`${API_ORIGIN}/**`, handler);
}

/** Anonymous visitor: no session cookie, no API mocks. */
export async function anonymous(page: Page): Promise<void> {
  await page.route('**/auth-session', (route) =>
    json(route, { error: 'no_session' }, 401)
  );
}

/**
 * A visitor who is not signed in yet, but whose backend is reachable — the
 * state the `/login` and `/signup` forms start from. The identity is still
 * available to `/auth/api/v1/auth/me` so that a successful form submission
 * can complete `login()`/`register()`, which call `getMe()` before resolving.
 */
export async function loggedOut(page: Page): Promise<void> {
  await mockGateway(page, { user: VIEWER, withSessionCookie: false });
}

/** Authenticated visitor with a mocked session and a mocked catalog. */
export async function signedIn(
  page: Page,
  options: Omit<GatewayOptions, 'user'> & { user?: MockUser } = {}
): Promise<void> {
  await mockGateway(page, { user: VIEWER, ...options });
}

/** Authenticated admin with a mocked session and a mocked admin API. */
export async function signedInAsAdmin(
  page: Page,
  options: Omit<GatewayOptions, 'user'> = {}
): Promise<void> {
  await mockGateway(page, { user: ADMIN, ...options });
}

/** Assert the middleware bounced a protected route to the sign-in screen. */
export async function expectRedirectedToLogin(page: Page): Promise<void> {
  await expect(page).toHaveURL(/\/login(\?|$)/);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Sign In' })
  ).toBeVisible();
}

export const test = base;
export { expect };
