/**
 * Behavioural tests for the domain methods on the shared API client:
 * request shaping, response unwrapping, and — most importantly — the
 * degradation paths (search/trending/recommendation fallbacks, best-effort
 * analytics, profile auto-creation) that keep pages usable when a downstream
 * service is unhealthy.
 */
import { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  apiClient,
  clearTokens,
  getApiErrorMessage,
  normalizeUser,
} from '@/api/client';
import type { BackendContent, User } from '@/types';

type Config = InternalAxiosRequestConfig;

interface Recorded {
  url: string;
  method: string;
  params: unknown;
  data: unknown;
}

let requests: Recorded[] = [];
let restore: (() => void) | null = null;

function path(url: string): string {
  return url.replace(/^https?:\/\/[^/]+/, '');
}

/** Axios serialises the body before the adapter sees it. */
function body(record: Recorded): unknown {
  return typeof record.data === 'string' ? JSON.parse(record.data) : record.data;
}

/** Route requests by (method, path-substring) so a test only scripts what it uses. */
type Route = { match: (r: Recorded, config: Config) => boolean; respond: (r: Recorded, config: Config) => unknown };

function install(routes: Route[]) {
  const adapter = (raw: unknown) => {
    const config = raw as Config;
    const r: Recorded = {
      url: path(config.url ?? config.baseURL ?? ''),
      method: String(config.method ?? 'get').toUpperCase(),
      params: config.params,
      data: config.data,
    };
    requests.push(r);
    const hit = routes.find((route) => route.match(r, config));
    if (!hit) return Promise.reject(new AxiosError(`No route for ${r.method} ${r.url}`, 'ERR_BAD_REQUEST', config, null));
    const body = hit.respond(r, config);
    if (body instanceof Error) return Promise.reject(body);
    return Promise.resolve({ data: body, status: 200, statusText: 'OK', headers: {}, config } as never);
  };
  const previous = apiClient.client.defaults.adapter;
  apiClient.client.defaults.adapter = adapter as never;
  restore = () => {
    apiClient.client.defaults.adapter = previous as never;
  };
}

function httpError(status: number, data: unknown = { detail: 'nope' }) {
  const config = { headers: new AxiosHeaders() } as unknown as Config;
  return new AxiosError(`status ${status}`, 'ERR_BAD_REQUEST', config, null, {
    data,
    status,
    statusText: 'ERR',
    headers: new AxiosHeaders(),
    config,
  } as never);
}

function on(method: string, fragment: string, body: unknown): Route {
  return {
    match: (r) => r.method === method && r.url.includes(fragment),
    respond: () => (body instanceof Error ? body : body),
  };
}

/** Body may be a value or a function of the request, for stateful sequences. */
function onFn(
  method: string,
  fragment: string,
  respond: (r: Recorded) => unknown
): Route {
  return { match: (r) => r.method === method && r.url.includes(fragment), respond: (r) => respond(r) };
}

function content(overrides: Partial<BackendContent> = {}): BackendContent {
  return {
    id: 'c1',
    title: 'Base Title',
    slug: 'base-title',
    description: 'A description.',
    content_type: 'movie',
    status: 'published',
    audience_score: 50,
    ...overrides,
  } as BackendContent;
}

beforeEach(() => {
  requests = [];
  restore = null;
  clearTokens();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ access_token: 'tok' }) }));
});

afterEach(() => {
  restore?.();
  restore = null;
  clearTokens();
  vi.unstubAllGlobals();
});

describe('getApiErrorMessage', () => {
  it('returns a rate-limit message for 429 regardless of the fallback', () => {
    const err = httpError(429, { detail: 'Too many requests from 10.0.0.4' });
    expect(getApiErrorMessage(err, 'Something went wrong')).toBe(
      'Too many attempts. Please wait a minute and try again.'
    );
  });

  it('prefers the caller-supplied unauthorized copy for 401', () => {
    expect(getApiErrorMessage(httpError(401), 'fallback', 'Please log in again')).toBe('Please log in again');
  });

  it('falls back to the generic message for 401 when no copy is supplied', () => {
    expect(getApiErrorMessage(httpError(401), 'Please log in again')).toBe('Please log in again');
  });

  it('falls back for any other status', () => {
    expect(getApiErrorMessage(httpError(500), 'Try again later')).toBe('Try again later');
  });

  it('falls back for a null/undefined error', () => {
    expect(getApiErrorMessage(null, 'Try again later')).toBe('Try again later');
  });

  it('falls back for a network error with no response', () => {
    const err = new AxiosError('Network Error', 'ERR_NETWORK', undefined, null);
    expect(getApiErrorMessage(err, 'You appear to be offline')).toBe('You appear to be offline');
  });
});

