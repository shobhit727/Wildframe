/**
 * Regression test: /auth-session must accept a real refresh token.
 *
 * ## What broke
 *
 * `POST /auth-session` guarded the refresh token with `token.length > 512`
 * and reported every rejection as `missing_refresh_token`. Real RS256
 * refresh tokens from auth-service are 745 characters, so the guard rejected
 * 100% of them:
 *
 *   1. `POST /auth/api/v1/auth/register` returns **201** with a valid pair.
 *   2. `setTokens` POSTs the refresh token to `/auth-session` -> **400**.
 *   3. `setTokens` throws, the signup page's catch shows
 *      "Could not create the account. Please try again.", and the user is
 *      stuck on /signup with a perfectly good account already created.
 *
 * ## What these tests assert
 *
 * Externally observable behavior of the route handler, at a realistic token
 * size (the token below is 745 chars, matching a live auth-service response).
 * The token is deliberately NOT shortened to fit the old limit — a test that
 * passes for the wrong reason is worse than no test.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';

import { REFRESH_COOKIE_NAME, REFRESH_COOKIE_MAX_AGE } from '@/utils/authCookie';

/** Exactly the length auth-service issues today (measured against the live stack). */
const REAL_REFRESH_TOKEN_LENGTH = 745;

/** A JWT-shaped token of the real size: base64url header.payload.signature. */
function realisticRefreshToken(length = REAL_REFRESH_TOKEN_LENGTH): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
  // header.payload.signature: the two dots are part of the total length.
  let body = '';
  for (let i = 0; i < length - 2; i += 1) body += alphabet[i % alphabet.length];
  return `${body.slice(0, length - 3)}.${body.slice(length - 3, length - 2)}.${body.slice(length - 2)}`;
}

function post(body: unknown): NextRequest {
  return new Request('https://localhost:3000/auth-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }) as unknown as NextRequest;
}

function setCookieHeader(res: Response): string {
  return res.headers.get('set-cookie') ?? '';
}

describe('POST /auth-session refresh token bounds', () => {
  beforeEach(() => {
    // secureFetch() only reaches the network on GET/DELETE; the POST path
    // never calls it, but the module import pulls in node:fs/node:https.
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('accepts a refresh token of the size auth-service actually issues', async () => {
    const { POST } = await import('@/app/auth-session/route');
    const token = realisticRefreshToken();
    expect(token).toHaveLength(REAL_REFRESH_TOKEN_LENGTH);

    const res = await POST(post({ refresh_token: token }));

    // THE FIX. Before it, this was 400 missing_refresh_token for every
    // real token, which is what broke registration end to end.
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ ok: true });

    const cookie = setCookieHeader(res);
    expect(cookie).toContain(`${REFRESH_COOKIE_NAME}=`);
    expect(cookie).toContain('HttpOnly');
    expect(cookie).toContain('Secure');
    expect(cookie).toContain(`Max-Age=${REFRESH_COOKIE_MAX_AGE}`);

    // The cookie must round-trip the token, not a truncated prefix of it.
    const value = decodeURIComponent(
      cookie.slice(cookie.indexOf('=') + 1, cookie.indexOf(';')),
    );
    expect(value).toBe(token);
  });

  it('leaves headroom above the real token size', async () => {
    // Guards against someone tightening the bound back to ~the token size.
    // The real token is 745 chars; the accepted bound must clear it with room
    // for a larger claim set, while still rejecting absurd input.
    const { POST } = await import('@/app/auth-session/route');
    const res = await POST(post({ refresh_token: realisticRefreshToken(2048) }));
    expect(res.status).toBe(200);
  });

  it('rejects an absent token as missing_refresh_token', async () => {
    const { POST } = await import('@/app/auth-session/route');

    for (const body of [{}, { refresh_token: '' }, { refresh_token: 123 }, { refresh_token: null }]) {
      const res = await POST(post(body));
      expect(res.status).toBe(400);
      await expect(res.json()).resolves.toEqual({ error: 'missing_refresh_token' });
    }
  });

  it('reports an oversized token distinctly instead of as a missing token', async () => {
    // An over-limit token was previously reported as `missing_refresh_token`,
    // which is factually wrong and sends the next debugger hunting for a
    // token that was present all along.
    const { POST } = await import('@/app/auth-session/route');
    const res = await POST(post({ refresh_token: realisticRefreshToken(100_000) }));

    expect(res.status).toBe(413);
    const body = (await res.json()) as { error?: string; max_length?: number };
    expect(body.error).toBe('refresh_token_too_large');
    expect(body.error).not.toBe('missing_refresh_token');
    expect(body.max_length).toBe(2048);
    expect(setCookieHeader(res)).toBe('');
  });

  it('rejects malformed JSON as invalid_json', async () => {
    const { POST } = await import('@/app/auth-session/route');
    const res = await POST(
      new Request('https://localhost:3000/auth-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{not json',
      }) as unknown as NextRequest,
    );
    expect(res.status).toBe(400);
    await expect(res.json()).resolves.toEqual({ error: 'invalid_json' });
  });
});
