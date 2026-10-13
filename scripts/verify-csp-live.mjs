#!/usr/bin/env node
/**
 * verify-csp-live.mjs — the live-server half of verify-csp-nonce.mjs.
 *
 * Called by verify-csp-nonce.mjs when BASE_URL is set. It probes a server that
 * is already running (typically the container on :3000) instead of spawning its
 * own, so the verdict is about the thing a user actually hits.
 *
 * Differences from the spawn-your-own path, both of which produced false
 * findings when they were missing:
 *   - ROUTES honors argv, so `verify-csp-nonce.mjs /creator` checks /creator
 *     instead of silently sweeping a hardcoded 8-route list.
 *   - A 3xx is treated as a PASS when it is the correct unauthenticated bounce
 *     for a protected route. The proxy redirects /browse, /watch, /my-list,
 *     /account, /billing and /creator to /login (apps/web/src/proxy.ts:27);
 *     counting those as CSP failures reports four correct responses as
 *     defects and turns the headline into "8/8 routes fail" when the app is
 *     fine.
 */

import fs from 'node:fs/promises';

// This harness verifies the local dev stack, whose TLS layer is Caddy fronting
// the certificates scripts/generate-dev-certs.sh generates — self-signed by
// design and gitignored. Node rejects them by default, which made every live
// probe fail with DEPTH_ZERO_SELF_SIGNED_CERT and read like the CSP bug it is
// not.
//
// It used to work around that by setting NODE_TLS_REJECT_UNAUTHORIZED=0, which
// disables verification entirely. That accepted an attacker's certificate just
// as readily as ours, and because the host came from an unvalidated BASE_URL it
// applied to any host the script was pointed at. Trust the dev certificate as a
// CA instead: NODE_EXTRA_CA_CERTS keeps certificate *and* hostname verification
// on, so only the dev CA is additionally trusted.
//
// Node reads NODE_EXTRA_CA_CERTS once at process start, so setting it here would
// be too late. Re-exec ourselves with it instead.
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]', '::1']);
const DEV_CA = new URL('../apps/web/certificates/localhost.pem', import.meta.url);

{
  const target = new URL(process.env.BASE_URL ?? 'https://localhost:3000');
  if (!LOCAL_HOSTS.has(target.hostname)) {
    console.error(
      `refusing to probe ${target.hostname}: this harness exists to verify the LOCAL dev\n` +
        'stack and only trusts the self-signed dev CA. Pointing it at a real host would\n' +
        'require trusting that host\'s CA; use a normal HTTP client for remote targets.'
    );
    process.exit(2);
  }

  if (!process.env.NODE_EXTRA_CA_CERTS) {
    try {
      await fs.access(DEV_CA);
    } catch {
      console.error(
        `no dev CA at ${DEV_CA.pathname}. Run scripts/generate-dev-certs.sh first, or set\n` +
          'NODE_EXTRA_CA_CERTS to the CA that signed the server you are probing.'
      );
      process.exit(2);
    }
    const { spawnSync } = await import('node:child_process');
    const result = spawnSync(
      process.execPath,
      [process.argv[1], ...process.argv.slice(2)],
      {
        stdio: 'inherit',
        env: { ...process.env, NODE_EXTRA_CA_CERTS: DEV_CA.pathname },
      }
    );
    process.exit(result.status ?? 1);
  }
}

const PROTECTED = ['/browse', '/watch', '/my-list', '/account', '/billing', '/creator'];

const DEFAULT_ROUTES = ['/', '/login', '/signup', '/account', '/my-list', '/billing', '/creator', '/watch/abc'];

const nonceFromCsp = (csp) => /script-src[^;]*'nonce-([^']+)'/.exec(csp)?.[1];

const inlineScriptNonces = (html) =>
  [...html.matchAll(/<script(?![^>]*\ssrc=)([^>]*)>/g)].map((m) => /nonce="([^"]+)"/.exec(m[1])?.[1]);

export default async function check(base) {
  const routes = process.argv.slice(2).filter((a) => !a.startsWith('-'));
  const targets = routes.length ? routes : DEFAULT_ROUTES;

  let failures = 0;
  let checked = 0;
  let bounced = 0;

  for (const route of targets) {
    let res;
    try {
      res = await fetch(base + route, { redirect: 'manual' });
    } catch (err) {
      failures++;
      console.log(`  FAIL ${route}  - ${err.cause?.code ?? err.message}`);
      continue;
    }

    if (res.status >= 300 && res.status < 400) {
      const location = res.headers.get('location') ?? '';
      if (PROTECTED.some((p) => route === p || route.startsWith(p + '/')) && location.startsWith('/login')) {
        // Correct unauthenticated bounce for a protected route. Counting it as
        // a CSP failure reported four right answers as defects.
        bounced++;
        console.log(`  ok   ${route}  -> ${res.status} ${location} (correct protected-route bounce)`);
      } else {
        failures++;
        console.log(`  FAIL ${route}  -> ${res.status} ${location}`);
        console.log('        - unexpected redirect');
      }
      continue;
    }
    checked++;

    const csp = res.headers.get('content-security-policy') ?? '';
    const html = await res.text();
    const expected = nonceFromCsp(csp);
    const actual = inlineScriptNonces(html);

    const problems = [];
    if (!expected) problems.push('response CSP has no script-src nonce');
    if (actual.length === 0) problems.push('no inline bootstrap scripts found (unexpected HTML shape)');
    if (actual.some((n) => n !== expected)) {
      problems.push(`inline script nonces ${JSON.stringify(actual)} != header nonce ${expected}`);
    }
    if (res.headers.get('x-nextjs-prerender')) {
      problems.push('response was served from a build-time prerender (x-nextjs-prerender)');
    }

    if (problems.length) {
      failures++;
      console.log(`  FAIL ${route}`);
      for (const p of problems) console.log(`        - ${p}`);
    } else {
      console.log(`  ok   ${route}  ${actual.length} inline script(s) carry nonce ${expected.slice(0, 8)}…`);
    }
  }

  if (failures) {
    console.error(`\n✗ ${failures}/${targets.length} route(s) did not serve CSP-compatible inline scripts.`);
    console.error('  Issue #981: routes are being prerendered at build time, so the per-request');
    console.error('  nonce cannot reach the inline scripts. See src/app/layout.tsx.');
    process.exit(1);
  }
  const bounceNote = bounced ? ` (+${bounced} correct protected-route bounce(s))` : '';
  console.log(`\n✓ all ${checked} route(s) serve CSP-compatible inline scripts${bounceNote}`);
}