describe('auth endpoints', () => {
  it('sends snake_case register fields to the register route', async () => {
    install([on('POST', '/auth/register', { access_token: 'a', refresh_token: 'r' })]);

    await apiClient.register('a@b.c', 'pw', 'Ada', 'Lovelace');

    const req = requests[0];
    expect(req.url).toBe('/auth/api/v1/auth/register');
    expect(body(req)).toEqual({ email: 'a@b.c', password: 'pw', first_name: 'Ada', last_name: 'Lovelace' });
  });

  it('publishes tokens on a plain login', async () => {
    install([on('POST', '/auth/login', { access_token: 'a', refresh_token: 'r' })]);

    const out = await apiClient.login('a@b.c', 'pw');

    expect(out).toEqual({ access_token: 'a', refresh_token: 'r' });
    expect(body(requests[0])).toEqual({ email: 'a@b.c', password: 'pw' });
  });

  it('returns the MFA challenge without persisting a session when one is required', async () => {
    install([
      on('POST', '/auth/login', { requires_mfa: true, mfa_challenge: 'chal-9', expires_in: 300 }),
    ]);

    const out = (await apiClient.login('a@b.c', 'pw')) as { requires_mfa: boolean; mfa_challenge: string };

    expect(out.requires_mfa).toBe(true);
    expect(out.mfa_challenge).toBe('chal-9');
    // No /auth-session write: an MFA challenge is not an authenticated session.
    const fetchMock = globalThis.fetch as ReturnType<typeof vi.fn>;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('posts the MFA challenge and code to login-verify', async () => {
    install([on('POST', '/mfa/login-verify', { access_token: 'a', refresh_token: 'r' })]);

    await apiClient.verifyMfaLogin('chal-9', '123456');

    expect(requests[0].url).toBe('/auth/api/v1/auth/mfa/login-verify');
    expect(body(requests[0])).toEqual({ mfa_challenge: 'chal-9', code: '123456' });
  });

  it('normalizes the /me payload into a User', async () => {
    install([
      on('GET', '/auth/me', {
        id: 7,
        email: 'ada@example.com',
        first_name: 'Ada',
        last_name: 'Lovelace',
        email_verified: 1,
        role: 'admin',
      }),
    ]);

    const user = await apiClient.getMe();

    expect(user).toEqual({
      id: '7',
      email: 'ada@example.com',
      firstName: 'Ada',
      lastName: 'Lovelace',
      emailVerified: true,
      role: 'admin',
    });
  });

  it('coerces a missing role to "user" so admin guards fail closed', () => {
    const user: User = normalizeUser({ id: 'u1', email: 'x@y.z' });
    expect(user.role).toBe('user');
  });
});

describe('getProfile auto-creation on 404', () => {
  it('creates a profile when the user has none yet', async () => {
    let created = false;
    install([
      onFn('GET', '/profiles/u1', () => (created ? httpError(404) : httpError(404))),
      onFn('POST', '/profiles', () => {
        created = true;
        return { id: 'new-profile', user_id: 'u1' };
      }),
    ]);

    const profile = await apiClient.getProfile('u1');

    expect(profile).toEqual({ id: 'new-profile', user_id: 'u1' });
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      'GET /users/api/v1/profiles/u1',
      'POST /users/api/v1/profiles',
    ]);
  });

  it('rethrows when the created profile has no id', async () => {
    install([on('GET', '/profiles/u1', httpError(404)), on('POST', '/profiles', { user_id: 'u1' })]);

    await expect(apiClient.getProfile('u1')).rejects.toMatchObject({ response: { status: 404 } });
  });

  it('does not attempt creation for a non-404 failure', async () => {
    install([on('GET', '/profiles/u1', httpError(500))]);

    await expect(apiClient.getProfile('u1')).rejects.toMatchObject({ response: { status: 500 } });
    expect(requests).toHaveLength(1);
  });

  it('sends the profile id and body on update', async () => {
    install([on('PATCH', '/profiles/u1', { id: 'u1', first_name: 'Grace' })]);

    await apiClient.updateProfile('u1', { first_name: 'Grace' });

    expect(requests[0]).toMatchObject({
      method: 'PATCH',
      url: '/users/api/v1/profiles/u1',
      data: '{"first_name":"Grace"}',
    });
  });
});

