/**
 * End-to-end regression check for issue #981: the production server must emit
 * inline RSC bootstrap scripts that carry the nonce from the response CSP.
 *
 * This is the layer the jsdom suite structurally cannot reach. The vitest
 * tests assert the *coupling* (per-request nonce => no static prerender);
 * this script proves the coupling actually holds in the bytes a browser
 * receives. It boots a real production build, fetches real routes, and
 * evaluates the real CSP against the real HTML.
 *
 * `csp.test.ts` asserted only that the header string was well formed, which is
 * why a total outage shipped green: the symptom was never in the string, it
 * was that the nonce never reached the scripts.
 *
 * Usage:
 *   node scripts/verify-csp-nonce.mjs
 * Requires a production build in apps/web/.next (the script builds if absent).
 */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const WEB_DIR = join(REPO_ROOT, 'apps', 'web');
const PORT = Number(process.env.CSP_VERIFY_PORT ?? 3998);
const BASE = `http://127.0.0.1:${PORT}`;

// /browse is a 307 to /login without a session cookie, so it has no body of
// its own to check. Every other route renders markup.
const ROUTES = ['/', '/login', '/signup', '/account', '/my-list', '/billing', '/creator', '/watch/abc'];

const run = (cmd, args, opts = {}) =>
  new Promise((res, rej) => {
    const p = spawn(cmd, args, { stdio: 'inherit', ...opts });
    p.on('exit', (code) => (code === 0 ? res() : rej(new Error(`${cmd} ${args.join(' ')} exited ${code}`))));
    p.on('error', rej);
  });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForServer(timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(BASE + '/', { redirect: 'manual' });
      if (res.status) return;
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  throw new Error(`server did not start on ${BASE} within ${timeoutMs}ms`);
}

const nonceFromCsp = (csp) => /script-src[^;]*'nonce-([^']+)'/.exec(csp)?.[1];

const inlineScriptNonces = (html) =>
  [...html.matchAll(/<script(?![^>]*\ssrc=)([^>]*)>/g)].map((m) => /nonce="([^"]+)"/.exec(m[1])?.[1]);

// /browse and friends are gated by the proxy's optimistic session-cookie
// check. A bare fetch gets a 307 to /login with no body, so pass the presence
// cookie the proxy looks for — the real authorization still happens in the
// destination service; this only gets us markup to inspect.
const SESSION_HEADERS = { cookie: '__Host-wf_refresh=verify-only-presence-token' };

async function main() {
  // BASE_URL makes this script verify a RUNNING server — typically the
  // container on :3000 — instead of spawning its own. Without it the script
  // serves whatever .next happens to sit in apps/web: a stale build predating
  // a force-dynamic fix reports a false site-wide CSP failure (issue #981's
  // own symptom) about code that was already correct, and the BUILD_ID check
  // below cannot distinguish a fresh build from a stale one.
  const liveBase = process.env.BASE_URL?.replace(/\/$/, '');
  if (liveBase) {
    const { default: check } = await import('./verify-csp-live.mjs');
    await check(liveBase);
    return;
  }

  if (!existsSync(join(WEB_DIR, '.next', 'BUILD_ID'))) {
    console.log('· no production build found, running `next build` (this takes a minute)…');
    await run('npm', ['run', 'build'], { cwd: WEB_DIR });
  }

  console.log(`· starting production server on :${PORT}`);
  // NODE_ENV=production is what makes the proxy emit the strict nonce CSP at
  // all. Under `next dev` this check would pass vacuously, which is exactly
  // why the Playwright suite could not have caught #981.
  const server = spawn('npx', ['next', 'start', '-p', String(PORT)], {
    cwd: WEB_DIR,
    env: { ...process.env, NODE_ENV: 'production' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  server.stdout.on('data', (d) => process.env.VERBOSE && process.stdout.write(d));
  server.stderr.on('data', (d) => process.env.VERBOSE && process.stderr.write(d));

  let failures = 0;
  let checked = 0;
  try {
    await waitForServer();

    for (const route of ROUTES) {
      const res = await fetch(BASE + route, { redirect: 'manual', headers: SESSION_HEADERS });
      if (res.status >= 300 && res.status < 400) {
        failures++;
        console.log(`  FAIL ${route}  -> ${res.status} ${res.headers.get('location')}`);
        console.log('        - expected a rendered body, got a redirect');
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
  } finally {
    server.kill('SIGTERM');
  }

  if (failures) {
    console.error(`\n✗ ${failures}/${ROUTES.length} route(s) did not serve CSP-compatible inline scripts.`);
    console.error('  Issue #981: routes are being prerendered at build time, so the per-request');
    console.error('  nonce cannot reach the inline scripts. See src/app/layout.tsx.');
    process.exit(1);
  }
  console.log(`\n✓ all ${checked} route(s) serve CSP-compatible inline scripts`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
