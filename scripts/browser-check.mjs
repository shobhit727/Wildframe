#!/usr/bin/env node
/**
 * browser-check.mjs — drive the running app in a real browser and report what a
 * human would see.
 *
 * Why this exists: `curl` cannot tell you whether a page hydrated. During the audit
 * that produced `https://github.com/shobhit727/Wildframe/issues/981` every route
 * returned HTTP 200 with a completely empty body, and a curl-and-grep for `<input`
 * confidently reported "0 inputs" on a page a browser rendered with five.
 *
 * What it reports per route:
 *   - HTTP status of the document
 *   - whether any form inputs exist after hydration
 *   - CSP violations the browser actually logged
 *   - the visible text, so a blank page is obvious
 *   - where the browser ended up after redirects (an auth bounce is usually the
 *     real story, e.g. /browse -> /login)
 *
 * Usage:
 *   node scripts/browser-check.mjs                       # default routes
 *   node scripts/browser-check.mjs / /login /signup      # specific routes
 *   BASE_URL=http://localhost:3000 node scripts/browser-check.mjs
 *
 * Exit code 0 if every route rendered content, 1 otherwise — so it can gate a build.
 */

import { createRequire } from 'node:module';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const BASE = (process.env.BASE_URL || 'https://localhost:3000').replace(/\/$/, '');
const DEFAULT_ROUTES = ['/', '/login', '/signup', '/browse'];
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 60000);
const SETTLE_MS = Number(process.env.SETTLE_MS || 5000);

function resolvePlaywright() {
  for (const base of [process.cwd(), path.join(process.cwd(), 'apps/web')]) {
    try {
      return require(path.join(base, 'node_modules/playwright-core'));
    } catch {
      /* keep looking */
    }
  }
  throw new Error(
    'playwright-core not found. Install it, or run this from the repo root / apps/web.\n' +
      '  npm i -D playwright-core   (no browser download needed if you pass --executable)',
  );
}

/**
 * Prefer an explicitly provided binary, then Playwright's own download, so this
 * works on a machine that installed browsers somewhere unexpected.
 */
function resolveExecutable() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const cache = path.join(homedir(), '.cache', 'ms-playwright');
  if (existsSync(cache)) {
    for (const dir of readdirSync(cache)) {
      for (const rel of [
        'chrome-linux64/chrome',
        'chrome-linux/chrome',
        'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
        'chrome-win/chrome.exe',
      ]) {
        const candidate = path.join(cache, dir, rel);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  // Fall back to Playwright's own resolution (may throw a useful error).
  return undefined;
}

async function main() {
  const routes = process.argv.slice(2).filter((a) => !a.startsWith('-'));
  const targets = routes.length ? routes : DEFAULT_ROUTES;
  const { chromium } = resolvePlaywright();

  const browser = await chromium.launch({
    executablePath: resolveExecutable(),
    args: ['--no-sandbox'],
  });

  const results = [];
  for (const route of targets) {
    // Fresh context per route: a session cookie from one page must not make the
    // next route look authenticated, which is a classic false pass.
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    const violations = [];
    const pageErrors = [];
    page.on('console', (m) => {
      const t = m.text();
      // Normalise the nonce so identical violations collapse into one line.
      if (t.includes('Content Security Policy')) violations.push(t.replace(/'nonce-[^']+'/g, "'nonce-…'"));
    });
    page.on('pageerror', (e) => pageErrors.push(String(e).slice(0, 160)));

    let status = null;
    let error = null;
    try {
      const res = await page.goto(BASE + route, { waitUntil: 'load', timeout: TIMEOUT_MS });
      status = res ? res.status() : null;
      await page.waitForTimeout(SETTLE_MS);
    } catch (e) {
      error = String(e).split('\n')[0].slice(0, 140);
    }

    const inputs = error ? 0 : await page.$$eval('input', (els) => els.length).catch(() => 0);
    const text = error
      ? ''
      : await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').trim()).catch(() => '');
    const landed = error ? '' : new URL(page.url()).pathname;

    results.push({ route, status, inputs, violations: [...new Set(violations)], pageErrors, landed, text, error });
    await ctx.close();
  }
  await browser.close();

  console.log(`\n  ${BASE}\n`);
  let bad = 0;
  for (const r of results) {
    const empty = r.text.length === 0;
    // A route that is legitimately empty (no form) is fine if it says something.
    const ok = !r.error && !empty && r.violations.length === 0;
    if (!ok) bad++;
    console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${r.route}`);
    console.log(`       status=${r.status}  inputs=${r.inputs}  landed=${r.landed}`);
    if (r.error) console.log(`       error: ${r.error}`);
    if (empty) console.log('       body is EMPTY — the page rendered nothing');
    if (r.violations.length) r.violations.forEach((v) => console.log(`       CSP: ${v.slice(0, 120)}`));
    if (r.pageErrors.length) r.pageErrors.forEach((e) => console.log(`       JS:   ${e}`));
    console.log(`       text: ${JSON.stringify(r.text.slice(0, 90))}`);
    console.log('');
  }

  if (bad === 0) {
    console.log(`  all ${results.length} route(s) rendered content\n`);
  } else {
    console.log(`  ${bad}/${results.length} route(s) failed — an empty body or a CSP violation means the page did not hydrate\n`);
  }
  process.exit(bad === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(`browser-check failed: ${e.message}`);
  process.exit(2);
});