describe('searchContent', () => {
  it('maps search hits and synthesises a slug from the title', async () => {
    install([
      on('GET', '/search/query', {
        query: 'neo',
        results: [
          { id: 'c9', title: 'Neon Drift', description: 'Racing', content_type: 'show', rating: 8.5 },
        ],
      }),
    ]);

    const [hit] = await apiClient.searchContent('neo');

    expect(requests[0].params).toEqual({ q: 'neo', limit: 30 });
    expect(hit.id).toBe('c9');
    expect(hit.slug).toBe('neon-drift');
    expect(hit.content_type).toBe('show');
    expect(hit.audience_score).toBe(8.5);
    expect(hit.status).toBe('published');
  });

  it('synthesises a slug from the title when the hit has none', async () => {
    install([on('GET', '/search/query', { results: [{ id: 'c1', title: 'The Long  Take' }] })]);

    const [hit] = await apiClient.searchContent('long');
    expect(hit.slug).toBe('the-long-take');
  });

  it('leaves artwork null when the hit carries none', async () => {
    install([on('GET', '/search/query', { results: [{ id: 'c1', title: 'No Art' }] })]);

    const [hit] = await apiClient.searchContent('no');
    // A fabricated empty-string URL would render as a broken <img> instead of
    // letting the UI fall back to its generated poster art.
    expect(hit.poster_url).toBeNull();
    expect(hit.backdrop_url).toBeNull();
  });

  it('falls back to the plain rating field for audience_score', async () => {
    install([on('GET', '/search/query', { results: [{ id: 'c1', title: 'Rated', rating: 7.4 }] })]);

    const [hit] = await apiClient.searchContent('rated');
    expect(hit.audience_score).toBe(7.4);
  });

  it('defaults content_type to movie and coerces a zero score', async () => {
    install([on('GET', '/search/query', { results: [{ id: 'c1', title: 'Bare' }] })]);

    const [hit] = await apiClient.searchContent('bare');
    expect(hit.content_type).toBe('movie');
    expect(hit.audience_score).toBe(0);
    expect(hit.description).toBe('');
  });

  it('drops hits that have no title', async () => {
    install([
      on('GET', '/search/query', { results: [{ id: 'x' }, { id: 'y', title: 'Real Movie' }] }),
    ]);

    const results = await apiClient.searchContent('real');
    expect(results.map((r) => r.id)).toEqual(['y']);
  });

  it('falls back to filtering the catalogue when search is unavailable', async () => {
    install([
      on('GET', '/search/query', httpError(503)),
      on('GET', '/content/api/v1/content', [
        content({ id: 'a', title: 'Alpha', description: 'x' }),
        content({ id: 'b', title: 'Beta', description: 'about alpha' }),
        content({ id: 'c', title: 'Gamma', description: 'z' }),
      ]),
    ]);

    const results = await apiClient.searchContent('ALPHA');

    expect(results.map((r) => r.id)).toEqual(['a', 'b']);
    expect(requests[1].params).toMatchObject({ page_size: 100 });
  });
});

describe('getTrending degradation', () => {
  it('uses the trending endpoint when it returns rows', async () => {
    install([on('GET', '/search/trending', { trending: [{ id: 't1' }, { id: 't2' }], total: 2 })]);

    const rows = await apiClient.getTrending();

    // Rows are normalized, so they carry synthesized defaults as well as the
    // upstream id. Assert the identity mapping, not the incidental shape.
    expect(rows.map((r) => r.id)).toEqual(['t1', 't2']);
    expect(requests[0].params).toEqual({ limit: 20 });
  });

  it('falls back to the top-scored catalogue rows on a 5xx', async () => {
    install([
      on('GET', '/search/trending', httpError(500)),
      on('GET', '/content/api/v1/content', [
        content({ id: 'low', audience_score: 10 }),
        content({ id: 'high', audience_score: 95 }),
        content({ id: 'mid', audience_score: 50 }),
      ]),
    ]);

    const rows = await apiClient.getTrending();
    expect(rows.map((r) => r.id)).toEqual(['high', 'mid', 'low']);
  });

  it('falls back when the endpoint returns an empty trending array', async () => {
    install([
      on('GET', '/search/trending', { trending: [], total: 0 }),
      on('GET', '/content/api/v1/content', [content({ id: 'only', audience_score: 1 })]),
    ]);

    const rows = await apiClient.getTrending();
    expect(rows.map((r) => r.id)).toEqual(['only']);
  });

  it('caps the fallback at 20 rows', async () => {
    const many = Array.from({ length: 30 }, (_, i) => content({ id: `c${i}`, audience_score: i }));
    install([
      on('GET', '/search/trending', httpError(500)),
      on('GET', '/content/api/v1/content', many),
    ]);

    const rows = await apiClient.getTrending();
    expect(rows).toHaveLength(20);
    expect(rows[0].id).toBe('c29');
  });

  it('treats a missing audience_score as 0 rather than dropping the row', async () => {
    install([
      on('GET', '/search/trending', httpError(500)),
      on('GET', '/content/api/v1/content', [
        content({ id: 'scored', audience_score: 70 }),
        content({ id: 'unscored', audience_score: undefined }),
      ]),
    ]);

    const rows = await apiClient.getTrending();
    expect(rows.map((r) => r.id)).toEqual(['scored', 'unscored']);
  });
});

