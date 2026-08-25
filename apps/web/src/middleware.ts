import { NextRequest, NextResponse } from 'next/server';
import { buildCspHeader, generateNonce } from '@/utils/csp';

export function middleware(request: NextRequest) {
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


  // The auth session is tracked via the HttpOnly 'wf_refresh' cookie set
  // by /auth-session. Presence of this cookie indicates a valid session.
  const token = request.cookies.get('wf_refresh')?.value;
  const { pathname } = request.nextUrl;

  // Protect dashboard routes
  const protectedRoutes = ['/browse', '/watch', '/my-list', '/account', '/billing'];
  const isProtectedRoute = protectedRoutes.some((route) => pathname.startsWith(route));

  if (isProtectedRoute && !token) {
    return withCsp(NextResponse.redirect(new URL('/login', request.url)), csp);
  }

  // Redirect authenticated users from auth pages
  const authRoutes = ['/login', '/signup'];
  const isAuthRoute = authRoutes.some((route) => pathname === route);

  if (isAuthRoute && token) {
    return withCsp(NextResponse.redirect(new URL('/browse', request.url)), csp);
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