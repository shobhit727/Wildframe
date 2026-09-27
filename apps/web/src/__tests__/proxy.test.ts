import { describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

import proxy from '@/proxy';

// Keep CSP generation out of route-boundary assertions; these tests only need
// to observe whether the proxy redirects or forwards the request.
vi.mock('@/utils/csp', () => ({
  buildCspHeader: () => 'default-src \'self\'',
  generateNonce: () => 'test-nonce',
}));

function requestFor(pathname: string, hasSession = false): NextRequest {
  const headers = hasSession ? { cookie: '__Host-wf_refresh=test-refresh-token' } : undefined;
  return new NextRequest(new URL(pathname, 'https://wildframe.test'), { headers });
}

function expectRedirect(pathname: string): void {
  const response = proxy(requestFor(pathname));
  expect(response.status).toBe(307);
  expect(response.headers.get('location')).toBe('https://wildframe.test/login');
}

function expectPassThrough(pathname: string): void {
  const response = proxy(requestFor(pathname));
  expect(response.status).toBe(200);
}

describe('proxy protected route boundaries', () => {
  it.each(['/browse', '/watch', '/my-list', '/account', '/billing', '/creator'])(
    'redirects anonymous requests to the protected route %s',
    (pathname) => {
      expectRedirect(pathname);
    },
  );

  it.each(['/browsex', '/watchlist', '/accounting', '/creatorx'])(
    'does not treat the sibling path %s as protected',
    (pathname) => {
      expectPassThrough(pathname);
    },
  );

  it('protects nested paths under each protected segment', () => {
    expectRedirect('/browse/movies');
    expectRedirect('/watch/title-1');
    expectRedirect('/creator/dashboard');
  });

  it('preserves the refresh-cookie authentication hint', () => {
    const response = proxy(requestFor('/creator', true));
    expect(response.status).toBe(200);
  });

  it('leaves the existing admin proxy boundary unchanged', () => {
    // Admin authorization remains owned by AdminGate; this route is not newly
    // added to the middleware protection list by the #913/#950 fix.
    expectPassThrough('/admin');
  });

  it('does not confuse the root route with a protected segment', () => {
    expectPassThrough('/');
  });
});