describe('getRecommendations hydration', () => {
  it('hydrates recommendation ids into full content rows', async () => {
    install([
      on('GET', '/recommendations/for-user/u1', {
        recommendations: [{ content_id: 'c1', score: 0.9 }, { content_id: 'c2', score: 0.8 }],
        total: 2,
      }),
      on('GET', '/content/api/v1/content/c1', content({ id: 'c1', title: 'One' })),
      on('GET', '/content/api/v1/content/c2', content({ id: 'c2', title: 'Two' })),
    ]);

    const rows = await apiClient.getRecommendations('u1');

    expect(rows.map((r) => r.title)).toEqual(['One', 'Two']);
    expect(requests[0].params).toEqual({ limit: 20 });
  });

  it('skips recommendations whose content fetch 404s', async () => {
    install([
      on('GET', '/recommendations/for-user/u1', {
        recommendations: [{ content_id: 'gone', score: 1 }, { content_id: 'c2', score: 0.5 }],
      }),
      on('GET', '/content/api/v1/content/gone', httpError(404)),
      on('GET', '/content/api/v1/content/c2', content({ id: 'c2', title: 'Two' })),
    ]);

    const rows = await apiClient.getRecommendations('u1');
    expect(rows.map((r) => r.id)).toEqual(['c2']);
  });

  it('falls back to the top-rated catalogue when every hydration fails', async () => {
    install([
      on('GET', '/recommendations/for-user/u1', { recommendations: [{ content_id: 'gone', score: 1 }] }),
      on('GET', '/content/api/v1/content/gone', httpError(500)),
      on('GET', '/content/api/v1/content', [
        content({ id: 'a', audience_score: 20 }),
        content({ id: 'b', audience_score: 99 }),
      ]),
    ]);

    const rows = await apiClient.getRecommendations('u1');
    expect(rows.map((r) => r.id)).toEqual(['b', 'a']);
  });

  it('honours the caller limit when hydrating', async () => {
    install([
      on('GET', '/recommendations/for-user/u1', {
        recommendations: [{ content_id: 'c1' }, { content_id: 'c2' }, { content_id: 'c3' }],
      }),
      on('GET', '/content/api/v1/content/c1', content({ id: 'c1' })),
      on('GET', '/content/api/v1/content/c2', content({ id: 'c2' })),
      on('GET', '/content/api/v1/content/c3', content({ id: 'c3' })),
    ]);

    const rows = await apiClient.getRecommendations('u1', 2);
    expect(rows).toHaveLength(2);
    expect(requests[0].params).toEqual({ limit: 2 });
  });

  it('falls back to the catalogue when the recommendations call itself fails', async () => {
    install([
      on('GET', '/recommendations/for-user/u1', httpError(500)),
      on('GET', '/content/api/v1/content', [content({ id: 'z', audience_score: 5 })]),
    ]);

    const rows = await apiClient.getRecommendations('u1');
    expect(rows.map((r) => r.id)).toEqual(['z']);
  });
});

