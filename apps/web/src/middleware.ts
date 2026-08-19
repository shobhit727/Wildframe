import { NextRequest, NextResponse } from 'next/server';
import { buildCspHeader, generateNonce } from '@/utils/csp';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://localhost:8000';
const isDev = process.env.NODE_ENV === 'development';

export function middleware(request: NextRequest) {
  // The auth session is tracked via the HttpOnly 'wf_refresh' cookie set
  // by /auth-session. Presence of this cookie indicates a valid session.
  const token = request.cookies.get('wf_refresh')?.value;
  const { pathname } = request.nextUrl;

  // Generate nonce and CSP for this request
  const nonce = generateNonce();
  const csp = buildCspHeader({ nonce, isDev, apiUrl: API_BASE_URL });

  // Prepare request headers with CSP so Next's renderer extracts the nonce
  // for its inline scripts (flight data bootstrap).
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('content-security-policy', csp);

  // Protect dashboard routes
  const protectedRoutes = ['/browse', '/watch', '/my-list', '/account', '/billing'];
  const isProtectedRoute = protectedRoutes.some((route) => pathname.startsWith(route));

  if (isProtectedRoute && !token) {
    const response = NextResponse.redirect(new URL('/login', request.url));
    // Apply CSP and no-store to redirect response as well
    response.headers.set('Content-Security-Policy', csp);
    if (token) response.headers.set('Cache-Control', 'no-store');
    return response;
  }

  // Redirect authenticated users from auth pages
  const authRoutes = ['/login', '/signup'];
  const isAuthRoute = authRoutes.some((route) => pathname === route);

  if (isAuthRoute && token) {
    const response = NextResponse.redirect(new URL('/browse', request.url));
    response.headers.set('Content-Security-Policy', csp);
    response.headers.set('Cache-Control', 'no-store');
    return response;
  }

  // Continue with modified request headers and CSP on response
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set('Content-Security-Policy', csp);

  // Cache-Control: no-store for authenticated responses
  if (token) {
    response.headers.set('Cache-Control', 'no-store');
  }

  return response;
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};