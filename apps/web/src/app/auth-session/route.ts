import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://localhost:8000';
import { buildRefreshCookieHeader, REFRESH_COOKIE_MAX_AGE, REFRESH_COOKIE_NAME as REFRESH_COOKIE } from '@/utils/authCookie';

const COOKIE_MAX_AGE = REFRESH_COOKIE_MAX_AGE;
const REFRESH_ENDPOINT = `${API_BASE_URL}/auth/api/v1/auth/refresh`;

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
  if (typeof token !== 'string' || !token || token.length > 512) {
    return NextResponse.json({ error: 'missing_refresh_token' }, { status: 400 });
  }

  const res = NextResponse.json({ ok: true });
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=${encodeURIComponent(token)}; ${buildRefreshCookieHeader({ maxAge: COOKIE_MAX_AGE })}`);
  return res;
}

/**
 * GET /auth-session
 * Use the HttpOnly refresh cookie to obtain a fresh access token from the auth service.
 * Called on page load (hydrate) and by the axios 401 interceptor.
 * Returns { access_token, refresh_token? } on success; 401 if no session or refresh failed.
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
      refresh_token: typeof data?.refresh_token === 'string' ? data.refresh_token : null,
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
 * Clear the HttpOnly refresh cookie (logout).
 */
export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=; ${buildRefreshCookieHeader({ maxAge: 0 })}`);
  return res;
}

/**
 * HTTPS fetch that trusts the project's self-signed dev certificate.
 *
 * Next's bundled fetch ignores NODE_EXTRA_CA_CERTS in its server worker, so
 * server-side calls to the TLS gateway fail verification. When the dev cert
 * exists we do a raw node:https request with an explicit CA; otherwise this
 * is a plain fetch (production, publicly-trusted certs).
 */
async function secureFetch(
  url: string,
  init: { method: string; headers: Record<string, string>; body: string },
): Promise<Response> {
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
          resolve(new Response(text, { status: res.statusCode ?? 502 }));
        });
      },
    );
    req.on('error', reject);
    req.write(init.body);
    req.end();
  });
}