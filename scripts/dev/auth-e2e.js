const { chromium } = require('playwright');

async function login(page, host, email, pass) {
  await page.goto(`https://${host}:3000/login`, { waitUntil: 'networkidle' });
  await page.locator('input[type="email"]').first().fill(email);
  await page.locator('input[type="password"]').first().fill(pass);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForURL(/browse/, { timeout: 25000 });
}

(async () => {
  const browser = await chromium.launch();
  const results = [];

  // 1. signup with a strong generated passphrase (via LAN host)
  {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    const apiErrors = [];
    page.on('response', r => { if (r.status() >= 400) apiErrors.push(r.status() + ' ' + r.url().slice(0, 90)); });
    const email = `strong${Date.now()}@wildframe.com`;
    const pw = 'correct-horse-battery-staple-42!';
    await page.goto('process.env.WF_WEB_URL || 'https://localhost:3000'/signup', { waitUntil: 'networkidle' });
    // fill whatever fields exist
    const textInputs = page.locator('input[type="text"], input[type="email"], input:not([type])');
    const n = await textInputs.count();
    if (n >= 3) {
      await textInputs.nth(0).fill('Strong');
      await textInputs.nth(1).fill('Passphrase');
      await textInputs.nth(2).fill(email);
    } else if (n === 1) {
      await textInputs.nth(0).fill(email);
    }
    await page.locator('input[type="password"]').first().fill(pw);
    await Promise.all([
      page.waitForURL(/browse|login/, { timeout: 25000 }).catch(() => {}),
      page.locator('button[type="submit"]').first().click(),
    ]);
    await page.waitForTimeout(2500);
    results.push(`signup(${email}): url=${page.url()} errors=${apiErrors.length ? apiErrors.join(' ; ') : 'none'}`);
    await ctx.close();
  }

  // 2. login via LAN IP
  {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    try {
      await login(page, new URL(process.env.WF_WEB_URL || 'https://localhost:3000').hostname, 'demo@wildframe.com', 'DemoPass123!');
      results.push('login via LAN host: OK -> ' + page.url());
    } catch (e) {
      results.push('login via LAN host: FAIL at ' + page.url());
    }
    await ctx.close();
  }

  // 3. login via localhost
  {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    try {
      await login(page, 'localhost', 'demo@wildframe.com', 'DemoPass123!');
      results.push('login via localhost: OK -> ' + page.url());
    } catch (e) {
      results.push('login via localhost: FAIL at ' + page.url());
    }
    await ctx.close();
  }

  console.log(results.join('\n'));
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
