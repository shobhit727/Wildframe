import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  // The auth session is tracked via the HttpOnly 'wf_refresh' cookie set
  // by /auth-session. Presence of this cookie indicates a valid session.
  const token = request.cookies.get('wf_refresh')?.value;
  const { pathname } = request.nextUrl;

  // Protect dashboard routes
  const protectedRoutes = ['/browse', '/watch', '/my-list', '/account', '/billing'];
  const isProtectedRoute = protectedRoutes.some((route) => pathname.startsWith(route));

  if (isProtectedRoute && !token) {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  // Redirect authenticated users from auth pages
  const authRoutes = ['/login', '/signup'];
  const isAuthRoute = authRoutes.some((route) => pathname === route);

  if (isAuthRoute && token) {
    return NextResponse.redirect(new URL('/browse', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};