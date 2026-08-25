const { chromium } = require('playwright');
// Hosts are configurable: WF_WEB_URL / WF_API_URL (defaults suit the dev stack).
const BASE = process.env.WF_WEB_URL || 'https://localhost:3000';
const API = process.env.WF_API_URL || 'https://localhost:8000';


(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror ' + page.url() + ': ' + e.message.slice(0, 120)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push('console ' + page.url() + ': ' + m.text().slice(0, 120)); });
  page.on('response', (r) => { if (r.status() >= 400 && !r.url().includes('_next')) errors.push('http' + r.status() + ' ' + r.url().slice(0, 130)); });

  // login
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' });
  await page.locator('input[type="email"]').first().fill('demo@wildframe.com');
  await page.locator('input[type="password"]').first().fill('DemoPass123!');
  await page.locator('button[type="submit"]').first().click();
  await page.waitForURL(/browse/, { timeout: 30000 });
  await page.waitForTimeout(3000);

  const pages = ['/', '/browse', '/my-list', '/account', '/billing', '/creator',
    '/admin', '/admin/users', '/admin/flags', '/admin/alerts', '/admin/audit', '/admin/config', '/signup'];
  for (const p of pages) {
    try {
      await page.goto(BASE + p, { waitUntil: 'networkidle', timeout: 45000 });
    } catch { /* networkidle may time out on polling pages */ }
    await page.waitForTimeout(2500);
    const name = p === '/' ? 'root' : p.replaceAll('/', '_');
    await page.screenshot({ path: `/tmp/opencode/pages${name}.png` });
    console.log('shot', p, '->', page.url());
  }

  // a watch page: grab first movie id from browse API
  const token = await page.evaluate(() => localStorage.getItem('wf_access') || localStorage.getItem('access_token') || '');
  let watchId = null;
  try {
    const res = await page.request.get('process.env.WF_API_URL || 'https://localhost:8000'/content/api/v1/content?content_type=movie&page_size=1',
      { headers: token ? { Authorization: 'Bearer ' + token } : {} });
    const j = await res.json();
    const item = j.items?.[0] || j.data?.[0] || j[0];
    watchId = item?.id;
  } catch {}
  console.log('watchId:', watchId);
  if (watchId) {
    await page.goto(BASE + '/watch/' + watchId, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(6000);
    await page.screenshot({ path: '/tmp/opencode/pages_watch.png' });
    console.log('shot /watch');
  }
  console.log('--- ERRORS (' + errors.length + ') ---');
  console.log([...new Set(errors)].slice(0, 25).join('\n') || 'none');
  await browser.close();
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
