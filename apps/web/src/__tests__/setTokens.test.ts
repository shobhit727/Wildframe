/**
 * Regression: setTokens must AWAIT the /auth-session cookie write.
 *
 * A fire-and-forget POST raced router.push('/browse') — the browser aborted
 * the request before Set-Cookie committed, so the next hard navigation
 * bounced to /login (middleware saw no wf_refresh cookie).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fetchMock = vi.fn();

vi.mock('axios', () => {
  const post = vi.fn().mockResolvedValue({
    data: {
      access_token: 'access-123',
      refresh_token: 'refresh-456',
      token_type: 'bearer',
      expires_in: 900,
    },
  });
  const interceptors = {
    request: { use: vi.fn() },
    response: { use: vi.fn() },
  };
  const instance = { post, get: vi.fn(), interceptors };
  const axios = Object.assign(vi.fn(() => instance), {
    create: vi.fn(() => instance),
  });
  return { default: axios };
});

describe('setTokens awaits cookie persistence', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(async () => {
    const { clearTokens } = await import('@/api/client');
    clearTokens();
    vi.unstubAllGlobals();
  });

  it('login() resolves only after the cookie POST completes', async () => {
    let resolveCookie: (v?: unknown) => void = () => {};
    fetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCookie = () => resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
        })
    );

    const { apiClient, getAccessToken } = await import('@/api/client');
    const pending = apiClient.login('settokens-test@example.invalid', 'not-a-real-password');
    let completed = false;
    void pending.then(() => { completed = true; });

    // Give the microtask queue a tick: the cookie POST is in-flight.
    await Promise.resolve();
    expect(completed).toBe(false);
    expect(getAccessToken()).toBeNull();

    // Resolve the cookie write, then login() may settle.
    resolveCookie();
    await pending;

    expect(completed).toBe(true);
    expect(getAccessToken()).toBe('access-123');
  });

  it('does not authenticate when cookie persistence fails', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 500 }));
    const { apiClient, getAccessToken } = await import('@/api/client');
    await expect(apiClient.login('settokens-test@example.invalid', 'not-a-real-password')).rejects.toThrow();
    expect(getAccessToken()).toBeNull();
  });
});
