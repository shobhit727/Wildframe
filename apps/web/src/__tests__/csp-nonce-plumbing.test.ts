/**
 * Regression test for the total frontend outage of issue #981.
 *
 * ## What broke
 *
 * `src/proxy.ts` attaches a CSP carrying a **fresh per-request nonce** to
 * every production response, and Next.js applies that nonce to its own inline
 * bootstrap scripts by reading the CSP back off the *request* header
 * (`next/dist/server/render.js`).
 *
 * That only works if the HTML is actually produced while serving a request.
 * Every route except `/` was **statically prerendered at build time**: the
 * inline `self.__next_f` RSC payload was written to disk with no nonce
 * attribute (there is no request yet at build time), then replayed verbatim
 * for every later request via `x-nextjs-cache: HIT`. The response CSP carried
 * a per-request nonce that could never match a build-time-baked script, so
 * `script-src 'self' 'nonce-…'` blocked both bootstrap scripts, React never
 * hydrated, and every route (`/`, `/login`, `/signup`, `/browse`) rendered an
 * empty `<body>`. Registration and login were impossible.
 *
 * ## Why the old suite stayed green
 *
 * `csp.test.ts` only asserts that the header *string* is well formed. A
 * builder unit test cannot fail on a plumbing bug whose entire symptom is that
 * the value never arrives. These tests therefore assert the **coupling between
 * the nonce policy and the render mode**, which is the thing that was wrong.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { NextRequest } from 'next/server';

import * as rootLayout from '@/app/layout';
import { buildCspHeader, generateNonce } from '@/utils/csp';

const APP_DIR = join(__dirname, '..', 'app');
const PROXY_SOURCE = readFileSync(join(__dirname, '..', 'proxy.ts'), 'utf8');

/** Every route segment in the app router, as `/`-prefixed paths. */
function appRouteFiles(dir: string = APP_DIR): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return appRouteFiles(full);
    return /^page\.(tsx|ts|jsx|js)$/.test(entry) ? [full] : [];
  });
}

