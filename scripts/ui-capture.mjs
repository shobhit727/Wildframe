#!/usr/bin/env node
/**
 * ui-capture.mjs — render every route of the web app in a real browser, with
 * the API mocked, and report what the page actually looks like.
 *
 * WHY THIS EXISTS
 * ---------------
 * A UI review done by reading JSX is a review of intentions, not of the
 * rendered page. Two of the defects this catches are invisible in the source:
 *
 *   - Horizontal overflow. A row of cards that is 40px too wide scrolls the
 *     whole page sideways on a phone. Nothing in the markup says "this
 *     overflows"; only the rendered `scrollWidth` does.
 *   - Text that is unreadable against its own background, or a control with a
 *     24px hit area, or a focus ring that never appears. Again: only the
 *     rendered page knows.
 *
 * It reuses the repository's own E2E mock harness (`apps/web/e2e/fixtures.ts`)
 * so the pages render with real catalog payloads instead of empty states.
 * An empty grid hides most layout problems, which is precisely when a review
 * is worth doing.
 *
 * It also takes screenshots, because the point of a UI change is what a person
 * sees, and a diff of pixels is evidence a text report is not.
 *
 * THE UI-QUALITY AUDIT
 * --------------------
 * "The page rendered" is not the same claim as "the page is usable". Three
 * defect classes found by hand in one UI pass were invisible to the render
 * check, so they are now checked mechanically, on every route, on every run:
 *
 *   - WCAG 2.1 AA text contrast (1.4.3). For every element that directly
 *     contains text, the effective background is resolved by walking the
 *     ancestor chain and compositing each semi-transparent background over
 *     the first opaque one, and the WCAG sRGB relative-luminance contrast
 *     ratio is computed. Text below 4.5:1 fails; text at 24px, or 18.66px and
 *     bold, is large text and is held to 3:1.
 *   - WCAG 2.2 AA target size (2.5.8). Interactive elements -- button,
 *     a[href], input, select, textarea, [role=button], [role=tab] -- must
 *     present a 24x24 CSS px hit area. Under that in either dimension is
 *     flagged, with the tag, the accessible label and the measured size.
 *   - Heading structure. The h1 count per route. Zero h1s and more than one
 *     h1 are both reported.
 *
 * The audit is on by default, because a defect class that is only catchable
 * by hand is a defect class that comes back. `--no-audit` turns it off when
 * you only want the render/overflow sweep.
 *
 * What the audit cannot see, stated plainly:
 *   - Background *images* and gradients are not sampled. The background
 *     *colour* under them is used, which is the CSS `background-color` layer.
 *     A gradient that changes contrast substantially will be mismeasured.
 *   - Text painted by ::before/::after is not measured; it is not in the DOM
 *     as text.
 *   - `opacity` on a container is not composited; only background-colour
 *     alpha is.
 *   - WCAG 2.5.8 has exceptions (spacing, inline, essential, user-agent
 *     controls). This check applies the 24x24 rule literally, so a finding
 *     is a candidate for review, not a verdict.
 *
 * USAGE
 *   node scripts/ui-capture.mjs                      # all routes, desktop+mobile
 *   node scripts/ui-capture.mjs --routes /,/browse   # specific routes
 *   node scripts/ui-capture.mjs --viewport mobile     # one viewport
 *   node scripts/ui-capture.mjs --out tem/ui          # screenshot directory
 *   node scripts/ui-capture.mjs --no-audit            # render + overflow only
 *   node scripts/ui-capture.mjs --self-test           # prove the audit detects
 *   node scripts/ui-capture.mjs --help                # usage
 *   BASE_URL=https://localhost:3000 node scripts/ui-capture.mjs
 *
 * Screenshots default to `tem/ui/` inside the repository, which `.gitignore`
 * reserves for agent scratch (screenshots, logs, test scripts). They are
 * review artifacts, not source: they are deliberately not committed, and the
 * script never writes outside the repository unless `--out` says otherwise.
 *
 * EXIT CODES
 *   0  every route rendered clean: no console errors, no horizontal overflow,
 *      and no audit findings
 *   1  at least one route had a problem of any kind
 *   2  the script itself failed (no browser, no dev server, bad usage)
 *
 *   --self-test exits 0 when the audit detected every defect it was given
 *   and 1 when it did not -- a detector that has stopped detecting is the
 *   one failure this whole script exists to prevent.
 */

