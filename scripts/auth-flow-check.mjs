#!/usr/bin/env node
/**
 * auth-flow-check.mjs — prove a user can actually register and stay signed in.
 *
 * Why this exists: registration returned HTTP 201 and the account was created, yet
 * the UI said "Could not create the account. Please try again." Three separate
 * defects stacked behind that one message:
 *
 *   1. the whole app rendered a blank page, so nothing was observable
 *   2. the session cookie was rejected (token longer than the cap in the route)
 *   3. a hard reload 502'd, so the session appeared not to survive a refresh
 *
 * A "registration works" check that stops at the 201 catches none of those. This
 * walks the whole chain a user actually walks, and fails loudly if any step is not
 * what it should be — including aborting rather than reporting success when setup
 * itself was rate limited.
 *
 * Usage:
 *   node scripts/auth-flow-check.mjs
 *   BASE_URL=https://localhost:3000 node scripts/auth-flow-check.mjs
 *   ALLOW_INSECURE_TLS=1 node scripts/auth-flow-check.mjs    # self-signed dev cert
 *
 * Exit 0 if the user can register, be recognised, and survive a hard reload.
 */

import { createRequire } from 'node:module';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

// The dev stack serves HTTPS with a self-signed certificate, which node's fetch
// rejects. Opt in explicitly rather than disabling verification globally, so this
// is never silently insecure and cannot be mistaken for a production setting.
if (process.env.ALLOW_INSECURE_TLS === '1') {
  const { Agent, setGlobalDispatcher } = await import('undici').catch(() => ({}));
  if (setGlobalDispatcher && Agent) {
    setGlobalDispatcher(new Agent({ connect: { rejectUnauthorized: false } }));
    console.log('  (TLS verification disabled — ALLOW_INSECURE_TLS=1, dev only)');
  } else {
    console.error('  undici unavailable; cannot disable TLS verification.');
    console.error('  Prefer: NODE_EXTRA_CA_CERTS=apps/web/certificates/localhost.pem node scripts/auth-flow-check.mjs');
    process.exit(2);
  }
}

const require = createRequire(import.meta.url);
const BASE = (process.env.BASE_URL || 'https://localhost:3000').replace(/\/$/, '');
const GATEWAY = (process.env.GATEWAY_URL || 'https://localhost:8000').replace(/\/$/, '');
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 60000);
const SETTLE_MS = Number(process.env.SETTLE_MS || 4000);

const stamp = Date.now();
// Two accounts on purpose. The API probe registers one, and the browser step then
// submits the real signup form for a DIFFERENT address - registering the same one
// twice returns 409, which is the service behaving correctly but reads as a
// failure of this script.
const API_EMAIL = process.env.CHECK_EMAIL || `agent-api-${stamp}@example.com`;
const UI_EMAIL = process.env.CHECK_UI_EMAIL || `agent-ui-${stamp}@example.com`;
const PASSWORD = process.env.CHECK_PASSWORD || 'Str0ng!Passw0rd';

function resolvePlaywright() {
  for (const base of [process.cwd(), path.join(process.cwd(), 'apps/web')]) {
    try {
      return require(path.join(base, 'node_modules/playwright-core'));
    } catch {
      /* keep looking */
    }
  }
  throw new Error('playwright-core not found. Run from the repo root or apps/web.');
}

function resolveExecutable() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const cache = path.join(homedir(), '.cache', 'ms-playwright');
  if (!existsSync(cache)) return undefined;
  for (const dir of readdirSync(cache)) {
    for (const rel of ['chrome-linux64/chrome', 'chrome-linux/chrome', 'chrome-mac/Chromium.app/Contents/MacOS/Chromium', 'chrome-win/chrome.exe']) {
      const candidate = path.join(cache, dir, rel);
      if (existsSync(candidate)) return candidate;
    }
  }
  return undefined;
}

