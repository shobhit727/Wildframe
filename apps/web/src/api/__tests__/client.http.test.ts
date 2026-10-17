/**
 * Transport-level tests for the shared axios instance in `@/api/client`.
 *
 * The instance is a module-level singleton whose interceptors (Authorization
 * injection + single-flight 401 refresh) are the part every page depends on.
 * Instead of stubbing `fetch`/XHR we install a custom axios *adapter*: that
 * runs the real request/response interceptor chain while giving us full
 * control over status codes, bodies and observed request config.
 */
import { AxiosError, AxiosHeaders } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiClient, clearTokens, getAccessToken, setTokens } from '@/api/client';

/**
 * The fully-built config an axios adapter receives. `defaults.adapter` is typed
 * as a config *or an array of configs*, so the parameter type is derived from
 * the adapter signature rather than the config type.
 */
type Config = Parameters<Extract<typeof apiClient.client.defaults.adapter, (...a: never[]) => unknown>>[0];

interface Recorded {
  url: string;
  method: string;
  params: unknown;
  data: unknown;
  authorization: string | undefined;
}

const requests: Recorded[] = [];
let restore: (() => void) | null = null;

const REAL_LOCATION = Object.getOwnPropertyDescriptor(window, 'location');

/**
 * Replace `window.location` with a recording stub. `clearAuth()` hard-navigates
 * with `window.location.href = '/login'`, which jsdom cannot perform, so the
 * navigation target has to be observable some other way.
 */
function stubLocation(pathname: string): string[] {
  const assigned: string[] = [];
  const fake: Record<string, unknown> = {
    origin: 'https://localhost:3000',
    pathname,
    assign: (v: unknown) => {
      assigned.push(String(v));
    },
    replace: (v: unknown) => {
      assigned.push(String(v));
    },
  };
  // `href` must be an accessor on a *stable* object: production code reads
  // `pathname` off `window.location` and writes `window.location.href = ...`.
  Object.defineProperty(fake, 'href', {
    configurable: true,
    get: () => `https://localhost:3000${pathname}`,
    set: (v: unknown) => {
      assigned.push(String(v));
    },
  });
  Object.defineProperty(window, 'location', {
    configurable: true,
    get: () => fake,
    set: (v: unknown) => {
      assigned.push(String(v));
    },
  });
  return assigned;
}

/** Strip the absolute baseURL so assertions read like the call sites do. */
function path(url: string): string {
  return url.replace(/^https?:\/\/[^/]+/, '');
}

/** The bearer header the request interceptor attached, if any. */
function authOf(config: Config): string | undefined {
  const value = AxiosHeaders.from(config.headers).get('Authorization');
  return typeof value === 'string' ? value : undefined;
}

function record(config: Config): Recorded {
  const entry: Recorded = {
    url: path(config.url ?? config.baseURL ?? ''),
    method: String(config.method ?? 'get').toUpperCase(),
    params: config.params,
    data: config.data,
    authorization: authOf(config),
  };
  requests.push(entry);
  return entry;
}

function resolve(config: Config, data: unknown, status = 200): Promise<never> {
  return Promise.resolve({ data, status, statusText: 'OK', headers: {}, config } as never);
}

function reject(config: Config, status: number, data: unknown = { detail: 'boom' }): Promise<never> {
  const response = {
    data,
    status,
    statusText: 'ERR',
    headers: new AxiosHeaders(),
    config,
  } as never;
  return Promise.reject(new AxiosError(`Request failed with status code ${status}`, 'ERR_BAD_REQUEST', config, null, response));
}

/**
 * Queue of canned outcomes, consumed in order. Anything past the end repeats
 * the last one, so a single-outcome setup is still readable.
 */
function script(...outcomes: Array<{ status: number; data: unknown }>) {
  let i = 0;
  const adapter = (raw: unknown) => {
    const config = raw as Config;
    record(config);
    const step = outcomes[Math.min(i, outcomes.length - 1)];
    i += 1;
    return step.status >= 200 && step.status < 300
      ? resolve(config, step.data, step.status)
      : reject(config, step.status, step.data);
  };
  return adapter;
}

function install(adapter: (config: Config) => Promise<never>) {
  const previous = apiClient.client.defaults.adapter;
  apiClient.client.defaults.adapter = adapter as never;
  restore = () => {
    apiClient.client.defaults.adapter = previous as never;
  };
}