import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..');
const BASE = (process.env.BASE_URL || 'https://localhost:3000').replace(/\/$/, '');

const VIEWPORTS = {
  desktop: { width: 1440, height: 900 },
  mobile: { width: 390, height: 844 },
};

/** WCAG 2.2 AA minimum target size (2.5.8), in CSS px. */
const MIN_TARGET = 24;

const USAGE = `ui-capture.mjs - render every route of the web app in a real browser and audit it.

USAGE
  node scripts/ui-capture.mjs                      # all routes, desktop+mobile
  node scripts/ui-capture.mjs --routes /,/browse   # specific routes
  node scripts/ui-capture.mjs --viewport mobile     # one viewport (desktop|mobile)
  node scripts/ui-capture.mjs --out tem/ui          # screenshot directory
  node scripts/ui-capture.mjs --no-audit            # render + overflow only
  node scripts/ui-capture.mjs --self-test           # prove the audit detects
  node scripts/ui-capture.mjs --help                # this message
  BASE_URL=https://localhost:3000 node scripts/ui-capture.mjs

EXIT CODES
  0  every route rendered clean (no console errors, no overflow, no audit findings)
  1  at least one route had a problem
  2  the script itself failed (no browser, no dev server, bad usage)
  --self-test: 0 the audit detected every defect it was given, 1 it did not
`;

/** Routes and the session each needs. */
const ROUTES = [
  { path: '/', session: 'anonymous' },
  { path: '/login', session: 'anonymous' },
  { path: '/signup', session: 'anonymous' },
  { path: '/browse', session: 'user' },
  { path: '/my-list', session: 'user' },
  { path: '/account', session: 'user' },
  { path: '/billing', session: 'user' },
  { path: '/creator', session: 'user' },
  { path: '/watch/wf-movie-nightfall', session: 'user' },
  { path: '/admin', session: 'admin' },
  { path: '/admin/users', session: 'admin' },
  { path: '/admin/alerts', session: 'admin' },
  { path: '/admin/audit', session: 'admin' },
  { path: '/admin/config', session: 'admin' },
  { path: '/admin/flags', session: 'admin' },
];

/**
 * Read the value of a flag, refusing to swallow the next flag as a value.
 * A missing value is a usage error, not a silent empty string.
 */
function flagValue(argv, index, flag) {
  const value = argv[index];
  if (value === undefined || String(value).startsWith('--')) {
    throw new Error(`${flag} needs a value`);
  }
  return value;
}

function parseArgs(argv) {
  const out = {
    routes: null,
    viewports: Object.keys(VIEWPORTS),
    out: null,
    audit: true,
    help: false,
    selfTest: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--routes') {
      out.routes = flagValue(argv, ++i, '--routes')
        .split(',')
        .map((r) => r.trim())
        .filter(Boolean);
    } else if (arg === '--viewport') {
      out.viewports = flagValue(argv, ++i, '--viewport')
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean);
    } else if (arg === '--out') {
      out.out = flagValue(argv, ++i, '--out');
    } else if (arg === '--no-audit') {
      out.audit = false;
    } else if (arg === '--self-test') {
      out.selfTest = true;
    } else if (arg === '--help' || arg === '-h') {
      out.help = true;
    } else {
      throw new Error(`unknown flag: ${arg}`);
    }
  }
  return out;
}

function resolvePlaywright() {
  for (const base of [REPO, path.join(REPO, 'apps/web')]) {
    try {
      return require(path.join(base, 'node_modules/playwright-core'));
    } catch {
      /* keep looking */
    }
  }
  throw new Error(
    'playwright-core not found. Run `npm ci` in apps/web (or at the repo root) first.',
  );
}

