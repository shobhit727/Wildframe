/**
 * Regression test: /auth-session must address auth-service over the internal
 * (server-side) route, not the public gateway.
 *
 * ## What broke
 *
 * The route handler resolved its upstream from
 * `process.env.NEXT_PUBLIC_API_URL || 'https://localhost:8000'`. That is a
 * *browser-facing* address. These handlers run inside the `web` container, where
 * `localhost:8000` is the web container itself and nothing listens on 8000, so
 * the connection was refused and GET/DELETE answered 502 `auth_unreachable`.
 *
 * The failure was invisible on the happy path and fatal on refresh:
 *
 *   1. `POST /auth/api/v1/auth/register` -> 201, refresh_token 745 chars.
 *   2. `POST /auth-session` -> 200, HttpOnly cookie set.
 *   3. Client-side navigation worked (it uses the browser client, not this).
 *   4. A hard reload of any protected route calls GET /auth-session -> 502,
 *      the client cleared the session and bounced the user to /login.
 *
 * So registration and login appeared to work while the session silently failed
 * to survive a refresh.
 *
 * ## Why a separate variable
 *
 * The browser must keep using the public HTTPS gateway derived from the page
 * host (src/api/client.ts). This handler must use the docker-network address.
 * One variable cannot serve both, which is exactly why the fix introduces a
 * server-only AUTH_SERVICE_URL rather than reusing the public one. The test
 * below pins that separation: with NEXT_PUBLIC_API_URL deliberately set to a
 * plausible-looking public value, the server-side call must still not use it.
 *
 * ## What these tests assert
 *
 * The upstream URL actually passed to fetch, plus the resulting status. That is
 * externally observable behavior — the same signal the 502 came from.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

import { REFRESH_COOKIE_NAME } from '@/utils/authCookie';

const { cookieStore, httpsRequest } = vi.hoisted(() => ({
  cookieStore: { get: vi.fn() },
  httpsRequest: vi.fn(),
}));

vi.mock('next/headers', () => ({
  cookies: async () => cookieStore,
}));

/**
 * Block the node:https fallback so the suite stays hermetic.
 *
 * secureFetch() only takes that path for an https:// URL *and* only when
 * certificates/localhost.pem exists — and it does exist on a developer machine
 * (apps/web/certificates is gitignored but present after
 * scripts/generate-dev-certs.sh). Without this mock, an http/https regression
 * silently escapes to the real network and the test result depends on whatever
 * is listening on the developer's port 8000. Mocking the module to throw makes
 * "the code took the TLS branch" an explicit, local failure.
 */
vi.mock('node:https', () => ({
  request: (...args: unknown[]) => httpsRequest(...args),
}));

/** The docker-network address auth-service actually listens on (compose). */
const INTERNAL_AUTH_URL = 'http://auth-service:8000';

const VALID_ACCESS_TOKEN = 'header.payload.signature';

/** A JWT-shaped refresh token; the handler does not inspect its contents. */
const REFRESH_TOKEN = 'r1.r2.r3';

function requestWith(method: string, headers: Record<string, string> = {}): NextRequest {
  return new Request('https://localhost:3000/auth-session', {
    method,
    headers: { 'Content-Type': 'application/json', ...headers },
  }) as unknown as NextRequest;
}

/**
 * Import the route with a clean module registry so its top-level
 * `AUTH_SERVICE_URL` read sees the env this test set.
 */
async function loadRoute() {
  vi.resetModules();
  return import('@/app/auth-session/route');
}

function upstreamUrls(): string[] {
  return vi.mocked(globalThis.fetch).mock.calls.map((call) => String(call[0]));
}