describe('playback sessions', () => {
  it('starts a session with the documented body shape', async () => {
    install([on('POST', '/playback-sessions', { id: 's1', status: 'active' })]);

    await apiClient.startPlaybackSession({
      user_id: 'u1',
      content_id: 'c1',
      episode_id: 'e1',
      device_id: 'd1',
    });

    expect(requests[0].url).toBe('/streaming/api/v1/playback-sessions');
    expect(body(requests[0])).toEqual({
      user_id: 'u1',
      content_id: 'c1',
      episode_id: 'e1',
      device_id: 'd1',
    });
  });

  it('rounds fractional progress to whole seconds', async () => {
    install([on('PATCH', '/playback-sessions/s1', { id: 's1' })]);

    await apiClient.updatePlaybackPosition('s1', 42.7);

    expect(requests[0].url).toBe('/streaming/api/v1/playback-sessions/s1');
    expect(body(requests[0])).toEqual({ current_position_seconds: 43 });
  });

  it('posts to the /end sub-route to close a session', async () => {
    install([on('POST', '/playback-sessions/s1/end', {})]);

    await apiClient.endPlaybackSession('s1');
    expect(requests[0].url).toBe('/streaming/api/v1/playback-sessions/s1/end');
  });

  it('returns the manifest payload for a playable episode', async () => {
    install([on('GET', '/episodes/e1/manifest', { manifest_url: 'https://cdn/x.m3u8', protocol: 'hls' })]);

    const manifest = await apiClient.getManifestForEpisode('e1');
    expect(manifest).toEqual({ manifest_url: 'https://cdn/x.m3u8', protocol: 'hls' });
    expect(requests[0].params).toEqual({ protocol: 'hls' });
  });

  it('returns null instead of throwing when the manifest is missing', async () => {
    install([on('GET', '/episodes/e1/manifest', httpError(404))]);
    expect(await apiClient.getManifestForEpisode('e1')).toBeNull();
  });

  it('returns null when the manifest request times out at the network layer', async () => {
    install([on('GET', '/episodes/e1/manifest', new AxiosError('timeout', 'ECONNABORTED', undefined, null))]);
    expect(await apiClient.getManifestForEpisode('e1')).toBeNull();
  });
});

describe('getWatchHistory', () => {
  it('joins live sessions to their content and drops ended ones', async () => {
    install([
      on('GET', '/users/u1/playback-sessions', [
        { id: 's1', content_id: 'c1', status: 'active' },
        { id: 's2', content_id: 'c2', status: 'ended' },
      ]),
      on('GET', '/content/api/v1/content/c1', content({ id: 'c1', title: 'Live' })),
    ]);

    const history = await apiClient.getWatchHistory('u1');

    expect(history).toHaveLength(1);
    expect(history[0].session.id).toBe('s1');
    expect(history[0].content).toMatchObject({ title: 'Live' });
  });

  it('skips orphaned sessions whose content no longer exists', async () => {
    install([
      on('GET', '/users/u1/playback-sessions', [{ id: 's1', content_id: 'gone', status: 'active' }]),
      on('GET', '/content/api/v1/content/gone', httpError(404)),
    ]);

    expect(await apiClient.getWatchHistory('u1')).toEqual([]);
  });

  it('propagates a failure to list sessions', async () => {
    install([on('GET', '/users/u1/playback-sessions', httpError(500))]);
    await expect(apiClient.getWatchHistory('u1')).rejects.toMatchObject({ response: { status: 500 } });
  });
});

describe('content list defaults', () => {
  it('defaults to the first page of 50', async () => {
    install([on('GET', '/content/api/v1/content', [])]);
    await apiClient.getContentList();
    expect(requests[0].params).toEqual({ page: 1, page_size: 50 });
  });

  it('lets caller params override the defaults', async () => {
    install([on('GET', '/content/api/v1/content', [])]);
    await apiClient.getContentList({ page: 3, content_type: 'series' });
    expect(requests[0].params).toEqual({ page: 3, page_size: 50, content_type: 'series' });
  });
});

describe('device and preference routes', () => {
  it('lists the devices registered for a user', async () => {
    install([on('GET', '/devices/u1', [{ device_id: 'd1' }])]);

    expect(await apiClient.getDevices('u1')).toEqual([{ device_id: 'd1' }]);
    expect(requests[0].url).toBe('/users/api/v1/devices/u1');
  });

  it('registers a device, ignoring the user id argument', async () => {
    install([on('POST', '/api/v1/devices', { device_id: 'd1' })]);

    await apiClient.registerDevice('u1', { device_id: 'd1', device_name: 'Living Room', device_type: 'tv' });

    // The route is user-scoped by the access token, so user_id must not leak
    // into the body or the backend would reject the unknown field.
    expect(requests[0].url).toBe('/users/api/v1/devices');
    expect(body(requests[0])).toEqual({
      device_id: 'd1',
      device_name: 'Living Room',
      device_type: 'tv',
    });
  });

  it('reads preferences for a user', async () => {
    install([on('GET', '/preferences/u1', { autoplay: true })]);

    expect(await apiClient.getPreferences('u1')).toEqual({ autoplay: true });
    expect(requests[0].url).toBe('/users/api/v1/preferences/u1');
  });

  it('patches only the supplied preference keys', async () => {
    install([on('PATCH', '/preferences/u1', { autoplay: false, autoplay_next: true })]);

    const updated = await apiClient.updatePreferences('u1', { autoplay: false });

    expect(updated).toEqual({ autoplay: false, autoplay_next: true });
    expect(requests[0].method).toBe('PATCH');
    expect(body(requests[0])).toEqual({ autoplay: false });
  });
});