/**
 * Measure the rendered page. Returns the facts that only exist after layout:
 * whether the page scrolls sideways, and which elements are responsible.
 */
const MEASURE = () => {
  const doc = document.documentElement;
  const viewport = doc.clientWidth;
  const overflow = Math.max(0, doc.scrollWidth - viewport);
  const offenders = [];
  if (overflow > 1) {
    for (const el of Array.from(document.querySelectorAll('body *'))) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      const right = rect.right + window.scrollX;
      if (right > viewport + 1) {
        offenders.push({
          tag: el.tagName.toLowerCase(),
          cls: (el.className && String(el.className).slice(0, 70)) || '',
          right: Math.round(right),
          width: Math.round(rect.width),
        });
      }
    }
  }
  // Keep the outermost offenders; a child of an offender is not a cause.
  offenders.sort((a, b) => b.width - a.width);
  return {
    overflow,
    viewport,
    offenders: offenders.slice(0, 5),
    title: document.title,
    textLength: (document.body.innerText || '').trim().length,
  };
};

/**
 * The UI-quality audit. Runs in the page, entirely on the rendered DOM.
 *
 * Self-contained on purpose: Playwright serialises this function and
 * evaluates it in the browser, so it cannot close over anything in this
 * module. The three checks are independent and the counts are the truth --
 * the per-route report may truncate the detail lines, but the totals are
 * what was actually found.
 */
