import { test, expect } from '@playwright/test';

test.describe('Content Playback (public access)', () => {
  test('should load content library page', async ({ page }) => {
    await page.goto('/browse');
    // Page loads (might show sign-in prompt or content)
    await expect(page.locator('h1')).toBeVisible();
  });

  test('should load content detail page', async ({ page }) => {
    await page.goto('/content/1');
    // Page loads (might show sign-in prompt or content)
    await expect(page.locator('h1')).toBeVisible();
  });

  test('should load search page', async ({ page }) => {
    await page.goto('/search');
    // Page loads (might show sign-in prompt or search UI)
    await expect(page.locator('h1')).toBeVisible();
  });
});