describe('/auth-session internal upstream addressing', () => {
  const savedEnv = { ...process.env };

  beforeEach(() => {
    cookieStore.get.mockReturnValue({ value: encodeURIComponent(REFRESH_TOKEN) });
    httpsRequest.mockReset();
    httpsRequest.mockImplementation(() => {
      throw new Error(
        'node:https was used for an internal http:// upstream. The internal address must not take the TLS branch.',
      );
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ access_token: VALID_ACCESS_TOKEN }), { status: 200 }),
      ),
    );
    // The fix reads a server-only variable. Delete every candidate so a stray
    // value in the ambient environment cannot make a test pass for the wrong
    // reason.
    delete process.env.AUTH_SERVICE_URL;
    delete process.env.NEXT_PUBLIC_API_URL;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    process.env = { ...savedEnv };
  });

  it('defaults to the internal docker-network address when no env is set', async () => {
    const { GET } = await loadRoute();

    const res = await GET();

    expect(res.status).toBe(200);
    expect(upstreamUrls()).toEqual([`${INTERNAL_AUTH_URL}/api/v1/auth/refresh`]);
  });

  it('does not use the public gateway address from inside the container', async () => {
    const { GET } = await loadRoute();

    await GET();

    // THE REGRESSION. Before the fix this was https://localhost:8000, which
    // inside the web container is a refused connection -> 502 auth_unreachable.
    // Assert a call was actually made first: an empty list would satisfy every
    // `not.toContain` below vacuously, which is how this test would have passed
    // for the wrong reason.
    const urls = upstreamUrls();
    expect(urls).toHaveLength(1);
    for (const url of urls) {
      expect(url).not.toContain('localhost:8000');
      expect(url).not.toContain('127.0.0.1');
    }
  });

  it('ignores NEXT_PUBLIC_API_URL even when it is set to a valid public value', async () => {
    // A plausible-looking public gateway address. The server must not adopt it:
    // that conflation is the bug.
    process.env.NEXT_PUBLIC_API_URL = 'https://api.example.com:8000';
    const { GET } = await loadRoute();

    const res = await GET();

    expect(res.status).toBe(200);
    const urls = upstreamUrls();
    expect(urls).toHaveLength(1);
    for (const url of urls) {
      expect(url).not.toContain('api.example.com');
    }
  });

  it('uses /api/v1/auth/* without the gateway routing prefix', async () => {
    const { GET } = await loadRoute();

    await GET();

    // The gateway strips its leading `/auth` segment before forwarding, so
    // asking auth-service for that segment would 404.
    expect(upstreamUrls()[0]).toBe(`${INTERNAL_AUTH_URL}/api/v1/auth/refresh`);
    expect(upstreamUrls()[0]).not.toContain('/auth/api/v1');
  });

  it('honors an explicit AUTH_SERVICE_URL override', async () => {
    process.env.AUTH_SERVICE_URL = 'http://127.0.0.1:9999/';
    const { GET } = await loadRoute();

    await GET();

    // Trailing slash tolerated rather than producing a doubled separator.
    expect(upstreamUrls()).toEqual(['http://127.0.0.1:9999/api/v1/auth/refresh']);
  });

  it('sends logout to the same internal base, not the public gateway', async () => {
    const { DELETE } = await loadRoute();

    const res = await DELETE(requestWith('DELETE', { Authorization: 'Bearer token' }));

    expect(res.status).toBe(200);
    // The handler revokes the cookie-held refresh token and the caller's access
    // token as two separate upstream calls; both must use the internal base.
    expect(upstreamUrls()).toEqual([
      `${INTERNAL_AUTH_URL}/api/v1/auth/logout`,
      `${INTERNAL_AUTH_URL}/api/v1/auth/logout`,
    ]);
  });

  it('never takes the node:https branch for the internal http upstream', async () => {
    const { GET } = await loadRoute();

    const res = await GET();

    expect(res.status).toBe(200);
    // node:https.request throws for an http:// URL ("Protocol \"http:\"
    // not supported"), so the internal address must go through plain fetch.
    expect(httpsRequest).not.toHaveBeenCalled();
  });

  it('still uses the CA-pinned TLS branch when pointed at an https upstream', async () => {
    // The host-side `next dev` case: certificates/localhost.pem is present, so
    // an https AUTH_SERVICE_URL must keep working via the node:https fallback.
    process.env.AUTH_SERVICE_URL = 'https://localhost:8000';
    const { GET } = await loadRoute();
    httpsRequest.mockImplementation((_url: string, _opts: unknown, cb: (res: unknown) => void) => {
      const payload = Buffer.from(JSON.stringify({ access_token: VALID_ACCESS_TOKEN }));
      const handlers: Record<string, ((chunk?: Buffer) => void)[]> = {};
      const res = {
        statusCode: 200,
        on(event: string, handler: (chunk?: Buffer) => void) {
          handlers[event] = handlers[event] ?? [];
          handlers[event].push(handler);
          return res;
        },
      };
      // Emit data-then-end on the next tick, in the order node would, so the
      // handler's chunk accumulation and final resolve both run.
      queueMicrotask(() => {
        cb(res);
        for (const handler of handlers.data ?? []) handler(payload);
        for (const handler of handlers.end ?? []) handler();
      });
      return {
        on() {
          return this;
        },
        write() {
          return true;
        },
        end() {},
      };
    });

    const res = await GET();

    expect(httpsRequest).toHaveBeenCalledTimes(1);
    expect(String(httpsRequest.mock.calls[0][0])).toBe(
      'https://localhost:8000/api/v1/auth/refresh',
    );
    expect(res.status).toBe(200);
  });

  it('returns 502 auth_unreachable only when the upstream actually fails', async () => {
    vi.mocked(globalThis.fetch).mockRejectedValue(new Error('ECONNREFUSED'));
    const { GET } = await loadRoute();

    const res = await GET();

    // The 502 is still correct for a genuinely unreachable service; the fix
    // is that the internal address now resolves. Guarding this keeps the
    // regression test from silently accepting a route that always 502s.
    expect(res.status).toBe(502);
    await expect(res.json()).resolves.toEqual({ error: 'auth_unreachable' });
  });

  it('keeps the session cookie name stable and clears it on failure', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValue(new Response('nope', { status: 401 }));
    const { GET } = await loadRoute();

    const res = await GET();

    expect(res.status).toBe(401);
    expect(res.headers.get('set-cookie')).toContain(`${REFRESH_COOKIE_NAME}=;`);
  });
});
