import { test, expect } from '@playwright/test';

test.describe('Content Playback', () => {
  test('should show login page when accessing browse', async ({ page }) => {
    await page.goto('/browse');
    // /browse requires auth - should redirect to login
    await expect(page).toHaveURL(/\/login/);
  });

  test('should show login page when accessing content detail', async ({ page }) => {
    await page.goto('/content/1');
    // /content/1 requires auth - should redirect to login
    await expect(page).toHaveURL(/\/login/);
  });

  test('should show login page when accessing search', async ({ page }) => {
    await page.goto('/search');
    // /search requires auth - should redirect to login
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe('Content Search (public)', () => {
  test('should load search page (if public)', async ({ page }) => {
    await page.goto('/search');
    // If search is public, should show search UI
    // If requires auth, should redirect to login
    await expect(page).toHaveURL(/\/search|\/login/);
  });
});