const AUDIT = () => {
  // The page background, per apps/web/src/app/globals.css.
  const PAGE_BG = [11, 11, 11, 1];
  // These constants are repeated here, not imported: this function is
  // serialised and evaluated inside the browser, so it cannot close over
  // anything in this module. Keep them in step with the module-level ones.
  const NORMAL = 4.5;
  const LARGE = 3;
  const MIN_TARGET = 24;

  // -- colour ------------------------------------------------------------
  // Chrome serialises computed colours in whatever space the author used,
  // and Tailwind v4 emits oklch/lab, so a plain rgb() regex is not enough.
  // Canvas normalises every CSS colour syntax to sRGB bytes.
  const canvas = document.createElement('canvas');
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const colorCache = new Map();

  const parseColor = (value) => {
    if (!value) return null;
    const key = String(value).trim();
    if (colorCache.has(key)) return colorCache.get(key);
    let out = null;
    const m = key.match(/^rgba?\(([^)]+)\)$/i);
    if (m) {
      const parts = m[1].split(/[,\s/]+/).filter(Boolean);
      const r = parseFloat(parts[0]);
      const g = parseFloat(parts[1]);
      const b = parseFloat(parts[2]);
      const a = parts.length > 3 ? parseFloat(parts[3]) : 1;
      if (!Number.isNaN(r) && !Number.isNaN(g) && !Number.isNaN(b)) {
        out = [r, g, b, Number.isNaN(a) ? 1 : Math.max(0, Math.min(1, a))];
      }
    }
    if (!out) {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000000';
      ctx.fillStyle = key;
      ctx.fillRect(0, 0, 1, 1);
      const d = ctx.getImageData(0, 0, 1, 1).data;
      out = [d[0], d[1], d[2], d[3] / 255];
    }
    colorCache.set(key, out);
    return out;
  };

  /** source-over composite of `src` over `dst`, both [r,g,b,a]. */
  const composite = (src, dst) => {
    const a = src[3];
    if (a >= 1) return [src[0], src[1], src[2], 1];
    if (a <= 0) return [dst[0], dst[1], dst[2], dst[3]];
    const outA = a + dst[3] * (1 - a);
    return [
      (src[0] * a + dst[0] * dst[3] * (1 - a)) / outA,
      (src[1] * a + dst[1] * dst[3] * (1 - a)) / outA,
      (src[2] * a + dst[2] * dst[3] * (1 - a)) / outA,
      outA,
    ];
  };

  // getComputedStyle is called for every text element and every one of its
  // ancestors; the live CSSStyleDeclaration is cached per element.
  const styleCache = new WeakMap();
  const styleOf = (el) => {
    let s = styleCache.get(el);
    if (!s) {
      s = getComputedStyle(el);
      styleCache.set(el, s);
    }
    return s;
  };

  /**
   * The colour actually painted behind `el`: every background-colour from the
   * element up to the first opaque one, composited, over the page colour.
   */
  const effectiveBackground = (el) => {
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const bg = parseColor(styleOf(node).backgroundColor);
      if (bg && bg[3] > 0) {
        layers.push(bg);
        if (bg[3] >= 1) break;
      }
      node = node.parentElement;
    }
    let result = PAGE_BG;
    for (let i = layers.length - 1; i >= 0; i -= 1) result = composite(layers[i], result);
    return result;
  };

  // -- WCAG relative luminance and contrast ratio ------------------------
  const channel = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  const luminance = (rgb) =>
    0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
  const contrastRatio = (a, b) => {
    const la = luminance(a);
    const lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  const SKIP_TAGS = new Set([
    'SCRIPT',
    'STYLE',
    'NOSCRIPT',
    'TEMPLATE',
    'HEAD',
    'TITLE',
    'META',
    'LINK',
    'BR',
  ]);

  /** The element's own text, not its descendants'. */
  const directText = (el) => {
    let out = '';
    for (const child of el.childNodes) {
      if (child.nodeType === 3) out += child.nodeValue || '';
    }
    return out.replace(/\s+/g, ' ').trim();
  };

  const notRendered = (el, cs) => {
    if (cs.display === 'none') return true;
    if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return true;
    if (parseFloat(cs.opacity) === 0) return true;
    const rect = el.getBoundingClientRect();
    return rect.width === 0 && rect.height === 0;
  };

  // -- 1. contrast -------------------------------------------------------
  const contrastFailures = [];
  const walker = document.createTreeWalker(
    document.body || document.documentElement,
    NodeFilter.SHOW_TEXT,
  );
  const seen = new Set();
  let node = walker.nextNode();
  while (node) {
    const text = (node.nodeValue || '').trim();
    const parent = node.parentElement;
    if (text && parent && !seen.has(parent) && !SKIP_TAGS.has(parent.tagName)) {
      seen.add(parent);
      const cs = styleOf(parent);
      if (!notRendered(parent, cs)) {
        const fg = parseColor(cs.color);
        if (fg && fg[3] > 0) {
          const bg = effectiveBackground(parent);
          const effective = composite(fg, bg);
          const ratio = contrastRatio(effective, bg);
          const size = parseFloat(cs.fontSize) || 0;
          const weight = parseInt(cs.fontWeight, 10) || 400;
          const bold = weight >= 700;
          const large = size >= 24 || (size >= 18.66 && bold);
          const required = large ? LARGE : NORMAL;
          if (ratio < required) {
            contrastFailures.push({
              tag: parent.tagName.toLowerCase(),
              text: directText(parent).slice(0, 60) || text.slice(0, 60),
              ratio: Math.round(ratio * 100) / 100,
              required,
              size: Math.round(size * 10) / 10,
              bold,
              color: cs.color,
              background: `rgb(${Math.round(bg[0])}, ${Math.round(bg[1])}, ${Math.round(bg[2])})`,
            });
          }
        }
      }
    }
    node = walker.nextNode();
  }
  contrastFailures.sort((a, b) => a.ratio - b.ratio);

  // -- 2. target size ----------------------------------------------------
  const accessibleLabel = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria && aria.trim()) return aria.trim().replace(/\s+/g, ' ').slice(0, 40);
    const labelledBy = el.getAttribute('aria-labelledby');
    if (labelledBy) {
      const t = labelledBy
        .split(/\s+/)
        .map((id) => {
          const n = document.getElementById(id);
          return n ? n.textContent || '' : '';
        })
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();
      if (t) return t.slice(0, 40);
    }
    const text = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 40);
    const title = el.getAttribute('title');
    if (title && title.trim()) return title.trim().slice(0, 40);
    const alt = el.getAttribute('alt');
    if (alt && alt.trim()) return alt.trim().slice(0, 40);
    return '(no label)';
  };

  const INTERACTIVE = [
    'button',
    'a[href]',
    'input',
    'select',
    'textarea',
    '[role="button"]',
    '[role="tab"]',
  ].join(', ');
  const smallTargets = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    const cs = styleOf(el);
    if (cs.display === 'none') continue;
    if (cs.visibility === 'hidden' || cs.visibility === 'collapse') continue;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue; // not rendered
    if (rect.width >= MIN_TARGET && rect.height >= MIN_TARGET) continue;
    smallTargets.push({
      tag: el.tagName.toLowerCase(),
      label: accessibleLabel(el),
      width: Math.round(rect.width * 10) / 10,
      height: Math.round(rect.height * 10) / 10,
    });
  }

  // -- 3. heading structure ----------------------------------------------
  const h1s = Array.from(document.querySelectorAll('h1')).filter((el) => {
    const cs = styleOf(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden';
  });

  return {
    contrast: {
      count: contrastFailures.length,
      items: contrastFailures.slice(0, 8),
    },
    targets: {
      count: smallTargets.length,
      items: smallTargets.slice(0, 8),
    },
    headings: {
      count: h1s.length,
      items: h1s
        .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40))
        .slice(0, 4),
    },
  };
};

