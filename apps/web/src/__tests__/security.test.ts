import { describe, it, expect } from 'vitest';

describe('Security config', () => {
  // Test next.config redirects: only /home -> / (static, same-origin).
  // No open redirects (no user-controlled destination, no http:// or //).
  it('has only static same-origin redirects', async () => {
    const { default: nextConfig } = await import('../../next.config.ts');
    const redirects = await nextConfig.redirects?.();
    expect(redirects).toBeDefined();
    if (!redirects) return;

    for (const redirect of redirects) {
      // Source and destination should be strings
      expect(typeof redirect.source).toBe('string');
      expect(typeof redirect.destination).toBe('string');
      // Destination must be relative (no protocol, no //)
      expect(redirect.destination).not.toMatch(/^https?:\/\//);
      expect(redirect.destination).not.toMatch(/^\/\//);
      // Permanent is boolean
      expect(typeof redirect.permanent).toBe('boolean');
    }
    // Known redirect: /home -> /
    expect(redirects).toContainEqual(
      expect.objectContaining({
        source: '/home',
        destination: '/',
        permanent: true,
      })
    );
    // Only one redirect expected
    expect(redirects.length).toBe(1);
  });

  // CSP is now handled by middleware; next.config should NOT have CSP header.
  it('does not set CSP in next.config headers', async () => {
    const { default: nextConfig } = await import('../../next.config.ts');
    const headers = await nextConfig.headers?.();
    expect(headers).toBeDefined();
    if (!headers) return;

    for (const headerGroup of headers) {
      for (const header of headerGroup.headers) {
        expect(header.key).not.toBe('Content-Security-Policy');
      }
    }
  });

  // Referrer-Policy should not be duplicated
  it('has no duplicate Referrer-Policy header', async () => {
    const { default: nextConfig } = await import('../../next.config.ts');
    const headers = await nextConfig.headers?.();
    expect(headers).toBeDefined();
    if (!headers) return;

    let count = 0;
    for (const headerGroup of headers) {
      for (const header of headerGroup.headers) {
        if (header.key === 'Referrer-Policy') count++;
      }
    }
    expect(count).toBe(1);
  });

  // Required security headers present
  it('has required security headers', async () => {
    const { default: nextConfig } = await import('../../next.config.ts');
    const headers = await nextConfig.headers?.();
    expect(headers).toBeDefined();
    if (!headers) return;

    const keys = new Set<string>();
    for (const headerGroup of headers) {
      for (const header of headerGroup.headers) {
        keys.add(header.key);
      }
    }
    expect(keys).toContain('X-Frame-Options');
    expect(keys).toContain('X-Content-Type-Options');
    expect(keys).toContain('X-XSS-Protection');
    expect(keys).toContain('Referrer-Policy');
    expect(keys).toContain('Permissions-Policy');
  });
});