describe('catalogue navigation routes', () => {
  it('lists genres', async () => {
    install([on('GET', '/content/api/v1/genres', [{ id: 'g1', name: 'sci-fi' }])]);

    expect(await apiClient.getGenres()).toEqual([{ id: 'g1', name: 'sci-fi' }]);
    expect(requests[0].url).toBe('/content/api/v1/genres');
  });

  it('lists seasons for a show', async () => {
    install([on('GET', '/content/api/v1/content/c1/seasons', [{ season_number: 1 }])]);

    expect(await apiClient.getSeasons('c1')).toEqual([{ season_number: 1 }]);
    expect(requests[0].url).toBe('/content/api/v1/content/c1/seasons');
  });

  it('lists episodes for a season', async () => {
    install([on('GET', '/seasons/s1/episodes', [{ episode_number: 1 }])]);

    expect(await apiClient.getEpisodes('c1', 's1')).toEqual([{ episode_number: 1 }]);
    expect(requests[0].url).toBe('/content/api/v1/content/c1/seasons/s1/episodes');
  });

  it('reads a single playback session', async () => {
    install([on('GET', '/playback-sessions/s1', { id: 's1', status: 'active' })]);

    expect(await apiClient.getPlaybackSession('s1')).toEqual({ id: 's1', status: 'active' });
    expect(requests[0].url).toBe('/streaming/api/v1/playback-sessions/s1');
  });

  it('propagates a missing episode so the watch page can show an error', async () => {
    install([on('GET', '/episodes', httpError(404))]);
    await expect(apiClient.getEpisodes('c1', 's404')).rejects.toMatchObject({ response: { status: 404 } });
  });
});

describe('billing', () => {
  it('reads the subscription for a user', async () => {
    install([on('GET', '/billing/subscription/u1', { tier: 'svod' })]);
    expect(await apiClient.getSubscription('u1')).toEqual({ tier: 'svod' });
  });

  it('posts the requested tier when subscribing', async () => {
    install([on('POST', '/billing/subscribe/u1', { status: 'active' })]);

    await apiClient.subscribe('u1', 'tvod');

    expect(requests[0].url).toBe('/billing/api/v1/billing/subscribe/u1');
    expect(body(requests[0])).toEqual({ tier: 'tvod' });
  });

  it('posts to the cancel route', async () => {
    install([on('POST', '/billing/cancel/u1', { status: 'cancelled' })]);
    await apiClient.cancelSubscription('u1');
    expect(requests[0].url).toBe('/billing/api/v1/billing/cancel/u1');
  });
});

describe('logEvent is best-effort', () => {
  it('posts the event envelope', async () => {
    install([on('POST', '/analytics/events', {})]);

    await apiClient.logEvent('u1', 'playback.start', { content_id: 'c1' });

    expect(body(requests[0])).toEqual({
      user_id: 'u1',
      event_type: 'playback.start',
      event_data: { content_id: 'c1' },
    });
  });

  it('defaults event_data to an empty object', async () => {
    install([on('POST', '/analytics/events', {})]);
    await apiClient.logEvent('u1', 'page.view');
    expect(body(requests[0])).toMatchObject({ event_data: {} });
  });

  it('resolves instead of throwing when analytics is down', async () => {
    install([on('POST', '/analytics/events', httpError(503))]);
    await expect(apiClient.logEvent('u1', 'playback.start')).resolves.toBeUndefined();
  });

  it('resolves instead of throwing on a network failure', async () => {
    install([on('POST', '/analytics/events', new AxiosError('offline', 'ERR_NETWORK', undefined, null))]);
    await expect(apiClient.logEvent('u1', 'playback.start')).resolves.toBeUndefined();
  });
});
