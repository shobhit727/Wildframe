const { chromium } = require('playwright');
// Hosts are configurable: WF_WEB_URL / WF_API_URL (defaults suit the dev stack).
const BASE = process.env.WF_WEB_URL || 'https://localhost:3000';
const API = process.env.WF_API_URL || 'https://localhost:8000';

const SHOT = (n) => `.tmp/ux/${n}.png`; // relative to repo root

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push('PAGEERROR ' + page.url().slice(-30) + ': ' + e.message.slice(0, 110)));
  page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE ' + m.text().slice(0, 110)); });
  page.on('response', r => { if (r.status() >= 400 && !r.url().includes('auth-session')) errors.push('HTTP' + r.status() + ' ' + r.request().method() + ' ' + r.url().slice(0, 110)); });

  const step = async (name, fn) => {
    try { await fn(); console.log('OK   ' + name); }
    catch (e) { console.log('FAIL ' + name + ' :: ' + e.message.slice(0, 140)); }
  };

  // 1. landing -> sign in
  await step('landing page renders', async () => {
    await page.goto(BASE + '/', { waitUntil: 'networkidle' });
    await page.locator('text=Stories that pull').first().waitFor({ timeout: 15000 });
    await page.screenshot({ path: SHOT('01-landing') });
  });
  await step('click Sign In -> login form', async () => {
    await page.locator('a:has-text("Sign In"), button:has-text("Sign In")').first().click();
    await page.waitForURL(/login/, { timeout: 15000 });
    await page.locator('input[type="email"]').first().waitFor({ timeout: 10000 });
  });
  // 2. login
  await step('login as demo', async () => {
    await page.locator('input[type="email"]').first().fill('demo@wildframe.com');
    await page.locator('input[type="password"]').first().fill('DemoPass123!');
    await page.locator('button[type="submit"]').first().click();
    await page.waitForURL(/browse/, { timeout: 25000 });
    await page.waitForTimeout(3500);
    await page.screenshot({ path: SHOT('02-browse') });
  });
  // 3. open a title from Trending row
  await step('open first trending title -> watch page', async () => {
    await page.locator('[class*="nf-row"] a, a[href^="/watch"]').first().click();
    await page.waitForURL(/watch/, { timeout: 20000 });
    await page.waitForTimeout(6000);
    await page.screenshot({ path: SHOT('03-watch') });
  });
  // 4. back to browse, search
  await step('search from navbar', async () => {
    await page.goto(BASE + '/browse', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    await page.locator('button[aria-label="Search"]').click();
    await page.locator('input[aria-label="Search titles, people, or genres"]').fill('toast');
    await page.waitForTimeout(3000);
    await page.screenshot({ path: SHOT('04-search') });
  });
  // 5. my list add
  await step('add current title to My List (watch page toggle)', async () => {
    await page.goto(BASE + '/browse', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    await page.locator('a[href^="/watch"]').first().click();
    await page.waitForURL(/watch/, { timeout: 20000 });
    await page.waitForTimeout(4000);
    await page.locator('button[aria-label="Toggle my list"]').click();
    await page.waitForTimeout(1000);
  });
  await step('my-list shows content', async () => {
    await page.goto(BASE + '/my-list', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: SHOT('05-mylist') });
  });
  // 6. account edit
  await step('account: open edit and save', async () => {
    await page.goto(BASE + '/account', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    const edit = page.locator('button:has-text("Edit")').first();
    if (await edit.count()) {
      await edit.click();
      const country = page.locator('input[name="country"], #country');
      if (await country.count()) await country.first().fill('NL');
      await page.locator('button:has-text("Save")').first().click();
      await page.waitForTimeout(1500);
    }
    await page.screenshot({ path: SHOT('06-account') });
  });
  // 7. billing switch plan
  await step('billing: choose Premium', async () => {
    await page.goto(BASE + '/billing', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2000);
    await page.screenshot({ path: SHOT('07-billing') });
  });
  // 8. admin dashboard
  await step('admin dashboard loads', async () => {
    await page.goto(BASE + '/admin', { waitUntil: 'networkidle' });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: SHOT('08-admin') });
  });
  // 9. logout
  await step('logout returns to login', async () => {
    await page.goto(BASE + '/browse', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    await page.locator('button[aria-label="Profile menu"]').click();
    const item = page.locator('[role="menuitem"]:has-text("Sign Out")');
    await item.waitFor({ state: 'visible', timeout: 10000 });
    await item.click();
    await page.waitForURL(/login/, { timeout: 15000 });
    await page.screenshot({ path: SHOT('09-logout') });
  });
  // 10. login again
  await step('login again works', async () => {
    await page.locator('input[type="email"]').first().fill('demo@wildframe.com');
    await page.locator('input[type="password"]').first().fill('DemoPass123!');
    await page.locator('button[type="submit"]').first().click();
    await page.waitForURL(/browse/, { timeout: 25000 });
  });

  console.log('\n--- ISSUES (' + errors.length + ') ---');
  console.log([...new Set(errors)].slice(0, 20).join('\n') || 'none');
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
