import { describe, it, expect } from 'vitest';
import {
  buildRefreshCookieHeader,
  REFRESH_COOKIE_NAME,
  REFRESH_COOKIE_MAX_AGE,
} from '@/utils/authCookie';

describe('Auth cookie builder', () => {
  it('exports constants', () => {
    expect(REFRESH_COOKIE_NAME).toBe('__Host-wf_refresh');
    expect(REFRESH_COOKIE_MAX_AGE).toBe(60 * 60 * 24 * 30); // 30 days
  });

  it('builds cookie header with all required attributes', () => {
    const header = buildRefreshCookieHeader({ maxAge: REFRESH_COOKIE_MAX_AGE });
    const parts = header.split(';').map((p) => p.trim());
    expect(parts).toContain('Path=/');
    expect(parts).toContain(`Max-Age=${REFRESH_COOKIE_MAX_AGE}`);
    expect(parts).toContain('HttpOnly');
    expect(parts).toContain('Secure');
    expect(parts).toContain('SameSite=Strict');
  });

  it('always includes Secure (dev uses HTTPS)', () => {
    const header = buildRefreshCookieHeader({ maxAge: 3600 });
    expect(header).toContain('Secure');
  });

  it('uses SameSite=Strict by default', () => {
    const header = buildRefreshCookieHeader({ maxAge: 3600 });
    expect(header).toContain('SameSite=Strict');
  });

  it('allows SameSite=Lax override', () => {
    const header = buildRefreshCookieHeader({ maxAge: 3600, sameSite: 'Lax' });
    expect(header).toContain('SameSite=Lax');
  });

  it('sets Max-Age=0 for clearing cookie', () => {
    const header = buildRefreshCookieHeader({ maxAge: 0 });
    expect(header).toContain('Max-Age=0');
  });
});