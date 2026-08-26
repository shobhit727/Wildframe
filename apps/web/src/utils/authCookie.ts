/**
 * Auth cookie header builder for the HttpOnly refresh token cookie.
 * Always Secure (dev uses HTTPS via --experimental-https), HttpOnly, SameSite=Strict.
 */
export interface RefreshCookieOptions {
  maxAge: number;
  sameSite?: 'Strict' | 'Lax'; // default Strict
}

/**
 * Build Set-Cookie header value for the refresh token cookie.
 * Attributes: Path=/, Max-Age, HttpOnly, Secure, SameSite=Strict
 */
export function buildRefreshCookieHeader(opts: RefreshCookieOptions): string {
  const { maxAge, sameSite = 'Strict' } = opts;
  const attrs = [
    'Path=/',
    `Max-Age=${maxAge}`,
    'HttpOnly',
    'Secure',
    `SameSite=${sameSite}`,
  ];
  return attrs.join('; ');
}

/** Cookie name for the refresh token. */
// __Host- prefix: browser enforces Secure + Path=/ + no Domain — the
// strongest cookie scoping available (audit #525).
export const REFRESH_COOKIE_NAME = '__Host-wf_refresh';

/** Default max age: 30 days. */
export const REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30;