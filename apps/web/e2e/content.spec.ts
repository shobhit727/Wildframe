import { test, expect } from '@playwright/test';

test.describe('Content Playback (public access)', () => {
  test('should load content library page', async ({ page }) => {
    await page.goto('/browse');
    // Page loads (might show sign-in prompt or content)
    await expect(page.getByRole('heading', { level: 2, name: 'Trending Now' })).toBeVisible();
  });

  test('should load content detail page', async ({ page }) => {
    await page.goto('/watch/1');
    // The real content detail surface is /watch/[id].
    await expect(page).toHaveTitle(/Wildframe/i);
  });

  test('should load search page', async ({ page }) => {
    await page.goto('/browse');
    // Search is part of the browse surface; there is no /search page.
    await expect(page.getByRole('heading', { level: 2, name: /Trending Now|Results for/i })).toBeVisible();
  });
});
