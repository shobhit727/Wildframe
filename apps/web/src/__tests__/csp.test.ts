import { describe, it, expect } from 'vitest';
import { buildCspHeader, generateNonce } from '@/utils/csp';

describe('CSP builder', () => {
  const apiUrl = 'https://api.example.com';

  it('generates a valid nonce', () => {
    const nonce = generateNonce();
    expect(nonce).toBeDefined();
    expect(typeof nonce).toBe('string');
    expect(nonce.length).toBeGreaterThan(0);
    // CSP nonce regex: /^'nonce-([A-Za-z0-9+/_-]+={0,2})'$/ — alphanumeric is valid
    // crypto.randomUUID().replace(/-/g, '') produces lowercase hex (a-f, 0-9)
    expect(nonce).toMatch(/^[a-z0-9+/_-]+={0,2}$/);
  });

  it('builds strict CSP for production (no unsafe-inline/eval in script-src)', () => {
    const nonce = 'testNonce123';
    const csp = buildCspHeader({ nonce, isDev: false, apiUrl });
    expect(csp).toContain(`script-src 'self' 'nonce-${nonce}'`);
    const scriptSrc = csp
      .split(';')
      .map((d) => d.trim())
      .find((d) => d.startsWith('script-src'));
    expect(scriptSrc).toBeDefined();
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(scriptSrc).not.toContain("'unsafe-eval'");
    expect(csp).toContain("upgrade-insecure-requests");
    expect(csp).toContain(`connect-src 'self' ${apiUrl} https:`);
  });

  it('builds permissive CSP for development (allows unsafe-inline/eval for HMR)', () => {
    const nonce = 'testNonce123';
    const csp = buildCspHeader({ nonce, isDev: true, apiUrl });
    expect(csp).toContain(`script-src 'self' 'nonce-${nonce}' 'unsafe-inline' 'unsafe-eval'`);
    expect(csp).not.toContain("upgrade-insecure-requests");
  });

  it('includes required directives', () => {
    const nonce = 'testNonce123';
    const csp = buildCspHeader({ nonce, isDev: false, apiUrl });
    const directives = csp.split(';').map((d) => d.trim());
    expect(directives).toContain("default-src 'self'");
    expect(directives).toContain("style-src 'self' 'unsafe-inline'");
    expect(directives).toContain("img-src 'self' blob: data: https:");
    expect(directives).toContain("media-src 'self' blob: https:");
    expect(directives).toContain("font-src 'self' data:");
    expect(directives).toContain("object-src 'none'");
    expect(directives).toContain("base-uri 'self'");
    expect(directives).toContain("form-action 'self'");
    expect(directives).toContain("frame-ancestors 'none'");
    expect(directives).toContain("worker-src 'self' blob:");
  });
});