/**
 * A synthetic page carrying known defects, used by --self-test.
 *
 * Every case is hand-computable, so the expected ratio in EXPECTED_* below is
 * arithmetic rather than a recording of what the tool last said. The page
 * background is the app's #0b0b0b.
 */
const SELFTEST_HTML = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>self test</title>
<style>
  html, body { margin: 0; padding: 0; background: #0b0b0b; color: #ffffff; }
  body { font-family: sans-serif; padding: 16px; }
  .case-a { color: #333333; background: #0b0b0b; font-size: 16px; }
  .case-b { color: rgba(255, 255, 255, 0.28); background: #0b0b0b; font-size: 16px; }
  .case-c { color: #4a4a4a; background: rgba(255, 255, 255, 0.06); font-size: 16px; }
  .case-d { color: #767676; background: #0b0b0b; font-size: 30px; }
  .case-e { color: #767676; background: #0b0b0b; font-size: 19px; font-weight: 700; }
  .case-f { color: #767676; background: #0b0b0b; font-size: 19px; font-weight: 400; }
</style></head>
<body>
  <h1>Self test</h1>
  <p class="case-a">CASE-A must be reported</p>
  <p class="case-b">CASE-B must be reported</p>
  <p class="case-c">CASE-C must be reported</p>
  <p class="case-d">CASE-D must not be reported</p>
  <p class="case-e">CASE-E must not be reported</p>
  <p class="case-f">CASE-F must be reported</p>
  <p style="color: #ffffff; font-size: 16px; background: #0b0b0b;">CASE-G must not be reported</p>
  <div>
    <button id="tiny" style="width: 10px; height: 10px;">tiny</button>
    <button id="exact" style="width: 24px; height: 24px;">exact</button>
    <button id="short" style="width: 30px; height: 12px;">short</button>
    <button id="hidden" style="display: none;">hidden</button>
    <a id="link" href="#t" aria-label="tiny link" style="display:inline-block;width:16px;height:16px;"></a>
  </div>
  <p id="t">Filler text so the page is not visually empty and the case labels are not the only content.</p>
</body></html>`;

/**
 * What the audit must find on SELFTEST_HTML, and what it must not.
 *
 * The ratios are computed by hand from the WCAG sRGB formula against the
 * #0b0b0b page background:
 *   A  #333 on #0b0b0b                  -> 1.56:1  (normal text, must fail)
 *   B  rgba(255,255,255,.28) on #0b0b0b  -> 2.42:1  (text alpha composited)
 *   C  #4a4a4a on rgba(255,255,255,.06) -> 1.97:1  (background composited)
 *   D  #767676 at 30px                  -> 4.33:1  (large text, 3:1, passes)
 *   E  #767676 at 19px bold             -> 4.33:1  (large text, 3:1, passes)
 *   F  #767676 at 19px regular          -> 4.33:1  (normal text, must fail)
 *   G  #ffffff at 16px                  -> 19.7:1  (passes)
 */
const EXPECTED_CONTRAST_FOUND = [
  { marker: 'CASE-A', ratio: 1.56, required: 4.5 },
  { marker: 'CASE-B', ratio: 2.42, required: 4.5 },
  { marker: 'CASE-C', ratio: 1.97, required: 4.5 },
  { marker: 'CASE-F', ratio: 4.33, required: 4.5 },
];
const EXPECTED_CONTRAST_ABSENT = ['CASE-D', 'CASE-E', 'CASE-G'];
const EXPECTED_TARGETS_FOUND = ['tiny', 'short', 'tiny link'];
const EXPECTED_TARGETS_ABSENT = ['exact', 'hidden'];

/**
 * Run the real AUDIT against a page with known defects and check it found
 * them. This is the guard on the guard: an audit that silently stopped
 * detecting would report "0 findings", which reads as good news.
 */
async function runSelfTest(chromium) {
  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.setContent(SELFTEST_HTML);
  const audit = await page.evaluate(AUDIT);
  await browser.close();

  const problems = [];
  const found = audit.contrast.items.map((c) => c.text);
  for (const want of EXPECTED_CONTRAST_FOUND) {
    const hit = found.find((t) => t.includes(want.marker));
    if (!hit) {
      problems.push(`contrast: ${want.marker} was not reported`);
      continue;
    }
    const item = audit.contrast.items.find((c) => c.text.includes(want.marker));
    if (Math.abs(item.ratio - want.ratio) > 0.02) {
      problems.push(
        `contrast: ${want.marker} ratio ${item.ratio}:1, expected ${want.ratio}:1`,
      );
    }
    if (item.required !== want.required) {
      problems.push(
        `contrast: ${want.marker} required ${item.required}:1, expected ${want.required}:1`,
      );
    }
  }
  for (const marker of EXPECTED_CONTRAST_ABSENT) {
    if (found.some((t) => t.includes(marker))) {
      problems.push(`contrast: ${marker} was reported but should not have been`);
    }
  }

  const targets = audit.targets.items.map((t) => t.label);
  for (const want of EXPECTED_TARGETS_FOUND) {
    if (!targets.some((t) => t.includes(want))) {
      problems.push(`target: "${want}" was not reported`);
    }
  }
  for (const unwanted of EXPECTED_TARGETS_ABSENT) {
    if (targets.some((t) => t.includes(unwanted))) {
      problems.push(`target: "${unwanted}" was reported but should not have been`);
    }
  }
  if (audit.headings.count !== 1) {
    problems.push(`heading: counted ${audit.headings.count} h1, expected 1`);
  }

  const ok = problems.length === 0;
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} audit self-test`);
  console.log(
    `       contrast=${audit.contrast.count} found (expected ${EXPECTED_CONTRAST_FOUND.length}) ` +
      `targets=${audit.targets.count} found (expected ${EXPECTED_TARGETS_FOUND.length}) ` +
      `h1=${audit.headings.count}`,
  );
  problems.forEach((p) => console.log(`       problem: ${p}`));
  console.log('');
  return ok;
}

/**
 * Is the web app actually there? A dead dev server must be a clear failure
 * (exit 2), not fifteen identical navigation errors that read like fifteen
 * broken routes.
 */
async function preflight(browser) {
  const context = await browser.newContext({
    viewport: VIEWPORTS.desktop,
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();
  try {
    const response = await page.goto(BASE + '/', {
      waitUntil: 'domcontentloaded',
      timeout: 20000,
    });
    if (!response) return { ok: false, error: 'no response from the web app' };
    return { ok: true, status: response.status() };
  } catch (err) {
    return { ok: false, error: String(err && err.message ? err.message : err) };
  } finally {
    await context.close();
  }
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (err) {
    console.error(`ui-capture: ${err.message}`);
    console.error('');
    console.error(USAGE);
    process.exit(2);
  }

  if (args.help) {
    console.log(USAGE);
    process.exit(0);
  }

  const outDir = path.resolve(REPO, args.out || path.join('tem', 'ui'));
  mkdirSync(outDir, { recursive: true });

  const { chromium } = resolvePlaywright();

  if (args.selfTest) {
    // No dev server needed: the self-test builds its own page.
    const ok = await runSelfTest(chromium);
    process.exit(ok ? 0 : 1);
  }

  // Validate the usage before importing the fixtures, so a bad flag gets a
  // clean one-line error instead of a module-resolution warning first.
  const routes = args.routes
    ? ROUTES.filter((r) => args.routes.some((want) => r.path === want))
    : ROUTES;
  if (routes.length === 0) {
    console.error('no known routes matched --routes');
    process.exit(2);
  }
  for (const v of args.viewports) {
    if (!VIEWPORTS[v]) {
      console.error(`unknown viewport: ${v}`);
      process.exit(2);
    }
  }

  const fixtures = await import(
    pathToFileURL(path.join(REPO, 'apps/web/e2e/fixtures.ts')).href
  );

  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  const reachable = await preflight(browser);
  if (!reachable.ok) {
    console.error(`ui-capture: the web app is not reachable at ${BASE}`);
    console.error(`  ${reachable.error}`);
    console.error('  Is the dev server running?  cd apps/web && npm run dev');
    console.error('  Or point BASE_URL at the address it is listening on.');
    await browser.close();
    process.exit(2);
  }

  const results = [];
  for (const viewportName of args.viewports) {
    const viewport = VIEWPORTS[viewportName];
    for (const route of routes) {
      const context = await browser.newContext({
        viewport,
        ignoreHTTPSErrors: true,
        deviceScaleFactor: 1,
      });
      const page = await context.newPage();
      const errors = [];
      page.on('console', (m) => {
        if (m.type() !== 'error') return;
        const where = (m.location() && m.location().url) || '';
        const text = m.text().slice(0, 200);
        // A signed-out visitor gets a 401 from /auth-session. That is the
        // documented behaviour, not a defect, and the browser logs every
        // non-2xx as a console error.
        if (where.includes('/auth-session') && text.includes('401')) return;
        // The mock harness does not serve video, so the player cannot load a
        // manifest. That is a gap in the fixtures, not a page defect.
        if (where.includes('/streaming/') || text.includes('HLS')) return;
        errors.push(text);
      });
      page.on('pageerror', (e) => errors.push(`pageerror: ${String(e).slice(0, 180)}`));

      const label = route.path === '/' ? 'home' : route.path.replace(/\//g, '_').replace(/^_/, '');
      const file = path.join(outDir, `${viewportName}-${label}.png`);

      let status = 0;
      let measured = null;
      let audit = null;
      let failure = '';
      try {
        // signedIn/signedInAsAdmin call mockGateway themselves, so calling it
        // again here would re-register the handlers and drop the session --
        // which looks like "the route redirected to /login" and sends you
        // hunting a bug that is in the harness.
        if (route.session === 'user') await fixtures.signedIn(page);
        else if (route.session === 'admin') await fixtures.signedInAsAdmin(page);
        else await fixtures.loggedOut(page);

        const response = await page.goto(BASE + route.path, {
          waitUntil: 'domcontentloaded',
          timeout: 45000,
        });
        status = response ? response.status() : 0;
        // Give client components time to fetch, hydrate and settle.
        await page.waitForLoadState('networkidle', { timeout: 20000 }).catch(() => {});
        await page.waitForTimeout(1200);
        measured = await page.evaluate(MEASURE);
        if (args.audit) audit = await page.evaluate(AUDIT);
        await page.screenshot({ path: file, fullPage: true });
      } catch (err) {
        failure = String(err && err.message ? err.message : err).slice(0, 200);
      }

      results.push({
        viewport: viewportName,
        route: route.path,
        status,
        failure,
        errors,
        measured,
        audit,
        file,
      });
      await context.close();
    }
  }
  await browser.close();

  let bad = 0;
  const totals = { routes: 0, contrast: 0, targets: 0, headings: 0, overflow: 0 };
  const byViewport = new Map();

  for (const r of results) {
    const problems = [];
    if (r.failure) problems.push(`navigation failed: ${r.failure}`);
    if (r.status && r.status >= 400) problems.push(`status ${r.status}`);
    if (r.errors.length) problems.push(`${r.errors.length} console error(s)`);
    if (r.measured && r.measured.overflow > 1) {
      problems.push(`horizontal overflow ${r.measured.overflow}px`);
    }
    if (r.measured && r.measured.textLength < 40) problems.push('page is visually empty');

    const v = byViewport.get(r.viewport) || { routes: 0, contrast: 0, targets: 0, headings: 0, overflow: 0 };
    v.routes += 1;
    totals.routes += 1;
    if (r.measured && r.measured.overflow > 1) {
      v.overflow += 1;
      totals.overflow += 1;
    }
    if (r.audit) {
      if (r.audit.contrast.count) {
        problems.push(`${r.audit.contrast.count} contrast failure(s)`);
        v.contrast += r.audit.contrast.count;
        totals.contrast += r.audit.contrast.count;
      }
      if (r.audit.targets.count) {
        problems.push(`${r.audit.targets.count} target(s) under ${MIN_TARGET}x${MIN_TARGET}px`);
        v.targets += r.audit.targets.count;
        totals.targets += r.audit.targets.count;
      }
      if (r.audit.headings.count !== 1) {
        problems.push(`heading structure: ${r.audit.headings.count} h1 element(s)`);
        v.headings += 1;
        totals.headings += 1;
      }
    }
    byViewport.set(r.viewport, v);

    const ok = problems.length === 0;
    if (!ok) bad += 1;

    console.log(`  ${ok ? 'OK  ' : 'FAIL'} [${r.viewport}] ${r.route}`);
    console.log(
      `       status=${r.status} text=${r.measured ? r.measured.textLength : '?'} ` +
        `overflow=${r.measured ? r.measured.overflow : '?'}px shot=${path.basename(r.file)}`,
    );
    problems.forEach((p) => console.log(`       problem: ${p}`));
    r.errors.slice(0, 3).forEach((e) => console.log(`       console: ${e}`));
    if (r.measured && r.measured.offenders.length) {
      r.measured.offenders.forEach((o) =>
        console.log(`       overflow: <${o.tag} class="${o.cls}"> width=${o.width} right=${o.right}`),
      );
    }
    if (r.audit) {
      r.audit.contrast.items.forEach((c) =>
        console.log(
          `       contrast: "${c.text}" ratio=${c.ratio}:1 required=${c.required}:1 ` +
            `size=${c.size}px${c.bold ? ' bold' : ''} color=${c.color} bg=${c.background}`,
        ),
      );
      r.audit.targets.items.forEach((t) =>
        console.log(`       target: <${t.tag}> "${t.label}" ${t.width}x${t.height}`),
      );
      if (r.audit.headings.count !== 1) {
        console.log(
          `       heading: ${r.audit.headings.count} h1 element(s)` +
            (r.audit.headings.items.length ? ` [${r.audit.headings.items.join(' | ')}]` : ''),
        );
      }
    }
    console.log('');
  }

  console.log(`  screenshots: ${outDir}`);
  if (args.audit) {
    console.log('  totals (routes / contrast / small-target / heading / overflow):');
    for (const [name, t] of byViewport) {
      console.log(
        `    ${name.padEnd(8)} ${t.routes} / ${t.contrast} / ${t.targets} / ${t.headings} / ${t.overflow}`,
      );
    }
    console.log(
      `    ${'overall'.padEnd(8)} ${totals.routes} / ${totals.contrast} / ${totals.targets} / ` +
        `${totals.headings} / ${totals.overflow}`,
    );
    console.log('');
  }
  if (bad === 0) {
    console.log(`  all ${results.length} page render(s) clean\n`);
  } else {
    console.log(`  ${bad}/${results.length} page render(s) have problems\n`);
  }
  process.exit(bad === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error(`ui-capture failed: ${e.message}`);
  process.exit(2);
});
