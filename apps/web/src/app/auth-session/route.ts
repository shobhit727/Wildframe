import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const REFRESH_COOKIE = 'wf_refresh';
const REFRESH_ENDPOINT = `${API_BASE_URL}/auth/api/v1/auth/refresh`;
const COOKIE_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

function cookieHeader(maxAge: number): string {
  const isProd = process.env.NODE_ENV === 'production';
  const attrs = [
    'Path=/',
    `Max-Age=${maxAge}`,
    'HttpOnly',
    'SameSite=Strict',
  ];
  if (isProd) attrs.push('Secure');
  return attrs.join('; ');
}

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
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=${encodeURIComponent(token)}; ${cookieHeader(COOKIE_MAX_AGE)}`);
  return res;
}

/**
 * GET /auth-session
 * Use the HttpOnly refresh cookie to obtain a fresh access token from the auth service.
 * Called on page load (hydrate) and by the axios 401 interceptor.
 * Returns { access_token, refresh_token? } on success; 401 if no session or refresh failed.
 */
export async function GET() {
  const cookieStore = await cookies();
  const raw = cookieStore.get(REFRESH_COOKIE)?.value;
  if (!raw) {
    return NextResponse.json({ error: 'no_session' }, { status: 401 });
  }

  let refreshToken: string;
  try {
    refreshToken = decodeURIComponent(raw);
  } catch {
    return NextResponse.json({ error: 'invalid_cookie' }, { status: 401 });
  }

  try {
    const response = await fetch(REFRESH_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: 'no-store',
    });

    if (!response.ok) {
      // Clear stale cookie
      const res = NextResponse.json({ error: 'refresh_failed' }, { status: 401 });
      res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=; ${cookieHeader(0)}`);
      return res;
    }

    const data = (await response.json()) as {
      access_token?: unknown;
      refresh_token?: unknown;
    };

    const accessToken = data?.access_token;
    if (typeof accessToken !== 'string' || !accessToken) {
      return NextResponse.json({ error: 'invalid_refresh_response' }, { status: 502 });
    }

    const res = NextResponse.json({
      access_token: accessToken,
      refresh_token: typeof data?.refresh_token === 'string' ? data.refresh_token : null,
    });

    // Rotate refresh token if the backend issued a new one
    if (typeof data?.refresh_token === 'string' && data.refresh_token) {
      res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=${encodeURIComponent(data.refresh_token)}; ${cookieHeader(COOKIE_MAX_AGE)}`);
    }
    return res;
  } catch {
    // Auth service unreachable
    return NextResponse.json({ error: 'auth_unreachable' }, { status: 502 });
  }
}

/**
 * DELETE /auth-session
 * Clear the HttpOnly refresh cookie (logout).
 */
export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.headers.set('Set-Cookie', `${REFRESH_COOKIE}=; ${cookieHeader(0)}`);
  return res;
}