import { test, expect } from '@playwright/test';

test.describe('Subscription Page (public access)', () => {
  test('should load subscription page', async ({ page }) => {
    await page.goto('/subscription');
    // Page loads (might show sign-in prompt or subscription plans)
    await expect(page.locator('h1')).toBeVisible();
  });
});