/** Call the API directly, so a failure here is unambiguous and not a UI symptom. */
async function api(pathname, init = {}) {
  const res = await fetch(GATEWAY + pathname, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  return { status: res.status, body };
}

const steps = [];
function record(name, ok, detail) {
  steps.push({ name, ok, detail });
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${name}\n       ${detail}`);
}

async function main() {
  const { chromium } = resolvePlaywright();

  console.log(`\n  auth flow check   ${BASE}\n`);

  // 1. Registration, straight to the API.
  const reg = await api('/auth/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email: API_EMAIL, password: PASSWORD, first_name: 'Agent', last_name: 'Check' }),
  });
  const refresh = reg.body && reg.body.refresh_token;
  if (reg.status !== 201 || !refresh) {
    // Abort rather than continue. A rate-limited or failed setup would otherwise be
    // misreported later as a session or reload regression.
    record('register', false, `expected 201, got ${reg.status} ${JSON.stringify(reg.body).slice(0, 120)}`);
    console.log('\n  Aborting: setup failed, so later steps would prove nothing.\n');
    console.log('  (A 429 here is the gateway rate limit, not a product fault. Wait and retry.)\n');
    process.exit(3);
  }
  record('register', true, `201, refresh_token ${refresh.length} chars`);

  // 2. The session must be accepted, not just issued.
  const sess = await fetch(BASE + '/auth-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  const setCookie = sess.headers.get('set-cookie') || '';
  const cookieOk = sess.status === 200 && setCookie.includes('__Host-wf_refresh');
  record(
    'session set',
    cookieOk,
    `${sess.status}, cookie ${cookieOk ? 'stored' : 'MISSING'} (${setCookie.slice(0, 70) || 'no set-cookie'})`,
  );

  // 3. Server-side session read, which is what a page reload hits. This is the step
  //    that returned 502 when server-side code pointed at the wrong container.
  const me = await fetch(BASE + '/auth-session', { headers: { cookie: setCookie.split(';')[0] || '' } });
  let meNote = `GET /auth-session -> ${me.status}`;
  if (me.status === 502) meNote += ' (server cannot reach the API — wrong in-container base URL)';
  else if (me.status !== 200) meNote += ' (expected 200 for a valid session)';
  record('session read (server-side)', me.status === 200, meNote);

  // 4. Now the same thing through a browser, including a hard reload of a protected
  //    route. This is the only part that proves hydration actually works.
  const browser = await chromium.launch({ executablePath: resolveExecutable(), args: ['--no-sandbox'] });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();

  let hydrated = false;
  let stayedSignedIn = false;
  let note = '';

  try {
    // Drive the real signup form rather than injecting a cookie.
    //
    // An earlier version of this script seeded `__Host-wf_refresh` with
    // `addCookies({ domain })`, which emits a Domain attribute. A `__Host-` cookie
    // must not have one, so the browser dropped it and the reload appeared to fail
    // for a reason that had nothing to do with the product. Submitting the form is
    // slower and less surgical, but it cannot produce a false failure, and it
    // exercises the path a real user takes.
    await page.goto(BASE + '/signup', { waitUntil: 'load', timeout: TIMEOUT_MS });
    await page.waitForSelector('input', { timeout: SETTLE_MS * 4 });

    // Selectors verified against the live form. Guessed selectors produced a
    // script that silently filled nothing and then reported a product failure.
    const FIELDS = {
      firstName: '#firstName',
      lastName: '#lastName',
      email: '#email',
      password: '#password',
      confirmPassword: '#confirmPassword',
    };
    for (const [name, sel] of Object.entries(FIELDS)) {
      const el = await page.$(sel);
      if (!el) throw new Error(`signup form is missing ${name} (${sel}) - the form changed`);
    }
    await page.fill(FIELDS.firstName, 'Agent');
    await page.fill(FIELDS.lastName, 'Check');
    await page.fill(FIELDS.email, UI_EMAIL);
    await page.fill(FIELDS.password, PASSWORD);
    await page.fill(FIELDS.confirmPassword, PASSWORD);

    await Promise.all([
      page.waitForResponse((r) => r.url().includes('/auth/api/v1/auth/register'), { timeout: SETTLE_MS * 6 }),
      page.click('button[type="submit"]'),
    ]).then(([reg]) => {
      if (reg.status() !== 201) note += `register via form returned ${reg.status()}; `;
    }).catch((e) => {
      note += `form submit did not return a register response (${String(e).split('\n')[0].slice(0, 60)}); `;
    });

    await page.waitForTimeout(SETTLE_MS * 2);
    const cookies = (await ctx.cookies(BASE)).map((c) => c.name);
    note += `cookies after signup: ${cookies.join(', ') || 'none'}; `;

    // The whole point: a hard reload of a PROTECTED route must not bounce to /login.
    await page.goto(BASE + '/browse', { waitUntil: 'load', timeout: TIMEOUT_MS });
    await page.waitForTimeout(SETTLE_MS);
    await page.reload({ waitUntil: 'load', timeout: TIMEOUT_MS });
    await page.waitForTimeout(SETTLE_MS);

    const landed = new URL(page.url()).pathname;
    const text = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').trim());
    hydrated = text.length > 0;
    stayedSignedIn = landed !== '/login' && text.length > 0;
    note += `after hard reload landed on ${landed}: ${JSON.stringify(text.slice(0, 55))}`;
  } catch (e) {
    note += `browser error: ${String(e).split('\n')[0].slice(0, 120)}`;
  } finally {
    await browser.close();
  }

  record('page hydrates', hydrated, note);
  record('session survives reload', stayedSignedIn, stayedSignedIn ? 'still authenticated after reload' : 'bounced to /login or blank');

  const failed = steps.filter((s) => !s.ok);
  console.log('');
  if (failed.length === 0) {
    console.log(`  PASS — a real user can register and stay signed in (${UI_EMAIL})\n`);
  } else {
    console.log(`  FAIL — ${failed.length}/${steps.length} step(s) broken. Registration returning 201 is NOT sufficient.\n`);
  }
  process.exit(failed.length === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(`auth-flow-check failed: ${e.message}`);
  process.exit(2);
});
