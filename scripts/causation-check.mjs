#!/usr/bin/env node
/**
 * causation-check.mjs — prove *why* something breaks, instead of theorising.
 *
 * A hypothesis is cheap; a cause you can toggle is not. This strips exactly one
 * response header, reloads the page, and reports whether the symptom disappears.
 * If it does, that header is the cause. If it does not, your hypothesis is wrong
 * and you have learned something worth more than the fix.
 *
 * The real use: the frontend rendered a blank body on every route while CI was
 * fully green. Rather than reasoning about which layer was at fault, this removed
 * only the Content-Security-Policy header and the page rendered with five inputs.
 * That turned "the CSP is probably involved" into a demonstrated cause, and it
 * took one run.
 *
 * Usage:
 *   node scripts/causation-check.mjs                       # default: CSP on /signup
 *   node scripts/causation-check.mjs /browse content-security-policy
 *   node scripts/causation-check.mjs /login x-frame-options
 *   KEEP_HEADER=1 node scripts/causation-check.mjs         # reverse: strip, keep control
 *
 * Exit 0 if stripping the header CHANGED the result (strong evidence the header is
 * the cause). Exit 1 if nothing changed (hypothesis not supported). Exit 3 if the
 * baseline already renders, so there is nothing to explain.
 */

import { createRequire } from 'node:module';
import { existsSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const BASE = (process.env.BASE_URL || 'https://localhost:3000').replace(/\/$/, '');
const ROUTE = process.argv[2] && !process.argv[2].startsWith('-') ? process.argv[2] : '/signup';
const HEADER = process.argv[3] || 'content-security-policy';
const KEEP = process.env.KEEP_HEADER === '1';
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 60000);
const SETTLE_MS = Number(process.env.SETTLE_MS || 6000);

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

async function observe(browser, { strip }) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const violations = [];
  page.on('console', (m) => {
    const t = m.text();
    if (t.includes('Content Security Policy')) violations.push(t.replace(/'nonce-[^']+'/g, "'nonce-…'"));
  });

  if (strip) {
    // route.fetch() performs the request, then we re-serve the response without the
    // header. Nothing else about the page changes.
    await page.route('**/*', async (route) => {
      const res = await route.fetch();
      const headers = { ...res.headers };
      delete headers[HEADER];
      await route.fulfill({ response: res, headers });
    });
  }

  let error = null;
  try {
    await page.goto(BASE + ROUTE, { waitUntil: 'load', timeout: TIMEOUT_MS });
    await page.waitForTimeout(SETTLE_MS);
  } catch (e) {
    error = String(e).split('\n')[0].slice(0, 140);
  }

  const inputs = error ? 0 : await page.$$eval('input', (e) => e.length).catch(() => 0);
  const text = error ? '' : await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' ').trim()).catch(() => '');
  await ctx.close();
  return { inputs, text, violations: [...new Set(violations)], error };
}

function describe(o) {
  return `inputs=${o.inputs} text=${JSON.stringify(o.text.slice(0, 70))} cspViolations=${o.violations.length}`;
}

async function main() {
  const { chromium } = resolvePlaywright();
  const browser = await chromium.launch({ executablePath: resolveExecutable(), args: ['--no-sandbox'] });

  const withHeader = await observe(browser, { strip: false });
  const withoutHeader = await observe(browser, { strip: true });
  await browser.close();

  console.log(`\n  ${BASE}${ROUTE}`);
  console.log(`  header under test: ${HEADER}   mode: ${KEEP ? 'strip BASELINE, keep control' : 'keep header, strip TEST'}\n`);
  console.log(`  as served        : ${describe(withHeader)}`);
  console.log(`  header stripped  : ${describe(withoutHeader)}\n`);

  if (withHeader.error || withoutHeader.error) {
    console.log('  INCONCLUSIVE — a navigation failed, so there is nothing to compare.\n');
    process.exit(3);
  }

  const rendered = (o) => o.inputs > 0 || o.text.length > 0;

  // Naming matters here, and the previous version had this inverted: it reached
  // "CAUSE SUPPORTED" only when the header was demonstrably NOT the cause. A
  // truth table over the four combinations is in scripts/README.md.
  //
  // baseline: what happens with the header as served
  // variant:  what happens with the header removed
  const baselineRenders = rendered(withHeader);
  const variantRenders = rendered(withoutHeader);

  // Nothing to explain if the page already works as served, or if both arms
  // behave identically.
  if (baselineRenders || baselineRenders === variantRenders) {
    console.log('  The page renders as served (or behaves identically either way), so this\n');
    console.log('  header is not what is breaking it. Either the fault is elsewhere, or\n');
    console.log('  you need a route that actually fails. No cause established.\n');
    process.exit(3);
  }

  if (variantRenders && !baselineRenders) {
    console.log(`  CAUSE SUPPORTED — removing '${HEADER}' alone makes the page render.\n`);
    console.log('  That is a demonstrated cause, not a correlation. Now fix that header.\n');
    process.exit(0);
  }

  console.log(`  HYPOTHESIS NOT SUPPORTED — removing '${HEADER}' did not change anything.\n`);
  console.log('  The header is not the cause. This is worth knowing: it rules the layer\n');
  console.log('  out, so stop investigating it and look elsewhere.\n');
  process.exit(1);
}

main().catch((e) => {
  console.error(`causation-check failed: ${e.message}`);
  process.exit(2);
});