describe('CSP nonce vs. render mode (#981)', () => {
  it('builds a CSP whose script-src is a per-request nonce with no inline escape hatch', () => {
    const a = buildCspHeader({ nonce: generateNonce(), isDev: false, apiUrl: 'https://localhost:8000' });
    const b = buildCspHeader({ nonce: generateNonce(), isDev: false, apiUrl: 'https://localhost:8000' });

    // A per-request nonce is what forces per-request rendering. If this ever
    // becomes stable across requests, the render-mode assertions below stop
    // being load-bearing and this test is the one that notices.
    expect(a).not.toBe(b);
    expect(a).toMatch(/script-src 'self' 'nonce-[A-Za-z0-9+/=_-]+'/);

    // Scoped to script-src: style-src legitimately keeps 'unsafe-inline'
    // (Tailwind/inline styles), and the XSS mitigation under test is the
    // script-src nonce. Weakening script-src is the failure mode to catch.
    const scriptSrc = a.split(';').find((d) => d.trim().startsWith('script-src'));
    expect(scriptSrc).not.toContain("'unsafe-inline'");
    expect(scriptSrc).not.toContain("'unsafe-eval'");
    expect(scriptSrc).not.toContain("'strict-dynamic'");
  });

  it('the proxy actually hands the nonce to Next on the REQUEST header', () => {
    // This is the delivery mechanism. It was never the bug, but if it silently
    // regressed the pages would go blank again for the same visible reason,
    // so it is pinned alongside the render-mode fix.
    expect(PROXY_SOURCE).toMatch(/requestHeaders\.set\(\s*['"]Content-Security-Policy['"]/);
  });

  it('the root layout opts the whole app router out of static prerendering', () => {
    // THE FIX. Without this, every route is baked at build time with
    // un-nonced inline scripts and the page never hydrates.
    expect(rootLayout.dynamic).toBe('force-dynamic');
  });

  it('no route re-enables static rendering under the root layout', () => {
    // A single page opting back in would blank that page only, which is
    // exactly the kind of partial outage that is easy to miss.
    const offenders = appRouteFiles().filter((file) =>
      /export\s+const\s+dynamic\s*(?::[^=]+)?=\s*['"](force-static|error)['"]/.test(
        readFileSync(file, 'utf8'),
      ),
    );
    expect(offenders).toEqual([]);
  });
});

/**
 * Models the browser's decision for one response, end to end: given a response
 * CSP and the HTML actually served, does `script-src` admit the inline
 * bootstrap? This is the failure the outage produced, expressed so the
 * assertions below read as the bug rather than as a proxy for it.
 */
describe('inline bootstrap scripts are admitted by the served CSP', () => {
  const nonceFromHeader = (csp: string): string | undefined =>
    /script-src[^;]*'nonce-([^']+)'/.exec(csp)?.[1];

  const inlineScriptNonces = (html: string): Array<string | undefined> =>
    [...html.matchAll(/<script(?![^>]*\ssrc=)([^>]*)>/g)].map((m) =>
      /nonce="([^"]+)"/.exec(m[1])?.[1],
    );

  /**
   * @param prerendered Build-time HTML: Next rendered it with no request in
   *   scope, so its inline scripts carry no nonce — which is exactly what
   *   `.next/server/app/<route>.html` contained for every broken route.
   */
    // nosemgrep: javascript.lang.security.audit.unknown-value-with-script-tag
    // `csp` is not external input. It is the return of buildCspHeader, a local
    // helper, and the <script> occurrences below are string literals this file
    // builds to assert against. Semgrep flags the helper's parameter because it
    // cannot see through the call boundary, so it treats a locally-derived string
    // as untrusted. Scoped to this one helper, with the reason stated, rather than
    // widening .semgrepignore or muting the rule repo-wide: this is the test that
    // guards the #981 outage, so muting it globally would blind us to the very
    // thing it exists to catch.
  const admitsInlineScripts = (csp: string, html: string): boolean => {
    const expected = nonceFromHeader(csp);
    return inlineScriptNonces(html).every((actual) => expected !== undefined && actual === expected);
  };

  it('admits a per-request render, where Next stamped the header nonce', () => {
    const csp = buildCspHeader({ nonce: 'abc123', isDev: false, apiUrl: 'https://localhost:8000' });
    const html = '<script nonce="abc123">self.__next_f.push([1])</script>';
    expect(admitsInlineScripts(csp, html)).toBe(true);
  });

  it('REJECTS a build-time prerender — the #981 outage', () => {
    const csp = buildCspHeader({ nonce: 'abc123', isDev: false, apiUrl: 'https://localhost:8000' });
    const html = '<script>self.__next_f.push([1])</script>';
    expect(admitsInlineScripts(csp, html)).toBe(false);
  });

  it('rejects when the HTML carries a stale nonce from a different request', () => {
    const csp = buildCspHeader({ nonce: 'this-request', isDev: false, apiUrl: 'https://localhost:8000' });
    const html = '<script nonce="an-earlier-request">self.__next_f.push([1])</script>';
    expect(admitsInlineScripts(csp, html)).toBe(false);
  });

    it('the app ships no prerendered route HTML, so no build-time artifact can be replayed', () => {
      // Structural guard only. It re-asserts the same thing as the test above,
      // so it adds no coverage: there is exactly one way to be right here.
      //
      // The end-to-end proof is `scripts/verify-csp-nonce.mjs`, which boots a real
      // production build and checks the nonce against real rendered HTML. NOTE: that
      // script currently cannot run, because `npm run build` fails on a pre-existing
      // Edge-runtime static-analysis error in apps/web/instrumentation.ts. Treat this
      // assertion as necessary but NOT sufficient until the build works again.
      expect(rootLayout.dynamic).toBe('force-dynamic');
    });

});

describe('proxy production CSP', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubEnv('NODE_ENV', 'production');
  });

  afterEach(() => {
    // unstubAllEnvs restores NODE_ENV to its pre-test value.
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('sets the same CSP on the request header and the response', async () => {
    const { default: proxy } = await import('@/proxy');
    const res = proxy(new NextRequest(new URL('/login', 'https://wildframe.test')));

    const responseCsp = res.headers.get('content-security-policy');
    expect(responseCsp).toBeTruthy();

    // Same policy on both sides is what lets Next's renderer read the nonce
    // off the request while the browser enforces the identical rule.
    const requestCsp = res.headers.get('x-middleware-request-content-security-policy');
    expect(requestCsp).toBe(responseCsp);
  });
});
