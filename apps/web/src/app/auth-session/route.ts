import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

import { buildRefreshCookieHeader, REFRESH_COOKIE_MAX_AGE, REFRESH_COOKIE_NAME as REFRESH_COOKIE } from '@/utils/authCookie';

/**
 * Base URL of auth-service for calls made from inside this container.
 *
 * This is deliberately a *server-only* setting, and it is deliberately not
 * NEXT_PUBLIC_API_URL. Two different addresses serve two different callers and
 * one variable cannot correctly serve both:
 *
 *   - Browser code must reach the public TLS gateway, derived at runtime from
 *     the host the page was served on (see src/api/client.ts). Caddy only
 *     allowlists the browser origin there, and NEXT_PUBLIC_* is inlined into the
 *     client bundle, so anything set here is visible to the browser anyway.
 *   - This route handler runs server-side, inside the `web` container, where
 *     `localhost:8000` is the web container itself and nothing listens. The
 *     docker-network address is http://auth-service:8000.
 *
 * Keying the server-side fallback on the public variable is what produced the
 * 502 `auth_unreachable` on GET/DELETE /auth-session: with NEXT_PUBLIC_API_URL
 * unset it fell back to https://localhost:8000, which inside the container is a
 * connection refusal. A successful registration set the cookie, so client-side
 * navigation worked, but every hard reload of a protected route bounced the user
 * to /login.
 *
 * Targets auth-service directly rather than the gateway, matching how every
 * other service in this repo makes internal calls (see AUTH_SERVICE_URL and
 * JWT_JWKS_URL in deployments/docker-compose.dev.yml). Routing through the
 * gateway would also put every server-side refresh into one shared, IP-keyed
 * /auth/* rate-limit budget, since these calls carry no access token and the
 * limiter falls back to client IP — every user's refreshes would draw on the
 * same bucket from the single web container address.
 *
 * The gateway's `/auth` path prefix is dropped here because that segment is
 * routing metadata the gateway strips before forwarding; auth-service serves
 * these routes itself at /api/v1/auth/*.
 */
const AUTH_SERVICE_URL = (process.env.AUTH_SERVICE_URL || 'http://auth-service:8000').replace(/\/+$/, '');

const COOKIE_MAX_AGE = REFRESH_COOKIE_MAX_AGE;
const REFRESH_ENDPOINT = `${AUTH_SERVICE_URL}/api/v1/auth/refresh`;
const LOGOUT_ENDPOINT = `${AUTH_SERVICE_URL}/api/v1/auth/logout`;

/**
 * Upper bound on the accepted refresh token, in characters.
 *
 * This exists to reject absurd input, not to encode a real token size: JWT
 * length is a function of the claim set and the signing algorithm, so any
 * tight bound silently breaks authentication the day auth-service adds a
 * claim or changes algorithm. A previous 512-char cap rejected every real
 * RS256 refresh token auth-service issues (745 chars today), turning a
 * successful 201 registration into "Could not create the account".
 *
 * 2048 gives ~2.7x headroom over the current token and still keeps the
 * Set-Cookie comfortably inside the 4096-byte per-cookie ceiling browsers
 * enforce (JWT base64url characters are never percent-expanded, so the
 * value's encoded length equals its character length). An absent token and
 * an oversized one are reported separately — conflating them sends the next
 * debugger looking for a missing token that was present all along.
 */
const MAX_REFRESH_TOKEN_LENGTH = 2048;

/**
 * POST /auth-session
 * Persist a refresh token as an HttpOnly cookie.
 * Called by the client after a successful login/register/MFA verify.
 */