function jsonFetch(accessToken: string) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ access_token: accessToken }),
  });
}

/**
 * Publish a token through the real `setTokens` path (so the in-memory token is
 * set by production code, not by poking module state) and hand back the
 * persist-fetch mock. Callers that need a different `/auth-session` behaviour
 * re-stub `fetch` afterwards.
 */
async function authenticated(access = 'tok', refresh = 'ref') {
  const persist = vi.fn().mockResolvedValue({ ok: true });
  vi.stubGlobal('fetch', persist);
  await setTokens({ access_token: access, refresh_token: refresh });
  return persist;
}

beforeEach(() => {
  requests.length = 0;
  restore = null;
  clearTokens();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
});

afterEach(() => {
  restore?.();
  restore = null;
  clearTokens();
  if (REAL_LOCATION) Object.defineProperty(window, 'location', REAL_LOCATION);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('request interceptor — Authorization header', () => {
  it('omits the Authorization header when no token is held', async () => {
    install(script({ status: 200, data: [] }));
    await apiClient.getContentList();
    expect(requests).toHaveLength(1);
    expect(requests[0].authorization).toBeUndefined();
  });

  it('injects `Bearer <token>` once a token is published', async () => {
    await authenticated('tok-abc', 'ref-abc');
    install(script({ status: 200, data: [] }));
    await apiClient.getContentList();
    expect(requests[0].authorization).toBe('Bearer tok-abc');
  });

  it('stops injecting the header after clearTokens', async () => {
    await authenticated('tok-abc', 'ref-abc');
    clearTokens();
    install(script({ status: 200, data: [] }));
    await apiClient.getContentList();
    expect(requests[0].authorization).toBeUndefined();
  });
});

describe('response interceptor — 401 refresh', () => {
  it('refreshes and replays the original request with the new token', async () => {
    await authenticated('stale', 'ref');
    const fetchMock = jsonFetch('fresh');
    vi.stubGlobal('fetch', fetchMock);

    install(script({ status: 401, data: { detail: 'expired' } }, { status: 200, data: [{ id: 'c1' }] }));

    const data = await apiClient.getContentList();

    expect(data).toEqual([{ id: 'c1' }]);
    expect(requests).toHaveLength(2);
    expect(requests[0].authorization).toBe('Bearer stale');
    expect(requests[1].authorization).toBe('Bearer fresh');
    // The replay is the *same* request, not a new one.
    expect(requests[1].url).toBe(requests[0].url);
    expect(requests[1].params).toEqual(requests[0].params);
  });

  it('does not attempt a refresh for auth endpoints (no infinite loop)', async () => {
    const fetchMock = jsonFetch('fresh');
    vi.stubGlobal('fetch', fetchMock);

    install(script({ status: 401, data: { detail: 'bad credentials' } }));

    await expect(apiClient.login('a@b.c', 'wrong')).rejects.toMatchObject({ response: { status: 401 } });

    expect(requests).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getAccessToken()).toBeNull();
  });

  it('rejects and drops the token when the refresh call itself fails', async () => {
    await authenticated('stale', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    const navigations = stubLocation('/watch/42');

    install(script({ status: 401, data: { detail: 'expired' } }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 401 } });

    // Observable effects of clearAuth(): the token is discarded and the user
    // is bounced to /login instead of staring at a dead page.
    expect(getAccessToken()).toBeNull();
    expect(navigations).toEqual(['/login']);
    expect(requests).toHaveLength(1);
  });

  it('does not redirect when the failed refresh happens on the login page', async () => {
    await authenticated('stale', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
    const navigations = stubLocation('/login');

    install(script({ status: 401, data: {} }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 401 } });
    // Re-navigating to the page you are already on would reload it and drop
    // whatever the user typed into the login form.
    expect(navigations).toEqual([]);
  });

  it('rejects and drops the token when refresh returns a non-string access_token', async () => {
    await authenticated('stale', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ access_token: 42 }) }));
    stubLocation('/browse');

    install(script({ status: 401, data: {} }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 401 } });
    expect(getAccessToken()).toBeNull();
  });

  it('rejects and drops the token when the refresh fetch throws', async () => {
    await authenticated('stale', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    stubLocation('/browse');

    install(script({ status: 401, data: {} }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 401 } });
    expect(getAccessToken()).toBeNull();
  });

  it('only retries once — a second 401 is surfaced, not looped on', async () => {
    await authenticated('stale', 'ref');
    vi.stubGlobal('fetch', jsonFetch('fresh'));

    install(script({ status: 401, data: {} }, { status: 401, data: { detail: 'still bad' } }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 401 } });

    expect(requests).toHaveLength(2);
    expect(getAccessToken()).toBe('fresh');
  });

  it('passes non-401 failures straight through untouched', async () => {
    await authenticated('tok', 'ref');
    const fetchMock = jsonFetch('fresh');
    vi.stubGlobal('fetch', fetchMock);

    install(script({ status: 500, data: { detail: 'server exploded' } }));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ response: { status: 500 } });

    expect(requests).toHaveLength(1);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe('tok');
  });

  it('passes transport failures (no response object) through untouched', async () => {
    await authenticated('tok', 'ref');
    const fetchMock = jsonFetch('fresh');
    vi.stubGlobal('fetch', fetchMock);

    install(() => Promise.reject(new AxiosError('Network Error', 'ERR_NETWORK', {} as Config, null)));

    await expect(apiClient.getContentList()).rejects.toMatchObject({ code: 'ERR_NETWORK' });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe('refreshAccessToken single-flight', () => {
  it('collapses concurrent callers onto one /auth-session request', async () => {
    const fetchMock = jsonFetch('shared-token');
    vi.stubGlobal('fetch', fetchMock);

    const [a, b, c] = await Promise.all([
      apiClient.refreshAccessToken(),
      apiClient.refreshAccessToken(),
      apiClient.refreshAccessToken(),
    ]);

    expect([a, b, c]).toEqual(['shared-token', 'shared-token', 'shared-token']);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('allows a new refresh after the first settles', async () => {
    const fetchMock = jsonFetch('token-1');
    vi.stubGlobal('fetch', fetchMock);

    expect(await apiClient.refreshAccessToken()).toBe('token-1');
    expect(await apiClient.refreshAccessToken()).toBe('token-1');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('requests the session with GET, same-origin credentials and no caching', async () => {
    const fetchMock = jsonFetch('tok');
    vi.stubGlobal('fetch', fetchMock);

    await apiClient.refreshAccessToken();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/auth-session');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('same-origin');
    expect(init.cache).toBe('no-store');
  });

  it('returns null for a 204-style empty body with no access_token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
    expect(await apiClient.refreshAccessToken()).toBeNull();
  });

  it('returns null for an empty-string access_token', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ access_token: '' }) }));
    expect(await apiClient.refreshAccessToken()).toBeNull();
  });

  it('leaves the previously held token untouched when refresh fails', async () => {
    await authenticated('keep-me', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));

    expect(await apiClient.refreshAccessToken()).toBeNull();
    expect(getAccessToken()).toBe('keep-me');
  });
});

