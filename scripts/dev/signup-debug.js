const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  page.on('request', r => { if (r.method() === 'POST') console.log('>>', r.url().slice(0, 100)); });
  page.on('response', async r => {
    if (r.request().method() === 'POST' || r.status() >= 400) {
      let body = '';
      try { body = (await r.text()).slice(0, 200); } catch {}
      console.log('<<', r.status(), r.url().slice(0, 90), '|', body.replace(/\n/g, ' '));
    }
  });
  page.on('pageerror', e => console.log('PAGEERROR:', e.message.slice(0, 150)));

  const email = `dbg${Date.now()}@wildframe.com`;
  await page.goto('https://localhost:3000/signup', { waitUntil: 'networkidle' });
  // enumerate inputs in DOM order
  const fields = await page.evaluate(() =>
    Array.from(document.querySelectorAll('input')).map(i => ({ id: i.id, name: i.name, type: i.type, placeholder: i.placeholder }))
  );
  console.log('inputs:', JSON.stringify(fields));
  await page.locator('input#firstName, input[name="firstName"]').first().fill('Debug');
  await page.locator('input#lastName, input[name="lastName"]').first().fill('User');
  await page.locator('input#email, input[name="email"], input[type="email"]').first().fill(email);
  await page.locator('input[type="password"]').nth(0).fill('correct-horse-battery-staple-42!');
  const pwCount = await page.locator('input[type="password"]').count();
  if (pwCount > 1) await page.locator('input[type="password"]').nth(1).fill('correct-horse-battery-staple-42!');
  await page.locator('button[type="submit"]').first().click();
  await page.waitForTimeout(6000);
  console.log('final url:', page.url());
  const alert = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  console.log('alerts:', alert);
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
