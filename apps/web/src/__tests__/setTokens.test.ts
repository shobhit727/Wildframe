/**
 * Regression: setTokens must AWAIT the /auth-session cookie write.
 *
 * A fire-and-forget POST raced router.push('/browse') — the browser aborted
 * the request before Set-Cookie committed, so the next hard navigation
 * bounced to /login (middleware saw no wf_refresh cookie).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

  it('login() resolves only after the cookie POST completes', async () => {
    let resolveCookie: (v?: unknown) => void = () => {};
    fetchMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveCookie = () => resolve(new Response(JSON.stringify({ ok: true }), { status: 200 }));
        })
    );

    const { apiClient } = await import('@/api/client');
    const pending = apiClient.login('demo@wildframe.com', 'DemoPass123!');

    // Give the microtask queue a tick: the cookie POST is in-flight.
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledWith(
      '/auth-session',
      expect.objectContaining({ method: 'POST' })
    );

    // Resolve the cookie write, then login() may settle.
    resolveCookie();
    await pending;

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body ?? '{}'));
    expect(body.refresh_token).toBe('refresh-456');
  });
});
