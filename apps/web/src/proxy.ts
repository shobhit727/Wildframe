import { NextRequest, NextResponse } from 'next/server';
import { buildCspHeader, generateNonce } from '@/utils/csp';

export default function proxy(request: NextRequest) {
  // Runtime CSP enforcement (web audit finding #2): the strict policy builder
  // existed but was never attached. Set it on every response; in production
  // use a per-request nonce that Next.js applies to its own bootstrap scripts
  // (it reads the CSP request header), in dev allow HMR's inline/eval needs.
  const isDev = process.env.NODE_ENV !== 'production';
  const nonce = generateNonce();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'https://localhost:8000';
  const csp = buildCspHeader({ nonce, isDev, apiUrl });

  const requestHeaders = new Headers(request.headers);
  if (!isDev) {
    // Next.js reads this request header and nonces its inline bootstrap.
    requestHeaders.set('Content-Security-Policy', csp);
  }


  // The auth session is tracked via the HttpOnly '__Host-wf_refresh' cookie set
  // by /auth-session. Presence is only an optimistic route hint, not authentication.
  const token = request.cookies.get('__Host-wf_refresh')?.value;
  const { pathname } = request.nextUrl;

  // Protect dashboard routes
  const protectedRoutes = ['/browse', '/watch', '/my-list', '/account', '/billing', '/creator'];
  // Match complete path segments so unrelated siblings such as /browsex do not inherit auth rules.
  const isProtectedRoute = protectedRoutes.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );

  // A page route has no handler for POST, PUT or DELETE, but Next serves the
  // page for any verb, so a write against /login returns the page with a
  // success status and a client that checks res.ok records a false success.
  // Reject them here, before the page route, and answer OPTIONS (a CORS
  // preflight) with 204 and an Allow header instead of the redirect it got
  // before, which made cross-origin preflight fail on every route.
  if (request.method === 'OPTIONS') {
    const res = new NextResponse(null, { status: 204 });
    res.headers.set('Allow', 'GET, HEAD, OPTIONS');
    return withCsp(res, csp);
  }
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    const res = NextResponse.json(
      { error: { code: 'METHOD_NOT_ALLOWED', message: `${request.method} is not supported on this route` } },
      { status: 405 },
    );
    res.headers.set('Allow', 'GET, HEAD, OPTIONS');
    return withCsp(res, csp);
  }

  if (isProtectedRoute && !token) {
    return withCsp(NextResponse.redirect(new URL('/login', request.url)), csp);
  }


  const res = NextResponse.next({ request: { headers: requestHeaders } });
  return withCsp(res, csp);
}


function withCsp(res: NextResponse, csp: string): NextResponse {
  res.headers.set('Content-Security-Policy', csp);
  return res;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};