describe('setTokens / logout session handling', () => {
  it('refuses to publish a response missing the access token', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      setTokens({ access_token: '', refresh_token: 'ref' })
    ).rejects.toThrow('Invalid authentication response');
    expect(fetchMock).not.toHaveBeenCalled();
    expect(getAccessToken()).toBeNull();
  });

  it('refuses to publish a response missing the refresh token', async () => {
    await expect(
      setTokens({ access_token: 'acc', refresh_token: '' })
    ).rejects.toThrow('Invalid authentication response');
    expect(getAccessToken()).toBeNull();
  });

  it('does not publish the access token when the cookie write fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));

    await expect(
      setTokens({ access_token: 'acc', refresh_token: 'ref' })
    ).rejects.toThrow('Could not persist session');
    // Critical ordering guarantee: a failed persist must not leave a token in
    // memory, or the app would treat the user as authenticated with no cookie.
    expect(getAccessToken()).toBeNull();
  });

  it('logs out by sending DELETE with the bearer token, then clears it', async () => {
    await authenticated('tok', 'ref');
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    await apiClient.logout();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/auth-session');
    expect(init.method).toBe('DELETE');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(getAccessToken()).toBeNull();
  });

  it('sends no Authorization header on logout when no token is held', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);

    await apiClient.logout();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toEqual({});
  });

  it('surfaces a logout failure and keeps the session alive', async () => {
    await authenticated('tok', 'ref');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));

    await expect(apiClient.logout()).rejects.toThrow('Could not end session. Please try again.');
    // The session is NOT torn down locally, so the user can retry.
    expect(getAccessToken()).toBe('tok');
  });
});