export async function POST(request: NextRequest) {
  let body: { refresh_token?: unknown } | null = null;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const token = body?.refresh_token;
  if (typeof token !== 'string' || !token) {
    return NextResponse.json({ error: 'missing_refresh_token' }, { status: 400 });
  }
  if (token.length > MAX_REFRESH_TOKEN_LENGTH) {
    return NextResponse.json(
      {
        error: 'refresh_token_too_large',
        max_length: MAX_REFRESH_TOKEN_LENGTH,
      },
      { status: 413 },
    );
  }

  const res = NextResponse.json({ ok: true });
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=${encodeURIComponent(token)}; ${buildRefreshCookieHeader({ maxAge: COOKIE_MAX_AGE })}`);
  return res;
}

/**
 * GET /auth-session
 * Use the HttpOnly refresh cookie to obtain a fresh access token from the auth service.
 * Called on page load (hydrate) and by the axios 401 interceptor.
 * Returns { access_token } on success; 401 if no session or refresh failed.
 *
 * The backend rotates refresh tokens on every use (single-use). Concurrent
 * GETs carrying the same cookie would otherwise burn the token twice — the
 * second call fails and clears the session. Single-flight per cookie value
 * collapses concurrent calls onto one upstream refresh.
 */
const inflightRefreshes = new Map<string, Promise<{ status: number; body: unknown; setCookie: string | null }>>();

export async function GET() {
  const cookieStore = await cookies();
  const raw = cookieStore.get(REFRESH_COOKIE)?.value;
  if (!raw) {
    return NextResponse.json({ error: 'no_session' }, { status: 401 });
  }

  const existing = inflightRefreshes.get(raw);
  if (existing) {
    const result = await existing;
    return buildRefreshResponse(result);
  }

  const task = doRefresh(raw).finally(() => inflightRefreshes.delete(raw));
  inflightRefreshes.set(raw, task);
  return buildRefreshResponse(await task);
}

function buildRefreshResponse(result: { status: number; body: unknown; setCookie: string | null }) {
  const res = NextResponse.json(result.body as Record<string, unknown>, { status: result.status });
  res.headers.set('Cache-Control', 'no-store');
  if (result.setCookie) res.headers.set('Set-Cookie', result.setCookie);
  return res;
}

async function doRefresh(raw: string): Promise<{ status: number; body: unknown; setCookie: string | null }> {
  let refreshToken: string;
  try {
    refreshToken = decodeURIComponent(raw);
  } catch {
    return { status: 401, body: { error: 'invalid_cookie' }, setCookie: null };
  }

  try {
    const response = await secureFetch(REFRESH_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!response.ok) {
      // Clear stale cookie
      return {
        status: 401,
        body: { error: 'refresh_failed' },
        setCookie: `${REFRESH_COOKIE}=; ${buildRefreshCookieHeader({ maxAge: 0 })}`,
      };
    }

    const data = (await response.json()) as {
      access_token?: unknown;
      refresh_token?: unknown;
    };

    const accessToken = data?.access_token;
    if (typeof accessToken !== 'string' || !accessToken) {
      return { status: 502, body: { error: 'invalid_refresh_response' }, setCookie: null };
    }

    const body: Record<string, unknown> = {
      access_token: accessToken,
    };

    // Rotate refresh token if the backend issued a new one
    let setCookie: string | null = null;
    if (typeof data?.refresh_token === 'string' && data.refresh_token) {
      setCookie = `${REFRESH_COOKIE}=${encodeURIComponent(data.refresh_token)}; ${buildRefreshCookieHeader({ maxAge: COOKIE_MAX_AGE })}`;
    }
    return { status: 200, body, setCookie };
  } catch {
    // Auth service unreachable
    return { status: 502, body: { error: 'auth_unreachable' }, setCookie: null };
  }
}

/**
 * DELETE /auth-session
 * Revoke the cookie-held refresh token and access token before clearing the cookie.
 */
export async function DELETE(request: NextRequest) {
  const raw = (await cookies()).get(REFRESH_COOKIE)?.value;
  const authorization = request.headers.get('Authorization');
  try {
    if (raw) {
      const response = await secureFetch(LOGOUT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: decodeURIComponent(raw) }),
      });
      if (!response.ok && response.status !== 401) {
        return NextResponse.json({ error: 'logout_failed' }, { status: 502 });
      }
    }
    if (authorization) {
      const response = await secureFetch(LOGOUT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: authorization },
        body: 'null',
      });
      if (!response.ok && response.status !== 401) {
        return NextResponse.json({ error: 'logout_failed' }, { status: 502 });
      }
    }
  } catch {
    return NextResponse.json({ error: 'auth_unreachable' }, { status: 502 });
  }
  const res = NextResponse.json({ ok: true });
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=; ${buildRefreshCookieHeader({ maxAge: 0 })}`);
  return res;
}

/**
 * Fetch that tolerates the project's self-signed dev certificate.
 *
 * The internal default (http://auth-service:8000) is plain HTTP on the docker
 * network, so it needs no CA at all. A self-signed cert only ever comes into
 * play when AUTH_SERVICE_URL is overridden to an https:// address, which is the
 * host-side `next dev` case against the TLS gateway. Next's bundled fetch
 * ignores NODE_EXTRA_CA_CERTS in its server worker, so that case does a raw
 * node:https request with an explicit CA; otherwise this is a plain fetch
 * (production, publicly-trusted certs).
 *
 * The scheme check is load-bearing rather than defensive: node:https throws
 * `Protocol "http:" not supported. Expected "https:"` for an http:// URL, so
 * branching on the mere presence of the dev cert would break the internal path
 * for anyone running with certificates mounted.
 */
async function secureFetch(
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
): Promise<Response> {
  if (!url.startsWith('https://')) {
    return fetch(url, { ...init, cache: 'no-store' });
  }
  const fs = await import('node:fs');
  const path = await import('node:path');
  const certPath = path.join(process.cwd(), 'certificates', 'localhost.pem');
  if (!fs.existsSync(certPath)) {
    return fetch(url, { ...init, cache: 'no-store' });
  }
  const https = await import('node:https');
  const ca = fs.readFileSync(certPath, 'utf8');
  return new Promise((resolve, reject) => {
    const req = https.request(
      url,
      {
        method: init.method,
        headers: { ...init.headers, 'Content-Length': String(Buffer.byteLength(init.body)) },
        ca,
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (c: Buffer) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          const status = res.statusCode ?? 502;
          resolve(new Response(status === 204 || status === 205 || status === 304 ? null : text, { status }));
        });
      },
    );
    req.on('error', reject);
    req.write(init.body);
    req.end();
  });
}